"""State subscriptions, normalization, repairs, and command routing."""

from __future__ import annotations

from asyncio import FIRST_COMPLETED, CancelledError, Lock, create_task, gather, wait
from asyncio import Event as AsyncEvent
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any, Literal, NoReturn

from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
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
from homeassistant.util.unit_conversion import TemperatureConverter

from .commands import CommandTracker
from .const import (
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_STALE_SECONDS,
    DEFAULT_CONFIRMATION_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DOMAIN,
    HOMEKIT_PAIR_SETTLE_SECONDS,
    SERVICE_CREATE_VACATION,
    SERVICE_DELETE_VACATION,
    SERVICE_SET_OCCUPANCY_MODES,
    SERVICE_SET_SENSORS_USED_IN_CLIMATE,
    SIGNAL_SNAPSHOT_UPDATED,
    SUFFIX_AIR_QUALITY_INDEX,
    SUFFIX_CO2,
    SUFFIX_EQUIPMENT_STAGE,
    SUFFIX_MINIMUM_FAN_RUNTIME,
    SUFFIX_NOTIFICATION,
    SUFFIX_RESUME_PROGRAM,
    SUFFIX_SOURCE_DEGRADED,
    SUFFIX_VOC,
)
from .models import (
    CommandSummary,
    MappingConfig,
    NormalizedSnapshot,
    RawSource,
    SourceHealth,
    build_snapshot,
    finite_number,
    finite_temperature,
    homekit_temperature_agrees,
)
from .source_contracts import (
    AIR_QUALITY_SENSOR_CONTRACTS,
    PhysicalIdentityStatus,
    homekit_action_contract_valid,
    physical_identity_status,
    sensor_contract_valid,
    temperature_source_unit,
)
from .temperature_quality import (
    SourceIdentity,
    TemperatureObservation,
    TemperatureRecovery,
)

# Core 2026.8's HomeKit writer honors the accessory's native granularity even
# though its climate adapter omits target_temp_step. Physical identity remains a
# separate per-mapping proof that is reevaluated through registry events.
HOMEKIT_WRITER_GRANULARITY_PROVEN = True
UNCONFIRMABLE_VENDOR_ACTIONS = frozenset(
    {
        SERVICE_CREATE_VACATION,
        SERVICE_DELETE_VACATION,
        SERVICE_SET_OCCUPANCY_MODES,
        SERVICE_SET_SENSORS_USED_IN_CLIMATE,
    }
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
        self._watched_entity_ids.clear()
        self._watched_device_ids.clear()
        for mapping in self.mappings:
            ir.async_delete_issue(self.hass, DOMAIN, f"mapping_{mapping.mapping_id}")

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
            require_matching_device=True,
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
        temperature_recovery_pending = self._update_temperature_recovery(
            mapping,
            homekit,
            homekit_temperature,
            pair_settled=homekit_pair_settled,
            confirmation=temperature_confirmation,
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

        async with self._command_slot(mapping_id):
            mapping = self._mapping_by_id[mapping_id]
            snapshot = self.snapshot(mapping_id)
            entity_id = self.resolve_entity_id(mapping.homekit_entity)
            if not snapshot.homekit_writable or entity_id is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="homekit_writer_unavailable",
                )
            self._validate_standard_command(mapping_id, service, service_data)
            await self._async_tracked_call(
                mapping_id,
                service,
                "climate",
                service,
                {**service_data, "entity_id": entity_id},
                expected,
                context,
                "homekit_command_failed",
            )

    async def async_vendor_command(
        self,
        mapping_id: str,
        service: str,
        service_data: Mapping[str, Any],
        expected: Mapping[str, Any],
        context: Context | None,
    ) -> None:
        """Call exactly one explicit Ecobee action with no fallback."""

        async with self._command_slot(mapping_id):
            entity_id = self._vendor_writer_entity(mapping_id, service)
            await self._async_tracked_call(
                mapping_id,
                service,
                "ecobee",
                service,
                {**service_data, "entity_id": entity_id},
                expected,
                context,
                "ecobee_command_failed",
            )

    async def async_vendor_action(
        self,
        mapping_id: str,
        service: str,
        service_data: Mapping[str, Any],
        context: Context | None,
    ) -> None:
        """Submit one Ecobee effect that has no honest state confirmation."""

        async with self._command_slot(mapping_id):
            entity_id = self._vendor_writer_entity(mapping_id, service)
            self._validate_vendor_action(mapping_id, service, service_data)
            await self._async_tracked_call(
                mapping_id,
                service,
                "ecobee",
                service,
                {**service_data, "entity_id": entity_id},
                None,
                context,
                "ecobee_command_failed",
            )

    async def async_resume_program(
        self, mapping_id: str, context: Context | None
    ) -> None:
        """Submit one mapped local clear-hold action without false confirmation."""

        async with self._command_slot(mapping_id):
            mapping = self._mapping_by_id[mapping_id]
            button_id = (
                self.resolve_entity_id(mapping.homekit_clear_hold_entity)
                if mapping.homekit_clear_hold_entity
                else None
            )
            if button_id is None or not self._homekit_action_available(
                mapping, "clear_hold"
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="homekit_writer_unavailable",
                )
            await self._async_tracked_call(
                mapping_id,
                "press",
                "button",
                "press",
                {"entity_id": button_id},
                None,
                context,
                "homekit_command_failed",
            )

    async def async_set_preset_mode(
        self, mapping_id: str, preset_mode: str, context: Context | None
    ) -> None:
        """Select one capability-advertised HomeKit preset exactly once."""

        async with self._command_slot(mapping_id):
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
                or not self._homekit_action_available(mapping, "preset")
                or preset_mode not in snapshot.preset_modes
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unsupported_preset_mode",
                )
            await self._async_tracked_call(
                mapping_id,
                "set_preset_mode",
                "select",
                "select_option",
                {"entity_id": entity_id, "option": preset_mode},
                {"preset_mode": preset_mode},
                context,
                "homekit_command_failed",
            )

    async def async_set_minimum_fan_runtime(
        self, mapping_id: str, minutes: float, context: Context | None
    ) -> None:
        """Route the documented Ecobee fan-minimum action."""

        numeric_minutes = finite_number(minutes)
        if (
            numeric_minutes is None
            or not numeric_minutes.is_integer()
            or not 0 <= numeric_minutes <= 60
            or int(numeric_minutes) % 5 != 0
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_fan_runtime",
            )
        aligned_minutes = int(numeric_minutes)
        await self.async_vendor_command(
            mapping_id,
            "set_fan_min_on_time",
            {"fan_min_on_time": aligned_minutes},
            {"minimum_fan_runtime": aligned_minutes},
            context,
        )

    async def async_send_notification(
        self, mapping_id: str, message: str, context: Context | None
    ) -> None:
        """Forward one message to the explicitly mapped Ecobee notify entity."""

        async with self._command_slot(mapping_id):
            mapping = self._mapping_by_id[mapping_id]
            entity_id = (
                self.resolve_entity_id(mapping.ecobee_notify_entity)
                if mapping.ecobee_notify_entity
                else None
            )
            if (
                not message.strip()
                or entity_id is None
                or not self.snapshot(mapping_id).ecobee_notify_writable
                or not self.hass.services.has_service("notify", "send_message")
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="ecobee_notification_unavailable",
                )
            try:
                await self.hass.services.async_call(
                    "notify",
                    "send_message",
                    {"message": message},
                    blocking=True,
                    context=context,
                    target={"entity_id": entity_id},
                )
            except Exception:  # noqa: BLE001 - source services may raise arbitrary errors
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="ecobee_notification_failed",
                ) from None

    async def _async_tracked_call(
        self,
        mapping_id: str,
        operation: str,
        domain: str,
        service: str,
        service_data: Mapping[str, Any],
        expected: Mapping[str, Any] | None,
        context: Context | None,
        error_key: str,
    ) -> None:
        """Track one write, using None expectations for submitted-only effects."""

        revision = self._tracker.begin(
            mapping_id, operation, expected if expected is not None else {}
        )
        self._cancel_timeout(mapping_id)
        if expected is not None:
            self._subscribe_state_reports()
        self.refresh_mapping(mapping_id)
        try:
            await self.hass.services.async_call(
                domain,
                service,
                dict(service_data),
                blocking=True,
                context=context,
            )
        except CancelledError:
            if (
                self._tracker.unconfirm(mapping_id, revision)
                and not self._stopped.is_set()
            ):
                self._subscribe_state_reports()
                self.refresh_mapping(mapping_id)
            raise
        except Exception:  # noqa: BLE001 - source services may raise arbitrary errors
            if self._tracker.fail(mapping_id, revision) and not self._stopped.is_set():
                self._subscribe_state_reports()
                self.refresh_mapping(mapping_id)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=error_key,
            ) from None

        if self._stopped.is_set():
            return
        accepted = (
            self._tracker.accept_write(mapping_id, revision)
            if expected is not None
            else self._tracker.submit(mapping_id, revision)
        )
        if not accepted:
            return
        self._subscribe_state_reports()
        if (
            expected is not None
            and self._tracker.pending_revision(mapping_id) == revision
        ):
            self._replace_timeout(mapping_id, revision)
        self.refresh_mapping(mapping_id)

    def _require_command_admission(self) -> None:
        """A stopped manager can never become a command writer again."""

        if self._stopped.is_set():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="command_unavailable",
            )

    @asynccontextmanager
    async def _command_slot(self, mapping_id: str) -> AsyncIterator[None]:
        """Keep FIFO dispatch while rejecting queued commands promptly on stop."""

        self._require_command_admission()
        lock = self._command_locks[mapping_id]
        acquire = create_task(lock.acquire())
        stopped = create_task(self._stopped.wait())
        try:
            await wait((acquire, stopped), return_when=FIRST_COMPLETED)
            self._require_command_admission()
            # Source events may still be queued. Read current registry/state
            # contracts after admission, before validating or tracking an effect.
            self.refresh_mapping(mapping_id)
            yield
        finally:
            for task in (acquire, stopped):
                if not task.done():
                    task.cancel()
            try:
                await gather(acquire, stopped, return_exceptions=True)
            finally:
                if (
                    acquire.done()
                    and not acquire.cancelled()
                    and acquire.exception() is None
                    and acquire.result()
                ):
                    lock.release()

    def _validate_standard_command(
        self, mapping_id: str, service: str, service_data: Mapping[str, Any]
    ) -> None:
        """Recheck mutable writer contracts inside the serialized command slot."""

        snapshot = self.snapshot(mapping_id)
        feature = {
            "set_fan_mode": ClimateEntityFeature.FAN_MODE,
            "set_humidity": ClimateEntityFeature.TARGET_HUMIDITY,
            "turn_on": ClimateEntityFeature.TURN_ON,
            "turn_off": ClimateEntityFeature.TURN_OFF,
        }.get(service)
        if feature is not None and not snapshot.supported_features & feature:
            self._raise_validation("unsupported_command")
        if service == "set_temperature":
            self._validate_temperature_command(mapping_id, service_data)
        if (
            service in {"set_temperature", "set_hvac_mode"}
            and "hvac_mode" in service_data
            and service_data["hvac_mode"] not in snapshot.hvac_modes
        ):
            self._raise_validation("unsupported_hvac_mode")
        if (
            service == "set_fan_mode"
            and service_data.get("fan_mode") not in snapshot.fan_modes
        ):
            self._raise_validation("unsupported_fan_mode")
        if service == "set_humidity":
            self.validated_humidity(mapping_id, service_data.get("humidity"))

    def _validate_temperature_command(
        self, mapping_id: str, service_data: Mapping[str, Any]
    ) -> None:
        """Validate every supplied target against the current HomeKit writer."""

        snapshot = self.snapshot(mapping_id)
        for key, feature in (
            ("temperature", ClimateEntityFeature.TARGET_TEMPERATURE),
            ("target_temp_low", ClimateEntityFeature.TARGET_TEMPERATURE_RANGE),
            ("target_temp_high", ClimateEntityFeature.TARGET_TEMPERATURE_RANGE),
        ):
            if key in service_data:
                if not snapshot.supported_features & feature:
                    self._raise_validation("unsupported_command")
                self.validated_temperature(mapping_id, service_data[key])

    def _validate_vendor_action(
        self, mapping_id: str, service: str, service_data: Mapping[str, Any]
    ) -> None:
        """Reject queued actions whose bounds or selected-device ownership changed."""

        if service == SERVICE_CREATE_VACATION:
            self.validated_vacation_temperature(
                mapping_id, service_data.get("cool_temp")
            )
            self.validated_vacation_temperature(
                mapping_id, service_data.get("heat_temp")
            )
        elif service == SERVICE_SET_SENSORS_USED_IN_CLIMATE:
            if not self.ecobee_sensor_devices_valid(
                mapping_id, service_data["device_ids"]
            ):
                self._raise_validation("invalid_sensor_selection")

    def validated_temperature(self, mapping_id: str, value: Any) -> float:
        """Validate a numeric target using current HomeKit bounds."""

        temperature = finite_number(value)
        if temperature is None:
            self._raise_validation("invalid_temperature")
        snapshot = self.snapshot(mapping_id)
        if (snapshot.min_temp is not None and temperature < snapshot.min_temp) or (
            snapshot.max_temp is not None and temperature > snapshot.max_temp
        ):
            self._raise_validation("invalid_temperature")
        return temperature

    def validated_humidity(self, mapping_id: str, value: Any) -> int:
        """Validate an integer target using current HomeKit humidity bounds."""

        humidity = finite_number(value)
        if humidity is None or not humidity.is_integer():
            self._raise_validation("invalid_humidity")
        snapshot = self.snapshot(mapping_id)
        if (
            snapshot.min_humidity is None
            or snapshot.max_humidity is None
            or humidity < snapshot.min_humidity
            or humidity > snapshot.max_humidity
        ):
            self._raise_validation("invalid_humidity")
        return int(humidity)

    def validated_vacation_temperature(self, mapping_id: str, value: Any) -> float:
        """Validate a numeric vacation target using current Ecobee writer bounds."""

        temperature = finite_number(value)
        if temperature is None:
            self._raise_validation("invalid_vacation_temperature")
        snapshot = self.snapshot(mapping_id)
        minimum = snapshot.ecobee_min_temp
        maximum = snapshot.ecobee_max_temp
        unit = snapshot.ecobee_temperature_unit
        if minimum is None or maximum is None or unit is None:
            self._raise_validation("ecobee_writer_unavailable")
        if not minimum <= temperature <= maximum:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_vacation_temperature_bounds",
                translation_placeholders={
                    "minimum": f"{minimum:g}",
                    "maximum": f"{maximum:g}",
                    "unit": unit,
                },
            )
        return temperature

    @staticmethod
    def _raise_validation(translation_key: str) -> NoReturn:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
        )

    def _vendor_writer_entity(self, mapping_id: str, service: str) -> str:
        """Resolve one healthy mapped Ecobee writer for a supported action."""

        mapping = self._mapping_by_id[mapping_id]
        entity_id = self.resolve_entity_id(mapping.ecobee_entity)
        identity_proven = (
            physical_identity_status(
                self.hass, mapping.homekit_entity, mapping.ecobee_entity
            )
            is PhysicalIdentityStatus.MATCH
        )
        source = self._raw_source(
            mapping.ecobee_entity,
            int(
                self._options.get(
                    CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS
                )
            ),
        )
        if (
            entity_id is None
            or not identity_proven
            or not source.usable
            or not self.hass.services.has_service("ecobee", service)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="ecobee_writer_unavailable",
            )
        return entity_id

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
    ) -> bool:
        """Keep confirmed rejected values out until this source changes and agrees."""

        mapping_id = mapping.mapping_id
        if mapping.homekit_temperature_entity is None:
            self._temperature_recovery.pop(mapping_id, None)
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

    def _climate_raw_source(
        self,
        entity_reference: str,
        stale_seconds: int | None,
        *,
        require_device: bool = False,
        now: datetime | None = None,
        report_times: Mapping[str, datetime] | None = None,
    ) -> RawSource:
        """Read a climate source using Core's configured-unit state contract."""

        source = self._raw_source(
            entity_reference,
            stale_seconds,
            require_device=require_device,
            now=now,
            report_times=report_times,
        )
        return RawSource(
            source.state,
            {
                **source.attributes,
                ATTR_UNIT_OF_MEASUREMENT: self.hass.config.units.temperature_unit,
            },
            age_seconds=source.age_seconds,
            health=source.health,
        )

    def _air_quality_raw_source(
        self,
        entity_reference: str | None,
        contract_name: str,
        stale_seconds: int,
        *,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
        required_device_id: str | None,
        physical_identity_proven: bool,
    ) -> RawSource | None:
        source = self._optional_raw_source(
            entity_reference,
            stale_seconds,
            now=now,
            report_times=report_times,
            required_device_id=required_device_id,
            require_matching_device=True,
        )
        if source is None or not source.usable:
            return source
        if (
            not physical_identity_proven
            or entity_reference is None
            or not sensor_contract_valid(
                self.hass,
                entity_reference,
                AIR_QUALITY_SENSOR_CONTRACTS[contract_name],
            )
        ):
            return self._invalid_source(source)
        return source

    def _temperature_raw_source(
        self,
        entity_reference: str | None,
        homekit: RawSource,
        *,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
        required_device_id: str | None,
    ) -> RawSource | None:
        """Read and unit-normalize an explicit same-device temperature sensor."""

        if entity_reference is None:
            return None
        source = self._raw_source(
            entity_reference,
            None,
            now=now,
            report_times=report_times,
            required_device_id=required_device_id,
            require_matching_device=True,
        )
        if not source.usable:
            return source
        value = finite_number(source.state, allow_text=True)
        if value is None:
            # Invalid measurements do not establish a transport outage.
            return RawSource(
                None,
                source.attributes,
                age_seconds=source.age_seconds,
                health=source.health,
            )
        source_unit = temperature_source_unit(self.hass, entity_reference)
        target_unit = homekit.attributes.get(
            ATTR_UNIT_OF_MEASUREMENT, self.hass.config.units.temperature_unit
        )
        try:
            if source_unit is None:
                raise ValueError
            target_unit_enum = UnitOfTemperature(str(target_unit))
        except TypeError, ValueError:
            return self._invalid_source(source)
        value = finite_temperature(value, source_unit)
        try:
            converted = (
                finite_temperature(
                    TemperatureConverter.convert(value, source_unit, target_unit_enum),
                    target_unit_enum,
                )
                if value is not None
                else None
            )
        except TypeError, ValueError, OverflowError:
            converted = None
        return RawSource(
            str(converted) if converted is not None else None,
            source.attributes,
            age_seconds=source.age_seconds,
            health=source.health,
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
        if state.state == STATE_UNKNOWN:
            health = SourceHealth.UNKNOWN
        elif state.state == STATE_UNAVAILABLE:
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
    def _invalid_source(source: RawSource) -> RawSource:
        return RawSource(
            None,
            source.attributes,
            age_seconds=source.age_seconds,
            health=SourceHealth.UNAVAILABLE,
        )

    @staticmethod
    def _confirmation_reference(
        mapping: MappingConfig, operation: str | None
    ) -> str | None:
        """Return the one source allowed to confirm the pending operation."""

        if operation == "set_preset_mode":
            return mapping.homekit_preset_entity
        if operation == "set_humidity":
            return mapping.homekit_entity
        if operation in UNCONFIRMABLE_VENDOR_ACTIONS:
            return None
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
            and state.state != STATE_UNAVAILABLE
        )

    def _homekit_action_available(
        self, mapping: MappingConfig, role: Literal["preset", "clear_hold"]
    ) -> bool:
        reference = (
            mapping.homekit_preset_entity
            if role == "preset"
            else mapping.homekit_clear_hold_entity
        )
        return bool(
            reference is not None
            and self._writer_available(
                reference,
                required_device_id=self._source_device_id(mapping.homekit_entity),
            )
            and homekit_action_contract_valid(self.hass, reference, role)
        )

    def ecobee_sensor_devices_valid(
        self, mapping_id: str, device_ids: list[str]
    ) -> bool:
        """Prove selected sensor devices belong to the mapped Ecobee account."""

        mapping = self._mapping_by_id[mapping_id]
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, mapping.ecobee_entity)
        source_entry = registry.async_get(entity_id) if entity_id else None
        source_config_entry_id = source_entry.config_entry_id if source_entry else None
        if source_config_entry_id is None:
            return False
        device_registry = dr.async_get(self.hass)
        return all(
            (device := device_registry.async_get(device_id)) is not None
            and source_config_entry_id == device.config_entry_id
            and any(domain == "ecobee" for domain, _value in device.identifiers)
            for device_id in device_ids
        )

    def _refresh_mapping_issue(self, mapping: MappingConfig) -> None:
        invalid, homekit_device_id, ecobee_device_id = self._required_source_issues(
            mapping
        )
        invalid.extend(
            self._optional_source_issues(mapping, homekit_device_id, ecobee_device_id)
        )
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
                translation_placeholders={
                    "mapping": mapping.name,
                    "source": ", ".join(invalid),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _required_source_issues(
        self, mapping: MappingConfig
    ) -> tuple[list[str], str | None, str | None]:
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
        elif homekit_entry.disabled:
            invalid.append("homekit disabled")
        elif (
            homekit_entry.device_id is None
            or dr.async_get(self.hass).async_get(homekit_entry.device_id) is None
        ):
            invalid.append("homekit device")
        ecobee_entity_id = er.async_resolve_entity_id(registry, mapping.ecobee_entity)
        ecobee_entry = (
            registry.async_get(ecobee_entity_id) if ecobee_entity_id else None
        )
        if ecobee_entry is None:
            invalid.append("ecobee")
        elif ecobee_entry.disabled:
            invalid.append("ecobee disabled")
        elif ecobee_device_id is None:
            invalid.append("ecobee device")
        elif (
            homekit_entry is not None
            and homekit_device_id is not None
            and ecobee_device_id is not None
            and physical_identity_status(
                self.hass, mapping.homekit_entity, mapping.ecobee_entity
            )
            is not PhysicalIdentityStatus.MATCH
        ):
            invalid.append("physical device identity")
        return invalid, homekit_device_id, ecobee_device_id

    def _optional_source_issues(
        self,
        mapping: MappingConfig,
        homekit_device_id: str | None,
        ecobee_device_id: str | None,
    ) -> list[str]:
        registry = er.async_get(self.hass)
        invalid: list[str] = []
        optional_sources = (
            (
                "HomeKit preset",
                mapping.homekit_preset_entity,
                homekit_device_id,
                None,
            ),
            (
                "HomeKit clear hold",
                mapping.homekit_clear_hold_entity,
                homekit_device_id,
                None,
            ),
            (
                "HomeKit temperature",
                mapping.homekit_temperature_entity,
                homekit_device_id,
                None,
            ),
            (
                "Ecobee air quality",
                mapping.ecobee_aqi_entity,
                ecobee_device_id,
                "aqi",
            ),
            (
                "Ecobee carbon dioxide",
                mapping.ecobee_co2_entity,
                ecobee_device_id,
                "co2",
            ),
            (
                "Ecobee volatile organic compounds",
                mapping.ecobee_voc_entity,
                ecobee_device_id,
                "voc",
            ),
            (
                "Ecobee notification",
                mapping.ecobee_notify_entity,
                ecobee_device_id,
                None,
            ),
        )
        for label, reference, required_device_id, contract_name in optional_sources:
            if not reference:
                continue
            entity_id = er.async_resolve_entity_id(registry, reference)
            entry = registry.async_get(entity_id) if entity_id else None
            if entry is None:
                invalid.append(label)
            elif entry.disabled:
                invalid.append(f"{label} disabled")
            elif (
                required_device_id is not None and entry.device_id != required_device_id
            ):
                invalid.append(f"{label} association")
            elif (
                (
                    label == "HomeKit temperature"
                    and temperature_source_unit(self.hass, reference) is None
                )
                or (
                    label == "HomeKit preset"
                    and not homekit_action_contract_valid(
                        self.hass, reference, "preset"
                    )
                )
                or (
                    label == "HomeKit clear hold"
                    and not homekit_action_contract_valid(
                        self.hass, reference, "clear_hold"
                    )
                )
                or (
                    contract_name is not None
                    and not sensor_contract_valid(
                        self.hass,
                        reference,
                        AIR_QUALITY_SENSOR_CONTRACTS[contract_name],
                    )
                )
            ):
                invalid.append(label)
        return invalid


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


def _state_age_seconds(last_reported: datetime, now: datetime) -> int:
    return max(0, int((now - last_reported).total_seconds()))
