"""Canonical climate projection for Ecobee Unified."""

from __future__ import annotations

from datetime import date as dt_date
from datetime import time as dt_time
from math import isfinite
from typing import Any, NoReturn, override

import voluptuous as vol
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    SERVICE_CREATE_VACATION,
    SERVICE_DELETE_VACATION,
    SERVICE_RESUME_PROGRAM,
    SERVICE_SET_OCCUPANCY_MODES,
    SERVICE_SET_SENSORS_USED_IN_CLIMATE,
    SIGNAL_SNAPSHOT_UPDATED,
)
from .manager import MappingManager
from .models import MappingConfig, NormalizedSnapshot
from .runtime import EcobeeUnifiedConfigEntry

SUPPORTED_CONTROL_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    | ClimateEntityFeature.TARGET_HUMIDITY
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.TURN_OFF
    | ClimateEntityFeature.TURN_ON
)

ATTR_AUTO_AWAY = "auto_away"
ATTR_COOL_TEMP = "cool_temp"
ATTR_DEVICE_IDS = "device_ids"
ATTR_END_DATE = "end_date"
ATTR_END_TIME = "end_time"
ATTR_FAN_MIN_ON_TIME = "fan_min_on_time"
ATTR_FAN_MODE = "fan_mode"
ATTR_FOLLOW_ME = "follow_me"
ATTR_HEAT_TEMP = "heat_temp"
ATTR_PRESET_MODE = "preset_mode"
ATTR_START_DATE = "start_date"
ATTR_START_TIME = "start_time"
ATTR_VACATION_NAME = "vacation_name"

ECOBEE_BUILT_IN_PROFILE_NAMES = {
    "away": "Away",
    "home": "Home",
    "sleep": "Sleep",
}


def _date_string(value: Any) -> str:
    result = cv.string(value)
    try:
        if len(result) != 10:
            raise ValueError
        dt_date.fromisoformat(result)
    except ValueError as err:
        raise vol.Invalid("Date must use YYYY-MM-DD") from err
    return result


def _time_string(value: Any) -> str:
    result = cv.string(value)
    try:
        if len(result) != 8:
            raise ValueError
        dt_time.fromisoformat(result)
    except ValueError as err:
        raise vol.Invalid("Time must use HH:MM:SS") from err
    return result


CREATE_VACATION_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Required(ATTR_VACATION_NAME): vol.All(cv.string, vol.Length(min=1, max=12)),
    vol.Required(ATTR_COOL_TEMP): vol.Coerce(float),
    vol.Required(ATTR_HEAT_TEMP): vol.Coerce(float),
    vol.Inclusive(ATTR_START_DATE, "start"): _date_string,
    vol.Inclusive(ATTR_START_TIME, "start"): _time_string,
    vol.Inclusive(ATTR_END_DATE, "end"): _date_string,
    vol.Inclusive(ATTR_END_TIME, "end"): _time_string,
    vol.Optional(ATTR_FAN_MODE, default="auto"): vol.In({"auto", "on"}),
    vol.Optional(ATTR_FAN_MIN_ON_TIME, default=0): vol.All(
        int, vol.Range(min=0, max=60)
    ),
}
DELETE_VACATION_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Required(ATTR_VACATION_NAME): vol.All(cv.string, vol.Length(min=1, max=12))
}
SET_OCCUPANCY_MODES_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Optional(ATTR_AUTO_AWAY): cv.boolean,
    vol.Optional(ATTR_FOLLOW_ME): cv.boolean,
}
SET_SENSORS_USED_IN_CLIMATE_SCHEMA: dict[str | vol.Marker, Any] = {
    vol.Optional(ATTR_PRESET_MODE): vol.All(cv.string, vol.Length(min=1, max=64)),
    vol.Required(ATTR_DEVICE_IDS): vol.All(
        cv.ensure_list,
        [cv.string],
        vol.Length(min=1, max=32),
    ),
}


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
        {},
        "async_resume_program",
    )
    platform.async_register_entity_service(
        SERVICE_CREATE_VACATION,
        CREATE_VACATION_SCHEMA,
        "async_create_vacation",
    )
    platform.async_register_entity_service(
        SERVICE_DELETE_VACATION,
        DELETE_VACATION_SCHEMA,
        "async_delete_vacation",
    )
    platform.async_register_entity_service(
        SERVICE_SET_OCCUPANCY_MODES,
        SET_OCCUPANCY_MODES_SCHEMA,
        "async_set_occupancy_modes",
    )
    platform.async_register_entity_service(
        SERVICE_SET_SENSORS_USED_IN_CLIMATE,
        SET_SENSORS_USED_IN_CLIMATE_SCHEMA,
        "async_set_sensors_used_in_climate",
    )


class EcobeeUnifiedClimate(ClimateEntity):
    """One no-I/O projection of a normalized thermostat snapshot."""

    _attr_has_entity_name = True
    _attr_translation_key = "thermostat"
    _unrecorded_attributes = frozenset(
        {"active_comfort_sensors", "command_confirmation"}
    )

    def __init__(self, manager: MappingManager, mapping: MappingConfig) -> None:
        self._manager = manager
        self._mapping = mapping
        self._attr_unique_id = mapping.mapping_id
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
    def precision(self) -> float:
        """Preserve fractional precision from an explicitly selected source."""

        if self._snapshot.provenance.get("current_temperature") in {
            "homekit_temperature",
            "ecobee",
        }:
            return PRECISION_TENTHS
        return super().precision

    @property
    @override
    def current_humidity(self) -> float | None:
        return self._snapshot.current_humidity

    @property
    @override
    def target_humidity(self) -> float | None:
        return self._snapshot.target_humidity

    @property
    @override
    def min_humidity(self) -> float:
        return (
            self._snapshot.min_humidity
            if self._snapshot.min_humidity is not None
            else super().min_humidity
        )

    @property
    @override
    def max_humidity(self) -> float:
        return (
            self._snapshot.max_humidity
            if self._snapshot.max_humidity is not None
            else super().max_humidity
        )

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
        features = ClimateEntityFeature(0)
        if self._snapshot.homekit_writable:
            features = (
                ClimateEntityFeature(self._snapshot.supported_features)
                & SUPPORTED_CONTROL_FEATURES
            )
        if self._snapshot.homekit_preset_writable and self._snapshot.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        return features

    @property
    @override
    def preset_mode(self) -> str | None:
        return self._snapshot.preset_mode

    @property
    @override
    def preset_modes(self) -> list[str]:
        return list(self._snapshot.preset_modes)

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
            "selected_sources": dict(snapshot.provenance),
            "degradation": list(snapshot.degradation),
            "ecobee_preset_mode": snapshot.ecobee_preset_mode,
            "ecobee_climate_mode": snapshot.climate_mode,
            "active_comfort_sensors": list(snapshot.active_sensors),
            "command_confirmation": {
                "revision": snapshot.command.revision,
                "operation": snapshot.command.operation,
                "status": snapshot.command.status.value,
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

    async def async_set_humidity(self, humidity: int) -> None:
        self._require_feature(ClimateEntityFeature.TARGET_HUMIDITY)
        target_humidity = self._validated_humidity(humidity)
        await self._manager.async_standard_command(
            self._mapping.mapping_id,
            "set_humidity",
            {"humidity": target_humidity},
            {"target_humidity": target_humidity},
            self._context,
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._require_feature(ClimateEntityFeature.PRESET_MODE)
        if preset_mode not in self._snapshot.preset_modes:
            self._raise_validation("unsupported_preset_mode")
        await self._manager.async_set_preset_mode(
            self._mapping.mapping_id, preset_mode, self._context
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

    async def async_resume_program(self) -> None:
        await self._manager.async_resume_program(
            self._mapping.mapping_id, self._context
        )

    async def async_create_vacation(
        self,
        vacation_name: str,
        cool_temp: float,
        heat_temp: float,
        start_date: str | None = None,
        start_time: str | None = None,
        end_date: str | None = None,
        end_time: str | None = None,
        fan_mode: str = "auto",
        fan_min_on_time: int = 0,
    ) -> None:
        """Create one mapped Ecobee vacation without claiming confirmation."""

        name = vacation_name.strip()
        if not name or len(name) > 12:
            self._raise_validation("invalid_vacation_name")
        cool = self._validated_vacation_temperature(cool_temp)
        heat = self._validated_vacation_temperature(heat_temp)
        if heat > cool:
            self._raise_validation("invalid_vacation_temperature_range")
        if fan_mode not in {"auto", "on"} or isinstance(fan_min_on_time, bool):
            self._raise_validation("invalid_vacation_options")
        if not isinstance(fan_min_on_time, int) or not 0 <= fan_min_on_time <= 60:
            self._raise_validation("invalid_vacation_options")
        if (start_date is None) != (start_time is None) or (end_date is None) != (
            end_time is None
        ):
            self._raise_validation("invalid_vacation_period")
        try:
            if start_date is not None and start_time is not None:
                _date_string(start_date)
                _time_string(start_time)
            if end_date is not None and end_time is not None:
                _date_string(end_date)
                _time_string(end_time)
        except vol.Invalid:
            self._raise_validation("invalid_vacation_period")
        if all(
            value is not None for value in (start_date, start_time, end_date, end_time)
        ):
            assert start_date is not None
            assert start_time is not None
            assert end_date is not None
            assert end_time is not None
            start = (
                dt_date.fromisoformat(start_date),
                dt_time.fromisoformat(start_time),
            )
            end = (dt_date.fromisoformat(end_date), dt_time.fromisoformat(end_time))
            if end <= start:
                self._raise_validation("invalid_vacation_period")
        service_data: dict[str, Any] = {
            ATTR_VACATION_NAME: name,
            ATTR_COOL_TEMP: cool,
            ATTR_HEAT_TEMP: heat,
            ATTR_FAN_MODE: fan_mode,
            ATTR_FAN_MIN_ON_TIME: fan_min_on_time,
        }
        service_data.update(
            {
                key: value
                for key, value in (
                    (ATTR_START_DATE, start_date),
                    (ATTR_START_TIME, start_time),
                    (ATTR_END_DATE, end_date),
                    (ATTR_END_TIME, end_time),
                )
                if value is not None
            }
        )
        await self._manager.async_vendor_action(
            self._mapping.mapping_id,
            SERVICE_CREATE_VACATION,
            service_data,
            self._context,
        )

    async def async_delete_vacation(self, vacation_name: str) -> None:
        """Delete one named mapped Ecobee vacation exactly once."""

        name = vacation_name.strip()
        if not name or len(name) > 12:
            self._raise_validation("invalid_vacation_name")
        await self._manager.async_vendor_action(
            self._mapping.mapping_id,
            SERVICE_DELETE_VACATION,
            {ATTR_VACATION_NAME: name},
            self._context,
        )

    async def async_set_occupancy_modes(
        self,
        auto_away: bool | None = None,
        follow_me: bool | None = None,
    ) -> None:
        """Set at least one mapped Ecobee occupancy policy exactly once."""

        if auto_away is None and follow_me is None:
            self._raise_validation("invalid_occupancy_modes")
        if any(
            value is not None and not isinstance(value, bool)
            for value in (auto_away, follow_me)
        ):
            self._raise_validation("invalid_occupancy_modes")
        service_data = {
            key: value
            for key, value in (
                (ATTR_AUTO_AWAY, auto_away),
                (ATTR_FOLLOW_ME, follow_me),
            )
            if value is not None
        }
        await self._manager.async_vendor_action(
            self._mapping.mapping_id,
            SERVICE_SET_OCCUPANCY_MODES,
            service_data,
            self._context,
        )

    async def async_set_sensors_used_in_climate(
        self,
        device_ids: list[str],
        preset_mode: str | None = None,
    ) -> None:
        """Set explicit Ecobee sensor participation exactly once."""

        if (
            not 1 <= len(device_ids) <= 32
            or len(device_ids) != len(set(device_ids))
            or any(not item for item in device_ids)
        ):
            self._raise_validation("invalid_sensor_selection")
        if not self._manager.ecobee_sensor_devices_valid(
            self._mapping.mapping_id, device_ids
        ):
            self._raise_validation("invalid_sensor_selection")
        service_data: dict[str, Any] = {ATTR_DEVICE_IDS: list(device_ids)}
        requested_preset = (
            preset_mode.strip()
            if preset_mode is not None
            else self._snapshot.climate_mode
        )
        if not requested_preset or len(requested_preset) > 64:
            self._raise_validation("invalid_sensor_selection")
        service_data[ATTR_PRESET_MODE] = ECOBEE_BUILT_IN_PROFILE_NAMES.get(
            requested_preset.casefold(), requested_preset
        )
        await self._manager.async_vendor_action(
            self._mapping.mapping_id,
            SERVICE_SET_SENSORS_USED_IN_CLIMATE,
            service_data,
            self._context,
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

    def _validated_humidity(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int | float):
            self._raise_validation("invalid_humidity")
        humidity = float(value)
        if not isfinite(humidity) or not humidity.is_integer():
            self._raise_validation("invalid_humidity")
        if (
            self._snapshot.min_humidity is None
            or self._snapshot.max_humidity is None
            or humidity < self._snapshot.min_humidity
            or humidity > self._snapshot.max_humidity
        ):
            self._raise_validation("invalid_humidity")
        return int(humidity)

    def _validated_vacation_temperature(self, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            self._raise_validation("invalid_vacation_temperature")
        temperature = float(value)
        if not isfinite(temperature):
            self._raise_validation("invalid_vacation_temperature")
        minimum = self._snapshot.ecobee_min_temp
        maximum = self._snapshot.ecobee_max_temp
        unit = self._snapshot.ecobee_temperature_unit
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
