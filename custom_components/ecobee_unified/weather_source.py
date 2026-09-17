"""Read seven scalar roles from explicitly paired native Ecobee weather feeds.

Core 2026.9.2 renders the provider station and UTC forecast timestamp in attribution:
https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/ecobee/weather.py
The provider timestamp is not a physical measurement time or an HA receipt time.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from homeassistant.components.weather.const import (
    VALID_UNITS_PRECIPITATION,
    VALID_UNITS_PRESSURE,
    VALID_UNITS_VISIBILITY,
    VALID_UNITS_WIND_SPEED,
)
from homeassistant.core import State
from homeassistant.helpers.entity_registry import RegistryEntry
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

WEATHER_ROLES = frozenset(
    {
        "condition",
        "temperature",
        "humidity",
        "pressure",
        "wind_bearing",
        "wind_speed",
        "visibility",
    }
)
_TEMPERATURE_UNITS = frozenset({"°C", "°F", "K"})
_UNIT_CONTRACTS = {
    "temperature": ("temperature_unit", _TEMPERATURE_UNITS),
    "pressure": ("pressure_unit", VALID_UNITS_PRESSURE),
    "wind_speed": ("wind_speed_unit", VALID_UNITS_WIND_SPEED),
    "visibility": ("visibility_unit", VALID_UNITS_VISIBILITY),
}
_ATTRIBUTION = re.compile(
    r"Ecobee weather provided by (?P<station>[^\r\n]{1,128}) at (?P<time>[^\r\n]{1,32}) UTC"
)
_PROVIDER_TIME = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


@dataclass(frozen=True, slots=True)
class WeatherFeedIdentity:
    """Same provider entry and station; no permanent geographic identity claim."""

    config_entry_id: str
    station: str


@dataclass(frozen=True, slots=True)
class WeatherObservation:
    """A selected scalar with separate provider forecast and HA receipt clocks."""

    value: float | str
    unit: str | None
    station: str
    provider_reported_at: datetime
    ha_reported_at: datetime


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    """One coherent native weather report, suitable for a WeatherEntity projection."""

    condition: str | None
    temperature: float | None
    humidity: float | None
    pressure: float | None
    wind_bearing: float | None
    wind_speed: float | None
    visibility: float | None
    temperature_unit: str | None
    pressure_unit: str | None
    wind_speed_unit: str | None
    visibility_unit: str | None
    precipitation_unit: str | None
    supported_features: int
    station: str
    provider_reported_at: datetime
    ha_reported_at: datetime


def weather_snapshot(
    entry: RegistryEntry,
    state: State | None,
    *,
    expected_station: str,
    expected_config_entry_id: str,
    now: datetime | None = None,
) -> WeatherSnapshot:
    """Validate all present current fields without fabricating missing readings."""
    identity, provider_time = _source_metadata(entry, state, now or dt_util.utcnow())
    _validate_identity(identity, expected_station, expected_config_entry_id)
    assert state is not None
    values: dict[str, Any] = {}
    units: dict[str, Any] = {}
    for role in WEATHER_ROLES:
        if role == "condition":
            values[role] = (
                None if state.state == "unknown" else _scalar(state, role, None)[0]
            )
        elif state.attributes.get(role) is None:
            values[role] = None
        else:
            values[role] = _scalar(state, role, None)[0]
    for role, (key, allowed) in _UNIT_CONTRACTS.items():
        unit = state.attributes.get(key)
        if unit is not None and (not isinstance(unit, str) or unit not in allowed):
            raise ValueError("weather_unit_invalid")
        units[f"{role}_unit"] = unit
    precipitation_unit = state.attributes.get("precipitation_unit")
    if precipitation_unit is not None and (
        not isinstance(precipitation_unit, str)
        or precipitation_unit not in VALID_UNITS_PRECIPITATION
    ):
        raise ValueError("weather_unit_invalid")
    features = state.attributes.get("supported_features", 0)
    if isinstance(features, bool) or not isinstance(features, int) or features < 0:
        raise ValueError("weather_source_invalid")
    return WeatherSnapshot(
        **values,
        **units,
        precipitation_unit=precipitation_unit,
        supported_features=features,
        station=identity.station,
        provider_reported_at=provider_time,
        ha_reported_at=state.last_reported,
    )


def _source_metadata(
    entry: RegistryEntry, state: State | None, now: datetime
) -> tuple[WeatherFeedIdentity, datetime]:
    if (
        entry.platform != "ecobee"
        or entry.domain != "weather"
        or not entry.config_entry_id
        or not entry.device_id
    ):
        raise ValueError("weather_source_invalid")
    if entry.disabled_by is not None:
        raise ValueError("source_disabled")
    if state is None:
        raise ValueError("source_missing")
    if state.entity_id != entry.entity_id:
        raise ValueError("weather_source_invalid")
    if state.state == "unavailable":
        raise ValueError("unavailable")
    attribution = state.attributes.get("attribution")
    match = (
        _ATTRIBUTION.fullmatch(attribution) if isinstance(attribution, str) else None
    )
    if match is None:
        raise ValueError("weather_attribution_invalid")
    station = match["station"]
    if (
        station != station.strip()
        or not station.isprintable()
        or station.casefold() in {"unknown", "none", "unavailable"}
    ):
        raise ValueError("weather_station_unknown")
    provider_text = match["time"]
    if not _PROVIDER_TIME.fullmatch(provider_text):
        raise ValueError("weather_timestamp_invalid")
    try:
        timestamp = datetime.strptime(provider_text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=UTC
        )
    except ValueError as err:
        raise ValueError("weather_timestamp_invalid") from err
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("weather_timestamp_invalid")
    if timestamp > now + timedelta(seconds=60):
        raise ValueError("weather_timestamp_future")
    return WeatherFeedIdentity(entry.config_entry_id, station), timestamp


def validate_weather_feed(
    sources: Sequence[tuple[RegistryEntry, State | None]],
    *,
    expected_station: str | None = None,
    role: str | None = None,
    output_unit: str | None = None,
    now: datetime | None = None,
) -> WeatherFeedIdentity:
    """Prove the selected aliases share one exact station within one native entry.

    A role optionally validates the complete selected scalar, including its unit.
    Source timestamps may differ; this function makes no simultaneous agreement claim.
    """
    if not sources:
        raise ValueError("source_missing")
    if len({entry.id for entry, _state in sources}) != len(sources):
        raise ValueError("weather_source_duplicate")
    current_time = now or dt_util.utcnow()
    identity, _timestamp = _source_metadata(*sources[0], current_time)
    if expected_station is not None and identity.station != expected_station:
        raise ValueError("weather_station_mismatch")
    for entry, state in sources:
        actual, _timestamp = _source_metadata(entry, state, current_time)
        _validate_identity(actual, identity.station, identity.config_entry_id)
        if role is not None:
            weather_observation(
                entry,
                state,
                role,
                expected_station=identity.station,
                expected_config_entry_id=identity.config_entry_id,
                output_unit=output_unit,
                now=current_time,
            )
    return identity


def _validate_identity(
    identity: WeatherFeedIdentity, expected_station: str, expected_config_entry_id: str
) -> None:
    if identity.config_entry_id != expected_config_entry_id:
        raise ValueError("weather_config_entry_mismatch")
    if identity.station != expected_station:
        raise ValueError("weather_station_mismatch")


def weather_observation(
    entry: RegistryEntry,
    state: State | None,
    role: str,
    *,
    expected_station: str,
    expected_config_entry_id: str,
    output_unit: str | None = None,
    now: datetime | None = None,
) -> WeatherObservation:
    """Read one coherent native state; revalidate the persisted feed every time."""
    if role not in WEATHER_ROLES:
        raise ValueError("invalid_weather_role")
    identity, provider_time = _source_metadata(entry, state, now or dt_util.utcnow())
    _validate_identity(identity, expected_station, expected_config_entry_id)
    assert state is not None
    value, unit = _scalar(state, role, output_unit)
    return WeatherObservation(
        value,
        unit,
        identity.station,
        provider_time,
        state.last_reported,
    )


def _scalar(
    state: State, role: str, output_unit: str | None
) -> tuple[float | str, str | None]:
    if role == "condition":
        if state.state == "unknown":
            raise ValueError("unknown")
        if (
            not state.state.strip()
            or len(state.state) > 64
            or not state.state.isprintable()
        ):
            raise ValueError("weather_value_invalid")
        if output_unit is not None:
            raise ValueError("weather_unit_mismatch")
        return state.state, None
    if role not in state.attributes or state.attributes[role] is None:
        raise ValueError("weather_value_missing")
    value = _number(state.attributes[role])
    unit = _unit(state, role)
    if role == "temperature":
        return _temperature(value, unit, output_unit)
    if output_unit is not None and output_unit != unit:
        raise ValueError("weather_unit_mismatch")
    if (
        value < 0
        or (role == "humidity" and value > 100)
        or (role == "wind_bearing" and value > 360)
        or (role == "pressure" and value == 0)
    ):
        raise ValueError("weather_value_invalid")
    return value, unit


def _unit(state: State, role: str) -> str:
    if role == "humidity":
        return "%"
    if role == "wind_bearing":
        return "°"
    key, allowed = _UNIT_CONTRACTS[role]
    unit = state.attributes.get(key)
    if not isinstance(unit, str) or unit not in allowed:
        raise ValueError("weather_unit_invalid")
    return unit


def _number(raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueError("weather_value_invalid")  # noqa: TRY004 -- Bounded public error contract.
    try:
        value = float(raw)
    except (ValueError, TypeError, OverflowError) as err:
        raise ValueError("weather_value_invalid") from err
    if not isfinite(value):
        raise ValueError("weather_value_invalid")
    return value


def _temperature(
    value: float, source_unit: str, output_unit: str | None
) -> tuple[float, str]:
    target_unit = source_unit if output_unit is None else output_unit
    if target_unit not in _TEMPERATURE_UNITS:
        raise ValueError("weather_unit_invalid")
    try:
        kelvin = TemperatureConverter.convert(value, source_unit, "K")
        converted = TemperatureConverter.convert(value, source_unit, target_unit)
    except (ValueError, OverflowError) as err:
        raise ValueError("weather_value_invalid") from err
    if not isfinite(kelvin) or kelvin < 0 or not isfinite(converted):
        raise ValueError("weather_value_invalid")
    return converted, target_unit
