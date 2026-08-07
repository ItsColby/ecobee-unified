"""Canonical climate projection for Ecobee Unified."""

from __future__ import annotations

from math import isfinite
from typing import Any, override

import voluptuous as vol
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ATTR_MINUTES,
    ATTR_RESUME_ALL,
    DOMAIN,
    SERVICE_RESUME_PROGRAM,
    SERVICE_SET_MINIMUM_FAN_RUNTIME,
    SIGNAL_SNAPSHOT_UPDATED,
)
from .manager import MappingManager
from .models import MappingConfig, NormalizedSnapshot
from .runtime import EcobeeUnifiedConfigEntry

SUPPORTED_CONTROL_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.TURN_OFF
    | ClimateEntityFeature.TURN_ON
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcobeeUnifiedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one canonical climate entity per explicit mapping."""

    manager = entry.runtime_data.manager
    async_add_entities(
        EcobeeUnifiedClimate(manager, mapping) for mapping in manager.mappings
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_RESUME_PROGRAM,
        {vol.Optional(ATTR_RESUME_ALL, default=False): cv.boolean},
        "async_resume_program",
    )
    platform.async_register_entity_service(
        SERVICE_SET_MINIMUM_FAN_RUNTIME,
        {vol.Required(ATTR_MINUTES): vol.All(cv.positive_int, vol.Range(max=60))},
        "async_set_minimum_fan_runtime",
    )


class EcobeeUnifiedClimate(ClimateEntity):
    """One no-I/O projection of a normalized thermostat snapshot."""

    _attr_has_entity_name = True
    _attr_translation_key = "thermostat"
    _unrecorded_attributes = frozenset(
        {"active_comfort_sensors", "command_confirmation", "source_age_seconds"}
    )

    def __init__(self, manager: MappingManager, mapping: MappingConfig) -> None:
        self._manager = manager
        self._mapping = mapping
        self._attr_unique_id = mapping.mapping_id
        self._attr_name = mapping.name
        source_entity_id = manager.resolve_entity_id(mapping.homekit_entity)
        self.device_entry = (
            async_entity_id_to_device(manager.hass, source_entity_id)
            if source_entity_id
            else None
        )

    @property
    def _snapshot(self) -> NormalizedSnapshot:
        return self._manager.snapshot(self._mapping.mapping_id)

    async def async_added_to_hass(self) -> None:
        """Subscribe the entity to its mapping's normalized snapshot."""

        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_SNAPSHOT_UPDATED}_{self._mapping.mapping_id}",
                self.async_write_ha_state,
            )
        )

    @property
    @override
    def available(self) -> bool:
        return self._snapshot.available

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        value = self._snapshot.hvac_mode
        try:
            return HVACMode(value) if value else None
        except ValueError:
            return None

    @property
    @override
    def hvac_modes(self) -> list[HVACMode]:
        result: list[HVACMode] = []
        for item in self._snapshot.hvac_modes:
            try:
                result.append(HVACMode(item))
            except ValueError:
                continue
        return result

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        value = self._snapshot.hvac_action
        try:
            return HVACAction(value) if value else None
        except ValueError:
            return None

    @property
    @override
    def current_temperature(self) -> float | None:
        return self._snapshot.current_temperature

    @property
    @override
    def current_humidity(self) -> float | None:
        return self._snapshot.current_humidity

    @property
    @override
    def target_temperature(self) -> float | None:
        return self._snapshot.target_temperature

    @property
    @override
    def target_temperature_low(self) -> float | None:
        return self._snapshot.target_temperature_low

    @property
    @override
    def target_temperature_high(self) -> float | None:
        return self._snapshot.target_temperature_high

    @property
    @override
    def fan_mode(self) -> str | None:
        return self._snapshot.fan_mode

    @property
    @override
    def fan_modes(self) -> list[str]:
        return list(self._snapshot.fan_modes)

    @property
    @override
    def supported_features(self) -> ClimateEntityFeature:
        if not self._snapshot.homekit_writable:
            return ClimateEntityFeature(0)
        return (
            ClimateEntityFeature(self._snapshot.supported_features)
            & SUPPORTED_CONTROL_FEATURES
        )

    @property
    @override
    def min_temp(self) -> float:
        return (
            self._snapshot.min_temp
            if self._snapshot.min_temp is not None
            else super().min_temp
        )

    @property
    @override
    def max_temp(self) -> float:
        return (
            self._snapshot.max_temp
            if self._snapshot.max_temp is not None
            else super().max_temp
        )

    @property
    @override
    def target_temperature_step(self) -> float | None:
        return self._snapshot.target_temperature_step

    @property
    @override
    def temperature_unit(self) -> str:
        return (
            self._snapshot.temperature_unit or self.hass.config.units.temperature_unit
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self._snapshot
        return {
            "source_health": {
                key: value.value for key, value in snapshot.source_health.items()
            },
            "source_age_seconds": dict(snapshot.source_ages),
            "selected_sources": dict(snapshot.provenance),
            "degradation": list(snapshot.degradation),
            "ecobee_preset_mode": snapshot.preset_mode,
            "ecobee_climate_mode": snapshot.climate_mode,
            "equipment_running": snapshot.equipment_running,
            "active_comfort_sensors": list(snapshot.active_sensors),
            "minimum_fan_runtime": snapshot.minimum_fan_runtime,
            "scheduled_profile": snapshot.scheduled_profile,
            "next_transition": snapshot.next_transition,
            "command_confirmation": {
                "revision": snapshot.command.revision,
                "operation": snapshot.command.operation,
                "status": snapshot.command.status.value,
                "age_seconds": snapshot.command.age_seconds,
            },
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode.value not in self._snapshot.hvac_modes:
            self._raise_validation("unsupported_hvac_mode")
        await self._manager.async_standard_command(
            self._mapping.mapping_id,
            "set_hvac_mode",
            {"hvac_mode": hvac_mode.value},
            {"hvac_mode": hvac_mode},
            self._context,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        expected: dict[str, Any] = {}
        if ATTR_TEMPERATURE in kwargs:
            self._require_feature(ClimateEntityFeature.TARGET_TEMPERATURE)
            expected["target_temperature"] = self._validated_temperature(
                kwargs[ATTR_TEMPERATURE]
            )
        if "target_temp_low" in kwargs:
            self._require_feature(ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)
            expected["target_temperature_low"] = self._validated_temperature(
                kwargs["target_temp_low"]
            )
        if "target_temp_high" in kwargs:
            self._require_feature(ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)
            expected["target_temperature_high"] = self._validated_temperature(
                kwargs["target_temp_high"]
            )
        low = expected.get("target_temperature_low")
        high = expected.get("target_temperature_high")
        if low is not None and high is not None and low > high:
            self._raise_validation("invalid_temperature_range")
        if "hvac_mode" in kwargs:
            mode = kwargs["hvac_mode"]
            mode_value = mode.value if isinstance(mode, HVACMode) else str(mode)
            if mode_value not in self._snapshot.hvac_modes:
                self._raise_validation("unsupported_hvac_mode")
            expected["hvac_mode"] = mode_value
        await self._manager.async_standard_command(
            self._mapping.mapping_id,
            "set_temperature",
            kwargs,
            expected,
            self._context,
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._require_feature(ClimateEntityFeature.FAN_MODE)
        if fan_mode not in self._snapshot.fan_modes:
            self._raise_validation("unsupported_fan_mode")
        await self._manager.async_standard_command(
            self._mapping.mapping_id,
            "set_fan_mode",
            {"fan_mode": fan_mode},
            {"fan_mode": fan_mode},
            self._context,
        )

    async def async_turn_off(self) -> None:
        self._require_feature(ClimateEntityFeature.TURN_OFF)
        await self._manager.async_standard_command(
            self._mapping.mapping_id,
            "turn_off",
            {},
            {"hvac_mode": HVACMode.OFF.value},
            self._context,
        )

    async def async_turn_on(self) -> None:
        self._require_feature(ClimateEntityFeature.TURN_ON)
        await self._manager.async_standard_command(
            self._mapping.mapping_id,
            "turn_on",
            {},
            {"hvac_mode": "__not_off__"},
            self._context,
        )

    async def async_resume_program(self, resume_all: bool) -> None:
        await self._manager.async_resume_program(
            self._mapping.mapping_id, resume_all, self._context
        )

    async def async_set_minimum_fan_runtime(self, minutes: int) -> None:
        await self._manager.async_set_minimum_fan_runtime(
            self._mapping.mapping_id, minutes, self._context
        )

    def _require_feature(self, feature: ClimateEntityFeature) -> None:
        if not self.supported_features & feature:
            self._raise_validation("unsupported_command")

    def _validated_temperature(self, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            self._raise_validation("invalid_temperature")
        temperature = float(value)
        if not isfinite(temperature):
            self._raise_validation("invalid_temperature")
        if (
            self._snapshot.min_temp is not None
            and temperature < self._snapshot.min_temp
        ) or (
            self._snapshot.max_temp is not None
            and temperature > self._snapshot.max_temp
        ):
            self._raise_validation("invalid_temperature")
        return temperature

    @staticmethod
    def _raise_validation(translation_key: str) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
        )
