"""State subscriptions, normalization, repairs, and command routing."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from homeassistant.core import (
    Context,
    Event,
    EventStateReportedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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

from .commands import CommandTracker
from .const import (
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_STALE_SECONDS,
    DEFAULT_CONFIRMATION_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DOMAIN,
    SIGNAL_SNAPSHOT_UPDATED,
    SUFFIX_AIR_QUALITY_INDEX,
    SUFFIX_CO2,
    SUFFIX_EQUIPMENT_STAGE,
    SUFFIX_MINIMUM_FAN_RUNTIME,
    SUFFIX_VOC,
)
from .models import (
    MappingConfig,
    NormalizedSnapshot,
    RawSource,
    SourceHealth,
    build_snapshot,
)

# An explicit mapping asserts the HomeKit and Ecobee climates represent the same
# physical Ecobee. Core 2026.8's HomeKit writer honors the accessory's native
# granularity even though its climate adapter omits target_temp_step.
TEMPERATURE_STEP_FUSION_PROVEN = True


class MappingManager:
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
        self._options = options
        self._unsub_state: Callable[[], None] | None = None
        self._unsub_state_report: Callable[[], None] | None = None
        self._unsub_registry: Callable[[], None] | None = None
        self._unsub_device_registry: Callable[[], None] | None = None
        self._unsub_timeouts: dict[str, Callable[[], None]] = {}
        self._unsub_stale_refreshes: dict[str, Callable[[], None]] = {}

    async def async_start(self) -> None:
        """Start subscriptions and build initial snapshots."""

        self._subscribe_states()
        self._unsub_registry = self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_event
        )
        self._unsub_device_registry = self.hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, self._handle_registry_event
        )
        self.refresh_all()

    async def async_stop(self) -> None:
        """Stop only subscriptions and timers owned by this manager."""

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
        for mapping in self.mappings:
            ir.async_delete_issue(self.hass, DOMAIN, f"mapping_{mapping.mapping_id}")

    def snapshot(self, mapping_id: str) -> NormalizedSnapshot:
        """Return the current immutable snapshot."""

        return self._snapshots[mapping_id]

    def resolve_entity_id(self, entity_reference: str) -> str | None:
        """Resolve a stable entity-registry ID without guessing by name."""

        return er.async_resolve_entity_id(er.async_get(self.hass), entity_reference)

    def refresh_all(self) -> None:
        """Rebuild one snapshot per mapping."""

        for mapping in self.mappings:
            self.refresh_mapping(mapping.mapping_id)

    def refresh_mapping(
        self,
        mapping_id: str,
        *,
        observation_revision: int | None = None,
        report_times: Mapping[str, datetime] | None = None,
    ) -> None:
        """Normalize mapped states once and publish every projection from it."""

        mapping = self._mapping_by_id[mapping_id]
        now = dt_util.utcnow()
        ecobee_stale_seconds = int(
            self._options.get(CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS)
        )
        homekit_device_id = self._source_device_id(mapping.homekit_entity)
        ecobee_device_id = self._source_device_id(mapping.ecobee_entity)
        homekit = self._raw_source(
            mapping.homekit_entity,
            None,
            require_device=True,
            now=now,
            report_times=report_times,
        )
        ecobee = self._raw_source(
            mapping.ecobee_entity,
            ecobee_stale_seconds,
            now=now,
            report_times=report_times,
        )
        homekit_preset = self._optional_raw_source(
            mapping.homekit_preset_entity,
            None,
            now=now,
            report_times=report_times,
            required_device_id=homekit_device_id,
            require_matching_device=True,
        )
        cloud_sensors = tuple(
            self._optional_raw_source(
                reference,
                ecobee_stale_seconds,
                now=now,
                report_times=report_times,
                required_device_id=ecobee_device_id,
                require_matching_device=True,
            )
            for reference in (
                mapping.ecobee_aqi_entity,
                mapping.ecobee_co2_entity,
                mapping.ecobee_voc_entity,
            )
        )
        snapshot = build_snapshot(
            mapping_id,
            homekit,
            ecobee,
            homekit_preset=homekit_preset,
            air_quality_index=cloud_sensors[0],
            co2=cloud_sensors[1],
            voc=cloud_sensors[2],
            command=self._tracker.summary(mapping_id),
            homekit_clear_hold_writable=self._writer_available(
                mapping.homekit_clear_hold_entity,
                required_device_id=homekit_device_id,
            ),
            temperature_step_fusion_proven=TEMPERATURE_STEP_FUSION_PROVEN,
        )
        if observation_revision is not None and self._tracker.observe(
            mapping_id, observation_revision, snapshot
        ):
            self._cancel_timeout(mapping_id)
            self._subscribe_state_reports()
            snapshot = build_snapshot(
                mapping_id,
                homekit,
                ecobee,
                homekit_preset=homekit_preset,
                air_quality_index=cloud_sensors[0],
                co2=cloud_sensors[1],
                voc=cloud_sensors[2],
                command=self._tracker.summary(mapping_id),
                homekit_clear_hold_writable=self._writer_available(
                    mapping.homekit_clear_hold_entity,
                    required_device_id=homekit_device_id,
                ),
                temperature_step_fusion_proven=TEMPERATURE_STEP_FUSION_PROVEN,
            )
        self._snapshots[mapping_id] = snapshot
        stale_inputs = [(ecobee, ecobee_stale_seconds)]
        stale_inputs.extend(
            (source, ecobee_stale_seconds)
            for source in cloud_sensors
            if source is not None
        )
        self._schedule_stale_refresh(mapping_id, stale_inputs)
        self._refresh_mapping_issue(mapping)
        async_dispatcher_send(self.hass, f"{SIGNAL_SNAPSHOT_UPDATED}_{mapping_id}")

    async def async_standard_command(
        self,
        mapping_id: str,
        service: str,
        service_data: Mapping[str, Any],
        expected: Mapping[str, Any],
        context: Context | None,
    ) -> None:
        """Call exactly one HomeKit writer and observe Ecobee without retry."""

        mapping = self._mapping_by_id[mapping_id]
        snapshot = self.snapshot(mapping_id)
        entity_id = self.resolve_entity_id(mapping.homekit_entity)
        if not snapshot.homekit_writable or entity_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="homekit_writer_unavailable",
            )

        revision = self._tracker.begin(mapping_id, service, expected)
        self._subscribe_state_reports()
        self._replace_timeout(mapping_id, revision)
        self.refresh_mapping(mapping_id)
        try:
            await self.hass.services.async_call(
                "climate",
                service,
                {"entity_id": entity_id, **service_data},
                blocking=True,
                context=context,
            )
        except Exception:  # noqa: BLE001 - source services may raise arbitrary errors
            if self._tracker.fail(mapping_id, revision):
                self._cancel_timeout(mapping_id)
                self._subscribe_state_reports()
                self.refresh_mapping(mapping_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="homekit_command_failed",
            ) from None

    async def async_vendor_command(
        self,
        mapping_id: str,
        service: str,
        service_data: Mapping[str, Any],
        expected: Mapping[str, Any],
        context: Context | None,
    ) -> None:
        """Call exactly one explicit Ecobee action with no fallback."""

        mapping = self._mapping_by_id[mapping_id]
        entity_id = self.resolve_entity_id(mapping.ecobee_entity)
        source = self._raw_source(
            mapping.ecobee_entity,
            int(
                self._options.get(
                    CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS
                )
            ),
        )
        if entity_id is None or not source.usable:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="ecobee_writer_unavailable",
            )
        revision = self._tracker.begin(mapping_id, service, expected)
        self._subscribe_state_reports()
        self._replace_timeout(mapping_id, revision)
        self.refresh_mapping(mapping_id)
        try:
            await self.hass.services.async_call(
                "ecobee",
                service,
                {"entity_id": entity_id, **service_data},
                blocking=True,
                context=context,
            )
        except Exception:  # noqa: BLE001 - source services may raise arbitrary errors
            if self._tracker.fail(mapping_id, revision):
                self._cancel_timeout(mapping_id)
                self._subscribe_state_reports()
                self.refresh_mapping(mapping_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ecobee_command_failed",
            ) from None

    async def async_resume_program(
        self, mapping_id: str, context: Context | None
    ) -> None:
        """Press the explicitly mapped local HomeKit clear-hold button once."""

        mapping = self._mapping_by_id[mapping_id]
        preset = self._optional_raw_source(
            mapping.homekit_preset_entity,
            None,
            now=dt_util.utcnow(),
            report_times=None,
            required_device_id=self._source_device_id(mapping.homekit_entity),
            require_matching_device=True,
        )
        button_id = (
            self.resolve_entity_id(mapping.homekit_clear_hold_entity)
            if mapping.homekit_clear_hold_entity
            else None
        )
        button_entry = (
            er.async_get(self.hass).async_get(button_id) if button_id else None
        )
        if (
            preset is None
            or not preset.usable
            or not self._writer_available(
                mapping.homekit_clear_hold_entity,
                required_device_id=self._source_device_id(mapping.homekit_entity),
            )
            or button_entry is None
            or button_entry.disabled
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="homekit_writer_unavailable",
            )
        revision = self._tracker.begin(
            mapping_id, "clear_hold", {"preset_mode": "__reported__"}
        )
        self._subscribe_state_reports()
        self._replace_timeout(mapping_id, revision)
        self.refresh_mapping(mapping_id)
        try:
            await self.hass.services.async_call(
                "button",
                "press",
                {"entity_id": button_id},
                blocking=True,
                context=context,
            )
        except Exception:  # noqa: BLE001 - source services may raise arbitrary errors
            if self._tracker.fail(mapping_id, revision):
                self._cancel_timeout(mapping_id)
                self._subscribe_state_reports()
                self.refresh_mapping(mapping_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="homekit_command_failed",
            ) from None

    async def async_set_preset_mode(
        self, mapping_id: str, preset_mode: str, context: Context | None
    ) -> None:
        """Select one capability-advertised HomeKit preset exactly once."""

        mapping = self._mapping_by_id[mapping_id]
        snapshot = self.snapshot(mapping_id)
        entity_id = (
            self.resolve_entity_id(mapping.homekit_preset_entity)
            if mapping.homekit_preset_entity
            else None
        )
        if (
            not snapshot.homekit_preset_writable
            or entity_id is None
            or preset_mode not in snapshot.preset_modes
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_preset_mode",
            )
        revision = self._tracker.begin(
            mapping_id, "set_preset_mode", {"preset_mode": preset_mode}
        )
        self._subscribe_state_reports()
        self._replace_timeout(mapping_id, revision)
        self.refresh_mapping(mapping_id)
        try:
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": preset_mode},
                blocking=True,
                context=context,
            )
        except Exception:  # noqa: BLE001 - source services may raise arbitrary errors
            if self._tracker.fail(mapping_id, revision):
                self._cancel_timeout(mapping_id)
                self._subscribe_state_reports()
                self.refresh_mapping(mapping_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="homekit_command_failed",
            ) from None

    async def async_set_minimum_fan_runtime(
        self, mapping_id: str, minutes: int, context: Context | None
    ) -> None:
        """Route the documented Ecobee fan-minimum action."""

        if not 0 <= minutes <= 60:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_fan_runtime",
            )
        await self.async_vendor_command(
            mapping_id,
            "set_fan_min_on_time",
            {"fan_min_on_time": minutes},
            {"minimum_fan_runtime": minutes},
            context,
        )

    @callback
    def _handle_state_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        for mapping in self.mappings:
            references = (
                mapping.homekit_entity,
                mapping.ecobee_entity,
                mapping.homekit_preset_entity,
                mapping.homekit_clear_hold_entity,
                mapping.ecobee_aqi_entity,
                mapping.ecobee_co2_entity,
                mapping.ecobee_voc_entity,
            )
            if entity_id not in {
                resolved
                for reference in references
                if reference and (resolved := self.resolve_entity_id(reference))
            }:
                continue
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
            self.refresh_mapping(
                mapping.mapping_id,
                observation_revision=observation_revision,
                report_times=report_times,
            )

    @callback
    def _handle_state_report_event(self, event: Event[EventStateReportedData]) -> None:
        """Observe a fresh unchanged Ecobee report only for pending commands."""

        entity_id = event.data["entity_id"]
        for mapping in self.mappings:
            operation = self._tracker.pending_operation(mapping.mapping_id)
            expected_reference = self._confirmation_reference(mapping, operation)
            expected_observer = (
                self.resolve_entity_id(expected_reference)
                if expected_reference
                else None
            )
            if operation is None or entity_id != expected_observer:
                continue
            revision = self._tracker.pending_revision(mapping.mapping_id)
            if revision is not None:
                self.refresh_mapping(
                    mapping.mapping_id,
                    observation_revision=revision,
                    report_times={entity_id: event.data["last_reported"]},
                )

    @callback
    def _handle_registry_event(self, _event: Event[Any]) -> None:
        self._subscribe_states()
        self.refresh_all()
        self._sync_helper_device_links()

    @callback
    def _handle_timeout(self, mapping_id: str, revision: int) -> None:
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
            lambda _now: self._handle_timeout(mapping_id, revision),
        )

    def _cancel_timeout(self, mapping_id: str) -> None:
        if unsubscribe := self._unsub_timeouts.pop(mapping_id, None):
            unsubscribe()

    @callback
    def _handle_stale_refresh(self, mapping_id: str) -> None:
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
                lambda _now: self._handle_stale_refresh(mapping_id),
            )

    def _subscribe_states(self) -> None:
        if self._unsub_state:
            self._unsub_state()
        entity_ids = {
            resolved
            for mapping in self.mappings
            for reference in (
                mapping.homekit_entity,
                mapping.ecobee_entity,
                mapping.homekit_preset_entity,
                mapping.homekit_clear_hold_entity,
                mapping.ecobee_aqi_entity,
                mapping.ecobee_co2_entity,
                mapping.ecobee_voc_entity,
            )
            if reference and (resolved := self.resolve_entity_id(reference))
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
            operation = self._tracker.pending_operation(mapping.mapping_id)
            if operation is None:
                continue
            reference = self._confirmation_reference(mapping, operation)
            if reference and (resolved := self.resolve_entity_id(reference)):
                observed_entity_ids.add(resolved)
        self._unsub_state_report = (
            async_track_state_report_event(
                self.hass, observed_entity_ids, self._handle_state_report_event
            )
            if observed_entity_ids
            else None
        )

    def _sync_helper_device_links(self) -> bool:
        """Relink unified entities when their HomeKit source device changes."""

        registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        changed = False
        mapping_by_unique_id = {
            unique_id: mapping
            for mapping in self.mappings
            for unique_id in (
                mapping.mapping_id,
                f"{mapping.mapping_id}_{SUFFIX_MINIMUM_FAN_RUNTIME}",
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
            changed = True
        return changed

    def _optional_raw_source(
        self,
        entity_reference: str | None,
        stale_seconds: int | None,
        *,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
        required_device_id: str | None = None,
        require_matching_device: bool = False,
    ) -> RawSource | None:
        return (
            self._raw_source(
                entity_reference,
                stale_seconds,
                now=now,
                report_times=report_times,
                required_device_id=required_device_id,
                require_matching_device=require_matching_device,
            )
            if entity_reference
            else None
        )

    def _raw_source(
        self,
        entity_reference: str,
        stale_seconds: int | None,
        *,
        require_device: bool = False,
        now: datetime | None = None,
        report_times: Mapping[str, datetime] | None = None,
        required_device_id: str | None = None,
        require_matching_device: bool = False,
    ) -> RawSource:
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, entity_reference)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        if registry_entry is None:
            return RawSource(None, health=SourceHealth.MISSING)
        assert entity_id is not None
        if registry_entry.disabled:
            return RawSource(None, health=SourceHealth.UNAVAILABLE)
        if require_matching_device and (
            required_device_id is None or registry_entry.device_id != required_device_id
        ):
            return RawSource(None, health=SourceHealth.MISSING)
        if require_device and (
            registry_entry.device_id is None
            or dr.async_get(self.hass).async_get(registry_entry.device_id) is None
        ):
            return RawSource(None, health=SourceHealth.MISSING)
        state = self.hass.states.get(entity_id)
        if state is None:
            return RawSource(None, health=SourceHealth.UNAVAILABLE)
        observed_at = (
            report_times.get(entity_id, state.last_reported)
            if report_times is not None
            else state.last_reported
        )
        age = _state_age_seconds(observed_at, now or dt_util.utcnow())
        if state.state in {"unknown", "unavailable"}:
            health = SourceHealth.UNAVAILABLE
        elif stale_seconds is not None and age > stale_seconds:
            health = SourceHealth.STALE
        else:
            health = SourceHealth.HEALTHY
        return RawSource(
            state.state,
            state.attributes,
            age_seconds=age,
            health=health,
        )

    @staticmethod
    def _confirmation_reference(
        mapping: MappingConfig, operation: str | None
    ) -> str | None:
        """Return the one source allowed to confirm the pending operation."""

        if operation in {"set_preset_mode", "clear_hold"}:
            return mapping.homekit_preset_entity
        if operation == "set_humidity":
            return mapping.homekit_entity
        return mapping.ecobee_entity if operation is not None else None

    def _source_device_id(self, entity_reference: str) -> str | None:
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, entity_reference)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        if (
            registry_entry is None
            or registry_entry.device_id is None
            or dr.async_get(self.hass).async_get(registry_entry.device_id) is None
        ):
            return None
        return registry_entry.device_id

    def _writer_available(
        self,
        entity_reference: str | None,
        *,
        required_device_id: str | None,
    ) -> bool:
        if entity_reference is None or required_device_id is None:
            return False
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, entity_reference)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        state = self.hass.states.get(entity_id) if entity_id else None
        return bool(
            registry_entry is not None
            and not registry_entry.disabled
            and registry_entry.device_id == required_device_id
            and state is not None
            and state.state != "unavailable"
        )

    def _refresh_mapping_issue(self, mapping: MappingConfig) -> None:
        registry = er.async_get(self.hass)
        homekit_entity_id = er.async_resolve_entity_id(registry, mapping.homekit_entity)
        homekit_entry = (
            registry.async_get(homekit_entity_id) if homekit_entity_id else None
        )
        homekit_device_id = self._source_device_id(mapping.homekit_entity)
        ecobee_device_id = self._source_device_id(mapping.ecobee_entity)
        invalid = []
        if homekit_entry is None:
            invalid.append("homekit")
        elif (
            homekit_entry.device_id is None
            or dr.async_get(self.hass).async_get(homekit_entry.device_id) is None
        ):
            invalid.append("homekit device")
        ecobee_entity_id = er.async_resolve_entity_id(registry, mapping.ecobee_entity)
        if ecobee_entity_id is None:
            invalid.append("ecobee")
        ecobee_siblings = (
            mapping.ecobee_aqi_entity,
            mapping.ecobee_co2_entity,
            mapping.ecobee_voc_entity,
        )
        if (
            ecobee_entity_id is not None
            and any(ecobee_siblings)
            and ecobee_device_id is None
        ):
            invalid.append("ecobee device")
        optional_sources = (
            ("HomeKit preset", mapping.homekit_preset_entity, homekit_device_id),
            (
                "HomeKit clear hold",
                mapping.homekit_clear_hold_entity,
                homekit_device_id,
            ),
            ("Ecobee air quality", mapping.ecobee_aqi_entity, ecobee_device_id),
            ("Ecobee carbon dioxide", mapping.ecobee_co2_entity, ecobee_device_id),
            (
                "Ecobee volatile organic compounds",
                mapping.ecobee_voc_entity,
                ecobee_device_id,
            ),
        )
        for label, reference, required_device_id in optional_sources:
            if not reference:
                continue
            entity_id = er.async_resolve_entity_id(registry, reference)
            entry = registry.async_get(entity_id) if entity_id else None
            if entry is None:
                invalid.append(label)
            elif (
                required_device_id is not None and entry.device_id != required_device_id
            ):
                invalid.append(f"{label} association")
        issue_id = f"mapping_{mapping.mapping_id}"
        if invalid:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="mapping_source_missing",
                translation_placeholders={"source": ", ".join(invalid)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)


def _state_age_seconds(last_reported: datetime, now: datetime) -> int:
    return max(0, int((now - last_reported).total_seconds()))
