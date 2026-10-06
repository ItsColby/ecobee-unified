"""State subscriptions, normalization, repairs, and command routing."""

from __future__ import annotations

from asyncio import Event as AsyncEvent
from asyncio import Lock
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfTemperature,
)
from homeassistant.core import (
    Event,
    EventStateReportedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .commands import CommandTracker
from .const import (
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_RELOAD_SILENT_TEMPERATURE_SOURCE,
    DEFAULT_CONFIRMATION_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DEFAULT_RELOAD_SILENT_TEMPERATURE_SOURCE,
    DOMAIN,
    HOMEKIT_PAIR_SETTLE_SECONDS,
    HOMEKIT_SOURCE_DOMAIN,
    SIGNAL_SNAPSHOT_UPDATED,
    SILENT_TEMPERATURE_RELOAD_COOLDOWN_SECONDS,
    SUFFIX_AIR_QUALITY_INDEX,
    SUFFIX_CO2,
    SUFFIX_EQUIPMENT_STAGE,
    SUFFIX_MINIMUM_FAN_RUNTIME,
    SUFFIX_NOTIFICATION,
    SUFFIX_RESUME_PROGRAM,
    SUFFIX_SOURCE_DEGRADED,
    SUFFIX_VOC,
)
from .manager_commands import CommandRunner
from .manager_sources import _state_age_seconds
from .models import (
    CommandSummary,
    MappingConfig,
    NormalizedSnapshot,
    RawSource,
    SourceHealth,
    build_snapshot,
    finite_number,
    homekit_temperature_agrees,
)
from .source_contracts import (
    PhysicalIdentityStatus,
    homekit_action_contract_valid,
    physical_identity_status,
)
from .temperature_quality import (
    SourceIdentity,
    TemperatureObservation,
    TemperatureRecovery,
    TemperatureSilence,
)

# Core 2026.8's HomeKit writer honors the accessory's native granularity even
# though its climate adapter omits target_temp_step. Physical identity remains a
# separate per-mapping proof that is reevaluated through registry events.
HOMEKIT_WRITER_GRANULARITY_PROVEN = True


class MappingManager(CommandRunner):
    """Own all state interpretation for one Ecobee Unified config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        mappings: tuple[MappingConfig, ...],
        options: Mapping[str, Any],
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.mappings = mappings
        self._mapping_by_id = {item.mapping_id: item for item in mappings}
        self._snapshots: dict[str, NormalizedSnapshot] = {}
        self._tracker = CommandTracker()
        self._command_locks = {mapping.mapping_id: Lock() for mapping in self.mappings}
        self._stopped = AsyncEvent()
        self._options = options
        self._unsub_state: Callable[[], None] | None = None
        self._unsub_state_report: Callable[[], None] | None = None
        self._unsub_registry: Callable[[], None] | None = None
        self._unsub_device_registry: Callable[[], None] | None = None
        self._unsub_timeouts: dict[str, Callable[[], None]] = {}
        self._unsub_stale_refreshes: dict[str, Callable[[], None]] = {}
        self._unsub_homekit_settles: dict[str, Callable[[], None]] = {}
        self._homekit_settle_report_times: dict[str, dict[str, datetime]] = {}
        self._temperature_recovery: dict[str, TemperatureRecovery] = {}
        self._temperature_mismatch_candidates: dict[str, TemperatureObservation] = {}
        self._unsub_temperature_mismatches: dict[str, Callable[[], None]] = {}
        self._temperature_silence: dict[str, TemperatureSilence] = {}
        self._silent_source_reloads: dict[str, datetime] = {}
        self._watched_entity_ids: set[str] = set()
        self._watched_entity_references = {
            reference
            for mapping in mappings
            for reference in _source_references(mapping)
            if reference
        }
        self._watched_device_ids: set[str] = set()

    async def async_start(self) -> None:
        """Start subscriptions and build initial snapshots."""

        self._require_command_admission()
        self._subscribe_states()
        self._unsub_registry = self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_entity_registry_event
        )
        self._unsub_device_registry = self.hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, self._handle_device_registry_event
        )
        self.refresh_all()

    async def async_stop(self) -> None:
        """Close admission and stop callbacks without retrying dispatched effects."""

        # A dispatched source call may already have changed the thermostat. Let its
        # caller receive the result, but do not retain confirmation or publish later.
        self._stopped.set()
        for mapping in self.mappings:
            if (
                revision := self._tracker.pending_revision(mapping.mapping_id)
            ) is not None:
                self._tracker.unconfirm(mapping.mapping_id, revision)
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_state_report:
            self._unsub_state_report()
            self._unsub_state_report = None
        if self._unsub_registry:
            self._unsub_registry()
            self._unsub_registry = None
        if self._unsub_device_registry:
            self._unsub_device_registry()
            self._unsub_device_registry = None
        for unsubscribe in self._unsub_timeouts.values():
            unsubscribe()
        self._unsub_timeouts.clear()
        for unsubscribe in self._unsub_stale_refreshes.values():
            unsubscribe()
        self._unsub_stale_refreshes.clear()
        for unsubscribe in self._unsub_homekit_settles.values():
            unsubscribe()
        self._unsub_homekit_settles.clear()
        self._homekit_settle_report_times.clear()
        for unsubscribe in self._unsub_temperature_mismatches.values():
            unsubscribe()
        self._unsub_temperature_mismatches.clear()
        self._temperature_mismatch_candidates.clear()
        self._temperature_recovery.clear()
        self._temperature_silence.clear()
        self._watched_entity_ids.clear()
        self._watched_device_ids.clear()
        for mapping in self.mappings:
            ir.async_delete_issue(self.hass, DOMAIN, f"mapping_{mapping.mapping_id}")
            ir.async_delete_issue(
                self.hass, DOMAIN, _silent_temperature_issue_id(mapping.mapping_id)
            )

    def snapshot(self, mapping_id: str) -> NormalizedSnapshot:
        """Return the current immutable snapshot."""

        return self._snapshots[mapping_id]

    def diagnostic_source_ages(self, mapping_id: str) -> dict[str, int | None]:
        """Return request-time ages for sources present in the cached snapshot."""

        snapshot = self.snapshot(mapping_id)
        mapping = self._mapping_by_id[mapping_id]
        references = {
            "homekit": mapping.homekit_entity,
            "ecobee": mapping.ecobee_entity,
            "homekit_preset": mapping.homekit_preset_entity,
            "homekit_temperature": mapping.homekit_temperature_entity,
            "air_quality_index": mapping.ecobee_aqi_entity,
            "co2": mapping.ecobee_co2_entity,
            "voc": mapping.ecobee_voc_entity,
        }
        now = dt_util.utcnow()
        ages: dict[str, int | None] = {}
        for source_name, cached_age in snapshot.source_ages.items():
            reference = references[source_name]
            entity_id = self.resolve_entity_id(reference) if reference else None
            state = self.hass.states.get(entity_id) if entity_id else None
            ages[source_name] = (
                _state_age_seconds(state.last_reported, now)
                if cached_age is not None and state is not None
                else None
            )
        return ages

    def diagnostic_command_summary(self, mapping_id: str) -> CommandSummary:
        """Return request-time command age without republishing entity state."""

        return self._tracker.summary(mapping_id)

    def resolve_entity_id(self, entity_reference: str) -> str | None:
        """Resolve a stable entity-registry ID without guessing by name."""

        return er.async_resolve_entity_id(er.async_get(self.hass), entity_reference)

    def refresh_all(self) -> None:
        """Rebuild one snapshot per mapping."""

        for mapping in self.mappings:
            self._cancel_homekit_settle(mapping.mapping_id)
            self.refresh_mapping(mapping.mapping_id)

    def refresh_mapping(
        self,
        mapping_id: str,
        *,
        observation_revision: int | None = None,
        report_times: Mapping[str, datetime] | None = None,
        homekit_pair_settled: bool = False,
        temperature_confirmation: TemperatureObservation | None = None,
    ) -> None:
        """Normalize mapped states once and publish every projection from it."""

        if self._stopped.is_set():
            return
        mapping = self._mapping_by_id[mapping_id]
        now = dt_util.utcnow()
        ecobee_stale_seconds = int(
            self._options.get(CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS)
        )
        homekit_device_id = self._source_device_id(mapping.homekit_entity)
        ecobee_device_id = self._source_device_id(mapping.ecobee_entity)
        identity_status = physical_identity_status(
            self.hass, mapping.homekit_entity, mapping.ecobee_entity
        )
        physical_identity_proven = identity_status is PhysicalIdentityStatus.MATCH
        homekit = self._climate_raw_source(
            mapping.homekit_entity,
            None,
            require_device=True,
            now=now,
            report_times=report_times,
        )
        ecobee_observed = self._climate_raw_source(
            mapping.ecobee_entity,
            ecobee_stale_seconds,
            now=now,
            report_times=report_times,
        )
        ecobee = (
            ecobee_observed
            if physical_identity_proven
            else self._invalid_source(ecobee_observed)
        )
        homekit_preset = self._optional_raw_source(
            mapping.homekit_preset_entity,
            None,
            now=now,
            report_times=report_times,
            required_device_id=homekit_device_id,
        )
        if (
            homekit_preset is not None
            and homekit_preset.health in {SourceHealth.HEALTHY, SourceHealth.UNKNOWN}
            and mapping.homekit_preset_entity is not None
            and not homekit_action_contract_valid(
                self.hass, mapping.homekit_preset_entity, "preset"
            )
        ):
            homekit_preset = self._invalid_source(homekit_preset)
        homekit_temperature = self._temperature_raw_source(
            mapping.homekit_temperature_entity,
            homekit,
            now=now,
            report_times=report_times,
            required_device_id=homekit_device_id,
        )
        temperature_silent = self._temperature_silent(mapping, homekit_temperature, now)
        if temperature_silent:
            assert homekit_temperature is not None
            homekit_temperature = replace(
                homekit_temperature, health=SourceHealth.STALE
            )
        temperature_recovery_pending = self._update_temperature_recovery(
            mapping,
            homekit,
            homekit_temperature,
            pair_settled=homekit_pair_settled,
            confirmation=temperature_confirmation,
            silent=temperature_silent,
        )
        cloud_sensors = tuple(
            self._air_quality_raw_source(
                reference,
                contract_name,
                ecobee_stale_seconds,
                now=now,
                report_times=report_times,
                required_device_id=ecobee_device_id,
                physical_identity_proven=physical_identity_proven,
            )
            for reference, contract_name in (
                (mapping.ecobee_aqi_entity, "aqi"),
                (mapping.ecobee_co2_entity, "co2"),
                (mapping.ecobee_voc_entity, "voc"),
            )
        )
        homekit_clear_hold_writable = self._homekit_action_available(
            mapping, "clear_hold"
        )
        homekit_preset_writable = self._homekit_action_available(mapping, "preset")
        snapshot = build_snapshot(
            mapping_id,
            homekit,
            ecobee,
            homekit_preset=homekit_preset,
            homekit_temperature=homekit_temperature,
            homekit_temperature_recovery_pending=temperature_recovery_pending,
            read_policies=self._options.get("read_policies", {}).get(mapping_id, {}),
            air_quality_index=cloud_sensors[0],
            co2=cloud_sensors[1],
            voc=cloud_sensors[2],
            command=self._tracker.summary(mapping_id),
            homekit_preset_writable=homekit_preset_writable,
            homekit_clear_hold_writable=homekit_clear_hold_writable,
            temperature_step_fusion_proven=(
                physical_identity_proven and HOMEKIT_WRITER_GRANULARITY_PROVEN
            ),
            physical_identity_proven=physical_identity_proven,
            physical_identity_mismatch=(
                identity_status is PhysicalIdentityStatus.MISMATCH
            ),
            ecobee_notify_writable=(
                physical_identity_proven
                and self._writer_available(
                    mapping.ecobee_notify_entity,
                    required_device_id=ecobee_device_id,
                )
            ),
        )
        if observation_revision is not None and self._tracker.observe(
            mapping_id, observation_revision, snapshot
        ):
            self._cancel_timeout(mapping_id)
            self._subscribe_state_reports()
            snapshot = replace(snapshot, command=self._tracker.summary(mapping_id))
        self._snapshots[mapping_id] = snapshot
        stale_inputs = [(ecobee, ecobee_stale_seconds)]
        stale_inputs.extend(
            (source, ecobee_stale_seconds)
            for source in cloud_sensors
            if source is not None
        )
        self._schedule_stale_refresh(mapping_id, stale_inputs)
        self._refresh_mapping_issue(mapping)
        self._handle_temperature_silence(mapping, temperature_silent)
        async_dispatcher_send(self.hass, f"{SIGNAL_SNAPSHOT_UPDATED}_{mapping_id}")

    @callback
    def _handle_state_event(self, event: Event[EventStateChangedData]) -> None:
        if self._stopped.is_set():
            return
        entity_id = event.data["entity_id"]
        for mapping in self.mappings:
            if entity_id not in {
                resolved
                for reference in _source_references(mapping)
                if reference and (resolved := self.resolve_entity_id(reference))
            }:
                continue
            self._record_climate_temperature_change(mapping, entity_id, event)
            operation = self._tracker.pending_operation(mapping.mapping_id)
            expected_reference = self._confirmation_reference(mapping, operation)
            expected_observer = (
                self.resolve_entity_id(expected_reference)
                if expected_reference
                else None
            )
            observation_revision = (
                self._tracker.current_revision(mapping.mapping_id)
                if operation is not None and entity_id == expected_observer
                else None
            )
            new_state = event.data["new_state"]
            report_times = (
                {entity_id: new_state.last_updated} if new_state is not None else None
            )
            if observation_revision is None and self._is_routine_paired_homekit_event(
                mapping, entity_id, event
            ):
                self._schedule_homekit_settle(mapping.mapping_id, report_times)
                continue
            pending_report_times = self._cancel_homekit_settle(mapping.mapping_id)
            merged_report_times = pending_report_times
            if report_times is not None:
                merged_report_times.update(report_times)
            self.refresh_mapping(
                mapping.mapping_id,
                observation_revision=observation_revision,
                report_times=merged_report_times or None,
            )

    def _is_routine_paired_homekit_event(
        self,
        mapping: MappingConfig,
        entity_id: str,
        event: Event[EventStateChangedData],
    ) -> bool:
        """Return whether a healthy paired HomeKit update may briefly settle."""

        if mapping.homekit_temperature_entity is None:
            return False
        paired_entity_ids = {
            resolved
            for reference in (
                mapping.homekit_entity,
                mapping.homekit_temperature_entity,
            )
            if (resolved := self.resolve_entity_id(reference)) is not None
        }
        if entity_id not in paired_entity_ids:
            return False
        old_state = event.data.get("old_state")
        new_state = event.data["new_state"]
        unavailable_states = {"unknown", "unavailable"}
        if (
            old_state is None
            or new_state is None
            or old_state.state in unavailable_states
            or new_state.state in unavailable_states
        ):
            return False
        precise_entity_id = self.resolve_entity_id(mapping.homekit_temperature_entity)
        if entity_id == precise_entity_id:
            return True
        return old_state.attributes.get(
            "current_temperature"
        ) != new_state.attributes.get("current_temperature")

    def _schedule_homekit_settle(
        self,
        mapping_id: str,
        report_times: Mapping[str, datetime] | None,
    ) -> None:
        """Coalesce sequential climate/temperature reports for one mapping."""

        self._cancel_temperature_mismatch(mapping_id)
        if report_times is not None:
            self._homekit_settle_report_times.setdefault(mapping_id, {}).update(
                report_times
            )
        if unsubscribe := self._unsub_homekit_settles.pop(mapping_id, None):
            unsubscribe()
        self._unsub_homekit_settles[mapping_id] = async_call_later(
            self.hass,
            HOMEKIT_PAIR_SETTLE_SECONDS,
            partial(self._handle_homekit_settle, mapping_id),
        )

    @callback
    def _handle_homekit_settle(
        self, mapping_id: str, _now: datetime | None = None
    ) -> None:
        """Publish the final paired HomeKit state after its short settle window."""

        self._unsub_homekit_settles.pop(mapping_id, None)
        report_times = self._homekit_settle_report_times.pop(mapping_id, None)
        self.refresh_mapping(
            mapping_id, report_times=report_times, homekit_pair_settled=True
        )

    def _cancel_homekit_settle(self, mapping_id: str) -> dict[str, datetime]:
        """Cancel one pending settle and return its diagnostic report times."""

        if unsubscribe := self._unsub_homekit_settles.pop(mapping_id, None):
            unsubscribe()
        return self._homekit_settle_report_times.pop(mapping_id, {})

    def _temperature_source_identity(
        self, mapping: MappingConfig
    ) -> SourceIdentity | None:
        """Identify actual registry associations independently of entity names."""

        registry = er.async_get(self.hass)
        entries = [
            registry.async_get(entity_id)
            if reference and (entity_id := self.resolve_entity_id(reference))
            else None
            for reference in (
                mapping.homekit_entity,
                mapping.homekit_temperature_entity,
            )
        ]
        climate, precise = entries
        if (
            climate is None
            or precise is None
            or climate.device_id is None
            or precise.device_id != climate.device_id
            or dr.async_get(self.hass).async_get(climate.device_id) is None
        ):
            return None
        return climate.id, climate.device_id, precise.id, precise.device_id

    def _update_temperature_recovery(
        self,
        mapping: MappingConfig,
        homekit: RawSource,
        precise: RawSource | None,
        *,
        pair_settled: bool,
        confirmation: TemperatureObservation | None,
        silent: bool = False,
    ) -> bool:
        """Keep confirmed rejected values out until this source changes and agrees."""

        mapping_id = mapping.mapping_id
        if mapping.homekit_temperature_entity is None:
            self._temperature_recovery.pop(mapping_id, None)
            self._cancel_temperature_mismatch(mapping_id)
            return False
        if silent:
            # Silence already excludes the sensor and names the cause. Keep any
            # rejected value: a re-report of it must still not recover precision.
            self._cancel_temperature_mismatch(mapping_id)
            return False
        identity = self._temperature_source_identity(mapping)
        agrees = homekit_temperature_agrees(precise, homekit)
        observation = None
        if identity is not None and agrees is not None and precise is not None:
            assert precise.state is not None
            observation = TemperatureObservation(
                identity,
                TemperatureConverter.convert(
                    float(precise.state),
                    UnitOfTemperature(homekit.attributes[ATTR_UNIT_OF_MEASUREMENT]),
                    UnitOfTemperature.CELSIUS,
                ),
            )
        confirmed = pair_settled or (
            confirmation is not None
            and observation is not None
            and observation.matches(confirmation)
        )
        recovery = self._temperature_recovery.setdefault(
            mapping_id, TemperatureRecovery()
        )
        pending = recovery.observe(identity, observation, agrees, confirmed=confirmed)
        already_rejected = (
            observation is not None
            and recovery.rejected is not None
            and observation.matches(recovery.rejected)
        )
        if agrees is not False or observation is None or confirmed or already_rejected:
            self._cancel_temperature_mismatch(mapping_id)
        else:
            candidate = self._temperature_mismatch_candidates.get(mapping_id)
            if candidate is None or not observation.matches(candidate):
                self._cancel_temperature_mismatch(mapping_id)
                self._temperature_mismatch_candidates[mapping_id] = observation
                self._unsub_temperature_mismatches[mapping_id] = async_call_later(
                    self.hass,
                    HOMEKIT_PAIR_SETTLE_SECONDS,
                    partial(self._handle_temperature_mismatch, mapping_id, observation),
                )
        return pending

    @callback
    def _handle_temperature_mismatch(
        self,
        mapping_id: str,
        observation: TemperatureObservation,
        _now: datetime | None = None,
    ) -> None:
        """Confirm only the still-current candidate after its bounded interval."""

        if self._temperature_mismatch_candidates.get(mapping_id) != observation:
            return
        self._unsub_temperature_mismatches.pop(mapping_id, None)
        self._temperature_mismatch_candidates.pop(mapping_id, None)
        self.refresh_mapping(mapping_id, temperature_confirmation=observation)

    def _cancel_temperature_mismatch(self, mapping_id: str) -> None:
        """Cancel confirmation without forgetting previously rejected evidence."""

        if unsubscribe := self._unsub_temperature_mismatches.pop(mapping_id, None):
            unsubscribe()
        self._temperature_mismatch_candidates.pop(mapping_id, None)

    def _record_climate_temperature_change(
        self,
        mapping: MappingConfig,
        entity_id: str,
        event: Event[EventStateChangedData],
    ) -> None:
        """Remember a rounded climate change that the precise sensor must answer."""

        if (
            mapping.homekit_temperature_entity is None
            or entity_id != self.resolve_entity_id(mapping.homekit_entity)
        ):
            return
        old_state = event.data.get("old_state")
        new_state = event.data["new_state"]
        if old_state is None or new_state is None:
            return
        before = finite_number(old_state.attributes.get("current_temperature"))
        after = finite_number(new_state.attributes.get("current_temperature"))
        if before is None or after is None or before == after:
            return
        self._temperature_silence.setdefault(
            mapping.mapping_id, TemperatureSilence()
        ).climate_changed(
            self._temperature_source_identity(mapping), new_state.last_updated
        )

    def _temperature_silent(
        self, mapping: MappingConfig, precise: RawSource | None, now: datetime
    ) -> bool:
        """Return whether a precise sensor's value stopped following the climate."""

        silence = self._temperature_silence.get(mapping.mapping_id)
        if (
            silence is None
            or precise is None
            or not precise.usable
            or mapping.homekit_temperature_entity is None
        ):
            return False
        entity_id = self.resolve_entity_id(mapping.homekit_temperature_entity)
        state = self.hass.states.get(entity_id) if entity_id else None
        return silence.silent(
            self._temperature_source_identity(mapping),
            # A re-report of an unchanged value is not an answer.
            state.last_changed if state is not None else None,
            now,
        )

    def _temperature_source_entry_id(self, mapping: MappingConfig) -> str | None:
        """Return the HomeKit entry that owns both paired temperature entities."""

        if mapping.homekit_temperature_entity is None:
            return None
        registry = er.async_get(self.hass)
        entries = [
            registry.async_get(entity_id)
            if (entity_id := self.resolve_entity_id(reference))
            else None
            for reference in (
                mapping.homekit_entity,
                mapping.homekit_temperature_entity,
            )
        ]
        climate, precise = entries
        if (
            climate is None
            or precise is None
            or climate.platform != HOMEKIT_SOURCE_DOMAIN
            or precise.platform != HOMEKIT_SOURCE_DOMAIN
            or climate.config_entry_id is None
            or climate.config_entry_id != precise.config_entry_id
        ):
            return None
        source_entry = self.hass.config_entries.async_get_entry(climate.config_entry_id)
        if source_entry is None or source_entry.domain != HOMEKIT_SOURCE_DOMAIN:
            return None
        return source_entry.entry_id

    def _handle_temperature_silence(self, mapping: MappingConfig, silent: bool) -> None:
        """Reload the silent sensor's source once per cooldown, else raise a Repair."""

        issue_id = _silent_temperature_issue_id(mapping.mapping_id)
        if not silent:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        source_entry_id = self._temperature_source_entry_id(mapping)
        if (
            source_entry_id is not None
            and self._options.get(
                CONF_RELOAD_SILENT_TEMPERATURE_SOURCE,
                DEFAULT_RELOAD_SILENT_TEMPERATURE_SOURCE,
            )
            is True
            and self._tracker.pending_operation(mapping.mapping_id) is None
        ):
            now = dt_util.utcnow()
            last_reload = self._silent_source_reloads.get(source_entry_id)
            if (
                last_reload is None
                or (now - last_reload).total_seconds()
                >= SILENT_TEMPERATURE_RELOAD_COOLDOWN_SECONDS
            ):
                self._silent_source_reloads[source_entry_id] = now
                # Evidence gathered before the reload cannot describe the
                # reloaded entity; a still-silent sensor must be caught anew.
                for other in self.mappings:
                    silence = self._temperature_silence.get(other.mapping_id)
                    if (
                        silence is not None
                        and self._temperature_source_entry_id(other) == source_entry_id
                    ):
                        silence.reset()
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                self.hass.config_entries.async_schedule_reload(source_entry_id)
                return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=source_entry_id is not None,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=(
                "homekit_temperature_silent"
                if source_entry_id is not None
                else "homekit_temperature_silent_manual"
            ),
            translation_placeholders={"mapping": mapping.name},
            data=(
                {"source_entry_id": source_entry_id}
                if source_entry_id is not None
                else None
            ),
        )

    @callback
    def _handle_state_report_event(self, event: Event[EventStateReportedData]) -> None:
        """Observe command confirmation or recovery from cadence-backed staleness."""

        entity_id = event.data["entity_id"]
        for mapping in self.mappings:
            operation = self._tracker.pending_operation(mapping.mapping_id)
            expected_reference = self._confirmation_reference(mapping, operation)
            expected_observer = (
                self.resolve_entity_id(expected_reference)
                if expected_reference
                else None
            )
            revision = (
                self._tracker.pending_revision(mapping.mapping_id)
                if operation is not None and entity_id == expected_observer
                else None
            )
            stale_recovery = any(
                entity_id == self.resolve_entity_id(reference)
                and self.snapshot(mapping.mapping_id).source_health.get(health_key)
                is SourceHealth.STALE
                for reference, health_key in (
                    (mapping.ecobee_entity, "ecobee"),
                    (mapping.ecobee_aqi_entity, "air_quality_index"),
                    (mapping.ecobee_co2_entity, "co2"),
                    (mapping.ecobee_voc_entity, "voc"),
                )
                if reference is not None
            )
            if revision is None and not stale_recovery:
                continue
            self.refresh_mapping(
                mapping.mapping_id,
                observation_revision=revision,
                report_times={entity_id: event.data["last_reported"]},
            )

    @callback
    def _handle_entity_registry_event(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        if self._stopped.is_set():
            return
        affected = {event.data["entity_id"]}
        old_entity_id = event.data.get("old_entity_id")
        if isinstance(old_entity_id, str):
            affected.add(old_entity_id)
        registry = er.async_get(self.hass)
        source_event = bool(affected.intersection(self._watched_entity_ids)) or any(
            (registry_entry := registry.async_get(entity_id)) is not None
            and registry_entry.id in self._watched_entity_references
            for entity_id in affected
        )
        if source_event:
            self._subscribe_states()
            self.refresh_all()
            self._sync_helper_device_links()
            return
        if any(
            (registry_entry := registry.async_get(entity_id)) is not None
            and registry_entry.platform == DOMAIN
            and registry_entry.config_entry_id == self.entry_id
            for entity_id in affected
        ):
            self._sync_helper_device_links()

    @callback
    def _handle_device_registry_event(
        self, event: Event[dr.EventDeviceRegistryUpdatedData]
    ) -> None:
        if self._stopped.is_set():
            return
        if event.data["device_id"] not in self._watched_device_ids:
            return
        self._subscribe_states()
        self.refresh_all()
        self._sync_helper_device_links()

    @callback
    def _handle_timeout(
        self, mapping_id: str, revision: int, _now: datetime | None = None
    ) -> None:
        self._unsub_timeouts.pop(mapping_id, None)
        if self._tracker.timeout(mapping_id, revision):
            self._subscribe_state_reports()
            self.refresh_mapping(mapping_id)

    def _replace_timeout(self, mapping_id: str, revision: int) -> None:
        self._cancel_timeout(mapping_id)
        seconds = int(
            self._options.get(CONF_CONFIRMATION_SECONDS, DEFAULT_CONFIRMATION_SECONDS)
        )
        self._unsub_timeouts[mapping_id] = async_call_later(
            self.hass,
            seconds,
            partial(self._handle_timeout, mapping_id, revision),
        )

    def _cancel_timeout(self, mapping_id: str) -> None:
        if unsubscribe := self._unsub_timeouts.pop(mapping_id, None):
            unsubscribe()

    @callback
    def _handle_stale_refresh(
        self, mapping_id: str, _now: datetime | None = None
    ) -> None:
        self._unsub_stale_refreshes.pop(mapping_id, None)
        self.refresh_mapping(mapping_id)

    def _schedule_stale_refresh(
        self,
        mapping_id: str,
        sources: list[tuple[RawSource, int]],
    ) -> None:
        if unsubscribe := self._unsub_stale_refreshes.pop(mapping_id, None):
            unsubscribe()
        delays = [
            max(1, stale_seconds - source.age_seconds + 1)
            for source, stale_seconds in sources
            if source.health is SourceHealth.HEALTHY and source.age_seconds is not None
        ]
        if delays:
            self._unsub_stale_refreshes[mapping_id] = async_call_later(
                self.hass,
                min(delays),
                partial(self._handle_stale_refresh, mapping_id),
            )

    def _subscribe_states(self) -> None:
        if self._unsub_state:
            self._unsub_state()
        entity_ids = {
            resolved
            for mapping in self.mappings
            for reference in _source_references(mapping)
            if reference and (resolved := self.resolve_entity_id(reference))
        }
        self._watched_entity_ids = entity_ids
        registry = er.async_get(self.hass)
        self._watched_device_ids = {
            registry_entry.device_id
            for entity_id in entity_ids
            if (registry_entry := registry.async_get(entity_id)) is not None
            and registry_entry.device_id is not None
        }
        self._unsub_state = (
            async_track_state_change_event(
                self.hass, entity_ids, self._handle_state_event
            )
            if entity_ids
            else None
        )
        self._subscribe_state_reports()

    def _subscribe_state_reports(self) -> None:
        if self._unsub_state_report:
            self._unsub_state_report()
            self._unsub_state_report = None
        observed_entity_ids: set[str] = set()
        for mapping in self.mappings:
            observed_entity_ids.update(
                resolved
                for reference in (
                    mapping.ecobee_entity,
                    mapping.ecobee_aqi_entity,
                    mapping.ecobee_co2_entity,
                    mapping.ecobee_voc_entity,
                )
                if reference and (resolved := self.resolve_entity_id(reference))
            )
            operation = self._tracker.pending_operation(mapping.mapping_id)
            if operation is None:
                continue
            reference = self._confirmation_reference(mapping, operation)
            if (
                reference == mapping.ecobee_entity
                and physical_identity_status(
                    self.hass, mapping.homekit_entity, mapping.ecobee_entity
                )
                is not PhysicalIdentityStatus.MATCH
            ):
                continue
            if reference and (resolved := self.resolve_entity_id(reference)):
                observed_entity_ids.add(resolved)
        self._unsub_state_report = (
            async_track_state_report_event(
                self.hass, observed_entity_ids, self._handle_state_report_event
            )
            if observed_entity_ids
            else None
        )

    def _sync_helper_device_links(self) -> None:
        """Relink unified entities when their HomeKit source device changes."""

        registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        mapping_by_unique_id = {
            unique_id: mapping
            for mapping in self.mappings
            for unique_id in (
                mapping.mapping_id,
                f"{mapping.mapping_id}_{SUFFIX_RESUME_PROGRAM}",
                f"{mapping.mapping_id}_{SUFFIX_SOURCE_DEGRADED}",
                f"{mapping.mapping_id}_{SUFFIX_MINIMUM_FAN_RUNTIME}",
                f"{mapping.mapping_id}_{SUFFIX_NOTIFICATION}",
                f"{mapping.mapping_id}_{SUFFIX_EQUIPMENT_STAGE}",
                f"{mapping.mapping_id}_{SUFFIX_AIR_QUALITY_INDEX}",
                f"{mapping.mapping_id}_{SUFFIX_CO2}",
                f"{mapping.mapping_id}_{SUFFIX_VOC}",
            )
        }
        for helper_entry in er.async_entries_for_config_entry(registry, self.entry_id):
            mapping = mapping_by_unique_id.get(helper_entry.unique_id)
            if mapping is None or helper_entry.config_entry_id != self.entry_id:
                continue
            source_entity_id = self.resolve_entity_id(mapping.homekit_entity)
            source_entry = (
                registry.async_get(source_entity_id) if source_entity_id else None
            )
            source_device_id = (
                source_entry.device_id
                if source_entry is not None
                and source_entry.device_id is not None
                and device_registry.async_get(source_entry.device_id) is not None
                else None
            )
            if helper_entry.device_id == source_device_id:
                continue
            registry.async_update_entity(
                helper_entry.entity_id, device_id=source_device_id
            )


def _source_references(mapping: MappingConfig) -> tuple[str | None, ...]:
    """Return every mapped source in the shared subscription order."""

    return (
        mapping.homekit_entity,
        mapping.ecobee_entity,
        mapping.homekit_preset_entity,
        mapping.homekit_clear_hold_entity,
        mapping.homekit_temperature_entity,
        mapping.ecobee_aqi_entity,
        mapping.ecobee_co2_entity,
        mapping.ecobee_voc_entity,
        mapping.ecobee_notify_entity,
    )


def _silent_temperature_issue_id(mapping_id: str) -> str:
    return f"homekit_temperature_silent_{mapping_id}"
