"""Ecobee Unified actions, registered once for the integration."""

from __future__ import annotations

from datetime import date as dt_date
from datetime import time as dt_time
from typing import Any

import probatio
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_platform_entity_service

from .const import (
    DOMAIN,
    MAX_FAN_RUNTIME_MINUTES,
    MIN_FAN_RUNTIME_MINUTES,
    SERVICE_CREATE_VACATION,
    SERVICE_DELETE_VACATION,
    SERVICE_RESUME_PROGRAM,
    SERVICE_SET_OCCUPANCY_MODES,
    SERVICE_SET_SENSORS_USED_IN_CLIMATE,
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


def date_string(value: Any) -> str:
    """Validate an ISO date in Ecobee's YYYY-MM-DD form."""
    result = cv.string(value)
    try:
        if dt_date.fromisoformat(result).isoformat() != result:
            raise ValueError
    except ValueError as err:
        raise probatio.Invalid("Date must use YYYY-MM-DD") from err
    return result


def time_string(value: Any) -> str:
    """Validate a time in Ecobee's HH:MM:SS form."""
    result = cv.string(value)
    try:
        if len(result) != 8 or dt_time.fromisoformat(result).isoformat() != result:
            raise ValueError
    except ValueError as err:
        raise probatio.Invalid("Time must use HH:MM:SS") from err
    return result


CREATE_VACATION_SCHEMA: dict[str | probatio.Marker, Any] = {
    probatio.Required(ATTR_VACATION_NAME): probatio.All(
        cv.string, str.strip, probatio.Length(min=1, max=12)
    ),
    probatio.Required(ATTR_COOL_TEMP): probatio.Coerce(float),
    probatio.Required(ATTR_HEAT_TEMP): probatio.Coerce(float),
    probatio.Inclusive(ATTR_START_DATE, "start"): date_string,
    probatio.Inclusive(ATTR_START_TIME, "start"): time_string,
    probatio.Inclusive(ATTR_END_DATE, "end"): date_string,
    probatio.Inclusive(ATTR_END_TIME, "end"): time_string,
    probatio.Optional(ATTR_FAN_MODE, default="auto"): probatio.In({"auto", "on"}),
    probatio.Optional(ATTR_FAN_MIN_ON_TIME, default=0): probatio.All(
        int, probatio.Range(min=MIN_FAN_RUNTIME_MINUTES, max=MAX_FAN_RUNTIME_MINUTES)
    ),
}
DELETE_VACATION_SCHEMA: dict[str | probatio.Marker, Any] = {
    probatio.Required(ATTR_VACATION_NAME): probatio.All(
        cv.string, str.strip, probatio.Length(min=1, max=12)
    )
}
SET_OCCUPANCY_MODES_SCHEMA: dict[str | probatio.Marker, Any] = {
    probatio.Optional(ATTR_AUTO_AWAY): cv.boolean,
    probatio.Optional(ATTR_FOLLOW_ME): cv.boolean,
}
SET_SENSORS_USED_IN_CLIMATE_SCHEMA: dict[str | probatio.Marker, Any] = {
    probatio.Optional(ATTR_PRESET_MODE): probatio.All(
        cv.string, str.strip, probatio.Length(min=1, max=64)
    ),
    probatio.Required(ATTR_DEVICE_IDS): probatio.All(
        cv.ensure_list,
        [cv.string],
        probatio.Length(min=1, max=32),
    ),
}

SERVICES: tuple[tuple[str, dict[str | probatio.Marker, Any], str], ...] = (
    (SERVICE_RESUME_PROGRAM, {}, "async_resume_program"),
    (SERVICE_CREATE_VACATION, CREATE_VACATION_SCHEMA, "async_create_vacation"),
    (SERVICE_DELETE_VACATION, DELETE_VACATION_SCHEMA, "async_delete_vacation"),
    (
        SERVICE_SET_OCCUPANCY_MODES,
        SET_OCCUPANCY_MODES_SCHEMA,
        "async_set_occupancy_modes",
    ),
    (
        SERVICE_SET_SENSORS_USED_IN_CLIMATE,
        SET_SENSORS_USED_IN_CLIMATE_SCHEMA,
        "async_set_sensors_used_in_climate",
    ),
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the climate actions, available before any entry loads."""
    for name, schema, method in SERVICES:
        async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=CLIMATE_DOMAIN,
            func=method,
            schema=schema,
        )
