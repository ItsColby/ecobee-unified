"""Validated, tracked command routing for mapped writers."""

from __future__ import annotations

from asyncio import FIRST_COMPLETED, CancelledError, Lock, create_task, gather, wait
from asyncio import Event as AsyncEvent
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, NoReturn

from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.core import (
    Context,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .commands import CommandTracker
from .const import (
    CONF_ECOBEE_STALE_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DOMAIN,
    SERVICE_CREATE_VACATION,
    SERVICE_SET_SENSORS_USED_IN_CLIMATE,
)
from .manager_sources import SourceReader
from .models import (
    NormalizedSnapshot,
    finite_number,
)
from .source_contracts import (
    PhysicalIdentityStatus,
    physical_identity_status,
)
from .temperature_quality import (
    TemperatureObservation,
)


def raise_validation(translation_key: str) -> NoReturn:
    """Raise one translated action validation error."""

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
    )


class CommandRunner(SourceReader):
    """Admit, validate, and track vendor and standard commands."""

    _command_locks: dict[str, Lock]
    _options: Mapping[str, Any]
    _stopped: AsyncEvent
    _tracker: CommandTracker

    if TYPE_CHECKING:

        def snapshot(self, mapping_id: str) -> NormalizedSnapshot: ...

        def resolve_entity_id(self, entity_reference: str) -> str | None: ...

        def refresh_mapping(
            self,
            mapping_id: str,
            *,
            observation_revision: int | None = None,
            report_times: Mapping[str, datetime] | None = None,
            homekit_pair_settled: bool = False,
            temperature_confirmation: TemperatureObservation | None = None,
        ) -> None: ...

        def _cancel_timeout(self, mapping_id: str) -> None: ...

        def _replace_timeout(self, mapping_id: str, revision: int) -> None: ...

        def _subscribe_state_reports(self) -> None: ...

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
                raise_validation("homekit_writer_unavailable")
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
                raise_validation("homekit_writer_unavailable")
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
                raise_validation("unsupported_preset_mode")
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
            raise_validation("invalid_fan_runtime")
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
                raise_validation("ecobee_notification_unavailable")
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
            raise_validation("command_unavailable")

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
            raise_validation("unsupported_command")
        if service == "set_temperature":
            self._validate_temperature_command(mapping_id, service_data)
        if (
            service in {"set_temperature", "set_hvac_mode"}
            and "hvac_mode" in service_data
            and service_data["hvac_mode"] not in snapshot.hvac_modes
        ):
            raise_validation("unsupported_hvac_mode")
        if (
            service == "set_fan_mode"
            and service_data.get("fan_mode") not in snapshot.fan_modes
        ):
            raise_validation("unsupported_fan_mode")
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
                    raise_validation("unsupported_command")
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
                raise_validation("invalid_sensor_selection")

    def validated_temperature(self, mapping_id: str, value: Any) -> float:
        """Validate a numeric target using current HomeKit bounds."""

        temperature = finite_number(value)
        if temperature is None:
            raise_validation("invalid_temperature")
        snapshot = self.snapshot(mapping_id)
        if (snapshot.min_temp is not None and temperature < snapshot.min_temp) or (
            snapshot.max_temp is not None and temperature > snapshot.max_temp
        ):
            raise_validation("invalid_temperature")
        return temperature

    def validated_humidity(self, mapping_id: str, value: Any) -> int:
        """Validate an integer target using current HomeKit humidity bounds."""

        humidity = finite_number(value)
        if humidity is None or not humidity.is_integer():
            raise_validation("invalid_humidity")
        snapshot = self.snapshot(mapping_id)
        if (
            snapshot.min_humidity is None
            or snapshot.max_humidity is None
            or humidity < snapshot.min_humidity
            or humidity > snapshot.max_humidity
        ):
            raise_validation("invalid_humidity")
        return int(humidity)

    def validated_vacation_temperature(self, mapping_id: str, value: Any) -> float:
        """Validate a numeric vacation target using current Ecobee writer bounds."""

        temperature = finite_number(value)
        if temperature is None:
            raise_validation("invalid_vacation_temperature")
        snapshot = self.snapshot(mapping_id)
        minimum = snapshot.ecobee_min_temp
        maximum = snapshot.ecobee_max_temp
        unit = snapshot.ecobee_temperature_unit
        if minimum is None or maximum is None or unit is None:
            raise_validation("ecobee_writer_unavailable")
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
            raise_validation("ecobee_writer_unavailable")
        return entity_id
