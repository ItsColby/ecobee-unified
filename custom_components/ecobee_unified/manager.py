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
    ATTR_RESUME_ALL,
    CONF_BEESTAT_STALE_SECONDS,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_HOMEKIT_STALE_SECONDS,
    DEFAULT_BEESTAT_STALE_SECONDS,
    DEFAULT_CONFIRMATION_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DEFAULT_HOMEKIT_STALE_SECONDS,
    DOMAIN,
    SIGNAL_SNAPSHOT_UPDATED,
)
from .models import (
    MappingConfig,
    NormalizedSnapshot,
    RawSource,
    SourceHealth,
    build_snapshot,
)


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
        homekit_stale_seconds = int(
            self._options.get(CONF_HOMEKIT_STALE_SECONDS, DEFAULT_HOMEKIT_STALE_SECONDS)
        )
        ecobee_stale_seconds = int(
            self._options.get(CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS)
        )
        beestat_stale_seconds = int(
            self._options.get(CONF_BEESTAT_STALE_SECONDS, DEFAULT_BEESTAT_STALE_SECONDS)
        )
        homekit = self._raw_source(
            mapping.homekit_entity,
            homekit_stale_seconds,
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
        scheduled_profile = self._optional_raw_source(
            mapping.scheduled_profile_entity,
            beestat_stale_seconds,
            now=now,
            report_times=report_times,
        )
        next_transition = self._optional_raw_source(
            mapping.next_transition_entity,
            beestat_stale_seconds,
            now=now,
            report_times=report_times,
        )
        snapshot = build_snapshot(
            mapping_id,
            homekit,
            ecobee,
            scheduled_profile,
            next_transition,
            self._tracker.summary(mapping_id),
        )
        if observation_revision is not None and self._tracker.observe(
            mapping_id, observation_revision, snapshot
        ):
            self._cancel_timeout(mapping_id)
            snapshot = build_snapshot(
                mapping_id,
                homekit,
                ecobee,
                scheduled_profile,
                next_transition,
                self._tracker.summary(mapping_id),
            )
        self._snapshots[mapping_id] = snapshot
        stale_inputs = [
            (homekit, homekit_stale_seconds),
            (ecobee, ecobee_stale_seconds),
        ]
        stale_inputs.extend(
            (source, beestat_stale_seconds)
            for source in (scheduled_profile, next_transition)
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
        try:
            await self.hass.services.async_call(
                "ecobee",
                service,
                {"entity_id": entity_id, **service_data},
                blocking=True,
                context=context,
            )
        except Exception:  # noqa: BLE001 - source services may raise arbitrary errors
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ecobee_command_failed",
            ) from None

    async def async_resume_program(
        self, mapping_id: str, resume_all: bool, context: Context | None
    ) -> None:
        """Route the documented Ecobee resume-program action."""

        await self.async_vendor_command(
            mapping_id,
            "resume_program",
            {ATTR_RESUME_ALL: resume_all},
            context,
        )

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
            context,
        )

    @callback
    def _handle_state_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        for mapping in self.mappings:
            ecobee_id = self.resolve_entity_id(mapping.ecobee_entity)
            references = (
                mapping.homekit_entity,
                mapping.ecobee_entity,
                mapping.scheduled_profile_entity,
                mapping.next_transition_entity,
            )
            if entity_id not in {
                resolved
                for reference in references
                if reference and (resolved := self.resolve_entity_id(reference))
            }:
                continue
            observation_revision = (
                self._tracker.current_revision(mapping.mapping_id)
                if entity_id == ecobee_id
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
            if entity_id != self.resolve_entity_id(mapping.ecobee_entity):
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
        if self._sync_helper_device_links():
            self.hass.config_entries.async_schedule_reload(self.entry_id)

    @callback
    def _handle_timeout(self, mapping_id: str, revision: int) -> None:
        self._unsub_timeouts.pop(mapping_id, None)
        if self._tracker.timeout(mapping_id, revision):
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
        if self._unsub_state_report:
            self._unsub_state_report()
        entity_ids = {
            resolved
            for mapping in self.mappings
            for reference in (
                mapping.homekit_entity,
                mapping.ecobee_entity,
                mapping.scheduled_profile_entity,
                mapping.next_transition_entity,
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
        ecobee_entity_ids = {
            resolved
            for mapping in self.mappings
            if (resolved := self.resolve_entity_id(mapping.ecobee_entity))
        }
        self._unsub_state_report = (
            async_track_state_report_event(
                self.hass, ecobee_entity_ids, self._handle_state_report_event
            )
            if ecobee_entity_ids
            else None
        )

    def _sync_helper_device_links(self) -> bool:
        """Relink unified entities when their HomeKit source device changes."""

        registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        changed = False
        for mapping in self.mappings:
            helper_entity_id = registry.async_get_entity_id(
                "climate", DOMAIN, mapping.mapping_id
            )
            helper_entry = (
                registry.async_get(helper_entity_id) if helper_entity_id else None
            )
            if helper_entry is None or helper_entry.config_entry_id != self.entry_id:
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
        stale_seconds: int,
        *,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
    ) -> RawSource | None:
        return (
            self._raw_source(
                entity_reference,
                stale_seconds,
                now=now,
                report_times=report_times,
            )
            if entity_reference
            else None
        )

    def _raw_source(
        self,
        entity_reference: str,
        stale_seconds: int,
        *,
        require_device: bool = False,
        now: datetime | None = None,
        report_times: Mapping[str, datetime] | None = None,
    ) -> RawSource:
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, entity_reference)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        if registry_entry is None:
            return RawSource(None, health=SourceHealth.MISSING)
        assert entity_id is not None
        if registry_entry.disabled:
            return RawSource(None, health=SourceHealth.UNAVAILABLE)
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
        elif age > stale_seconds:
            health = SourceHealth.STALE
        else:
            health = SourceHealth.HEALTHY
        return RawSource(
            state.state,
            state.attributes,
            age_seconds=age,
            health=health,
        )

    def _refresh_mapping_issue(self, mapping: MappingConfig) -> None:
        registry = er.async_get(self.hass)
        homekit_entity_id = er.async_resolve_entity_id(registry, mapping.homekit_entity)
        homekit_entry = (
            registry.async_get(homekit_entity_id) if homekit_entity_id else None
        )
        invalid = []
        if homekit_entry is None:
            invalid.append("homekit")
        elif (
            homekit_entry.device_id is None
            or dr.async_get(self.hass).async_get(homekit_entry.device_id) is None
        ):
            invalid.append("homekit device")
        if er.async_resolve_entity_id(registry, mapping.ecobee_entity) is None:
            invalid.append("ecobee")
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
