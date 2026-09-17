"""Capture native historical source identity without acquiring provider history.

Beestat's public cached configuration proves its effective owner association,
not provider serial identity. The configuration flow must confirm that binding.
Legacy daily aggregates and native Recorder means retain different methods.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_list_statistic_ids
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .datapoints import DatapointConfig, datapoint_observation_role
from .weather_source import validate_weather_feed

_OWNERS = frozenset({"ecobee", "homekit_controller", "ecobee_unified", "battery_notes"})
_CLASSES = {
    "temperature": "temperature",
    "humidity": "humidity",
    "co2": "carbon_dioxide",
    "aqi": "aqi",
    "voc": "volatile_organic_compounds",
    "battery": "battery",
    "weather_temperature": "temperature",
    "weather_humidity": "humidity",
}
_SENSOR_SPECS = {
    "temperature": ("temperature", "include_temperature", "°F"),
    "co2": ("co2_concentration", "include_co2", "ppm"),
    "aqi": ("air_quality", "include_air_quality", "%"),
    "voc": ("voc_concentration", "include_voc", "ppb"),
}
SOURCE_TIMING_FIELDS = frozenset(
    {
        "provider_data_through",
        "provider_data_through_ref",
        "provider_window_begin",
        "import_completed_at",
    }
)
SOURCE_CONTRACT_FIELDS = frozenset(
    {
        "statistic_id",
        "metadata",
        "method",
        "native_unit",
        "transformation",
        "identity_evidence",
        "entity_ref",
        "anchor_ref",
        "timezone",
        "settlement_basis",
    }
)


async def async_capture_source(
    hass: HomeAssistant,
    statistic_id: str,
    quantity: str,
    anchor_reference: str,
    *,
    context: Context | None = None,
    validation_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture one explicitly selected source's public identity and shape."""
    if quantity not in _CLASSES:
        raise ValueError("historical_unsupported_quantity")
    if not isinstance(statistic_id, str) or not 1 <= len(statistic_id) <= 255:
        raise ValueError("historical_unsupported_source")
    anchor = _entry(hass, anchor_reference)
    _validate_role(hass, anchor, quantity)
    anchor_identity = _entity_identity(hass, anchor)
    metadata = await _metadata(hass, statistic_id)
    _validate_unit(metadata, quantity, beestat=statistic_id.startswith("beestat:"))
    result: dict[str, Any] = {
        "statistic_id": statistic_id,
        "metadata": metadata,
        "native_unit": metadata["unit"],
        "anchor_ref": anchor.id,
        "timezone": hass.config.time_zone,
        "settlement_basis": None,
    }
    if statistic_id.startswith("beestat:"):
        result.update(
            await _beestat_source(
                hass,
                statistic_id,
                quantity,
                anchor_identity,
                context=context,
                validation_cache=validation_cache,
            )
        )
    elif statistic_id.startswith("sensor.") and metadata["source"] == "recorder":
        source_entry = _entry(hass, statistic_id)
        _validate_role(hass, source_entry, quantity)
        source_identity = _entity_identity(hass, source_entry)
        _require_equivalent(hass, source_identity, anchor_identity)
        result.update(
            {
                "entity_ref": source_entry.id,
                "method": "recorder_hourly_time_weighted",
                "transformation": (
                    "aqi_raw_div350_times100" if quantity == "aqi" else "identity"
                ),
                "identity_evidence": {
                    "basis": "native_device_identity",
                    "source": source_identity,
                    "anchor": anchor_identity,
                    "quantity": quantity,
                    "historical_continuity": "unknown",
                },
            }
        )
    else:
        raise ValueError("historical_unsupported_source")
    return result


async def async_validate_source(
    hass: HomeAssistant,
    source: dict[str, Any],
    quantity: str,
    anchor_reference: str,
    *,
    context: Context | None = None,
    validation_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Revalidate the saved contract; retain caller-owned IDs and extra fields."""
    current = await async_capture_source(
        hass,
        source.get("statistic_id", ""),
        quantity,
        anchor_reference,
        context=context,
        validation_cache=validation_cache,
    )
    if any(source.get(key) != current.get(key) for key in SOURCE_CONTRACT_FIELDS):
        raise ValueError("historical_source_changed")
    # Watermarks may disappear or advance without changing historical identity.
    refreshed = {
        key: value for key, value in source.items() if key not in SOURCE_TIMING_FIELDS
    }
    refreshed.update(current)
    return refreshed


async def _metadata(hass: HomeAssistant, statistic_id: str) -> dict[str, Any]:
    try:
        async with asyncio.timeout(10):
            rows = await async_list_statistic_ids(hass, statistic_ids={statistic_id})
    except HomeAssistantError, TimeoutError:
        raise ValueError("historical_source_metadata_unavailable") from None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("historical_source_metadata_invalid")
    matching = [row for row in rows if row.get("statistic_id") == statistic_id]
    if len(matching) != 1:
        raise ValueError("historical_source_metadata_unavailable")
    row = matching[0]
    mean_type = row.get("mean_type")
    if (
        isinstance(mean_type, bool)
        or not isinstance(mean_type, int)
        or mean_type != StatisticMeanType.ARITHMETIC
        or row.get("has_sum") is not False
        or "statistics_unit_of_measurement" not in row
        or "display_unit_of_measurement" not in row
        or any(
            row.get(key) is not None and not isinstance(row[key], str)
            for key in (
                "source",
                "unit_class",
                "statistics_unit_of_measurement",
                "display_unit_of_measurement",
            )
        )
    ):
        raise ValueError("historical_source_metadata_invalid")
    return {
        "source": row.get("source"),
        "unit": row["statistics_unit_of_measurement"],
        "display_unit": row["display_unit_of_measurement"],
        "mean_type": int(StatisticMeanType.ARITHMETIC),
        "unit_class": row.get("unit_class"),
    }


def _validate_unit(metadata: dict[str, Any], quantity: str, *, beestat: bool) -> None:
    quantity = quantity.removeprefix("weather_")
    allowed: set[str | None]
    unit_class = "unitless"
    if quantity == "temperature":
        allowed = {"°F"} if beestat else {"°C", "°F", "K"}
        unit_class = "temperature"
    elif quantity in {"humidity", "battery"}:
        allowed = {"%"}
    elif quantity == "co2":
        allowed = {"ppm"}
    elif quantity == "aqi":
        allowed = {"%"} if beestat else {None}
    else:
        allowed = {"ppb"} if beestat else {"μg/m³", "µg/m³"}
        unit_class = "unitless" if beestat else "concentration"
    display_allowed = {"°C", "°F", "K"} if quantity == "temperature" else allowed
    if (
        metadata["unit"] not in allowed
        or metadata["display_unit"] not in display_allowed
        or metadata["unit_class"] != unit_class
        or metadata["source"] != ("beestat" if beestat else "recorder")
    ):
        raise ValueError("historical_source_unit_mismatch")


def _entry(hass: HomeAssistant, reference: str) -> er.RegistryEntry:
    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, reference)
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None or entry.platform not in _OWNERS:
        raise ValueError("historical_source_unavailable")
    return entry


def _validate_role(hass: HomeAssistant, entry: er.RegistryEntry, quantity: str) -> None:
    if quantity.startswith("weather_"):
        try:
            validate_weather_feed(
                [(entry, hass.states.get(entry.entity_id))],
                role=quantity.removeprefix("weather_"),
            )
        except ValueError:
            raise ValueError("historical_source_role_mismatch") from None
        return
    state = hass.states.get(entry.entity_id)
    attributes = state.attributes if state else {}
    device_class = attributes.get(
        "device_class", entry.device_class or entry.original_device_class
    )
    if (
        entry.domain != "sensor"
        or device_class != _CLASSES[quantity]
        or (entry.platform == "battery_notes" and quantity != "battery")
    ):
        raise ValueError("historical_source_role_mismatch")
    unit = attributes.get("unit_of_measurement", entry.unit_of_measurement)
    allowed_units: dict[str, set[str | None]] = {
        "temperature": {"°C", "°F", "K"},
        "humidity": {"%"},
        "battery": {"%"},
        "co2": {"ppm"},
        "aqi": {None},
        "voc": {"μg/m³", "µg/m³"},
    }
    if not isinstance(unit, str | type(None)) or unit not in allowed_units[quantity]:
        raise ValueError("historical_source_unit_mismatch")
    if quantity == "humidity" and entry.platform == DOMAIN:
        _validate_composed_humidity(hass, entry)
    if quantity != "temperature":
        return
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    if not isinstance(device, dr.DeviceEntry):
        raise ValueError("historical_source_identity_unproven")  # noqa: TRY004 - public validation contract
    identity = _hardware_identity(hass, device)
    serial = identity["serial"]
    physical = (
        (
            entry.platform == "ecobee"
            and entry.unique_id
            in {f"{serial}-temperature", f"{serial}-ei:0-temperature"}
        )
        or (
            entry.platform == "homekit_controller"
            and device.model == "EBERS41"
            and not entry.unique_id.endswith("_1_16_19")
        )
        or (
            entry.platform == "ecobee_unified"
            and attributes.get("semantic") == "physical_temperature"
            and attributes.get("time_basis") == "current"
        )
    )
    if not physical:
        raise ValueError("historical_source_role_mismatch")


def _validate_composed_humidity(hass: HomeAssistant, entry: er.RegistryEntry) -> None:
    """Admit only proven current measurements from the exact saved output owner."""
    owner = (
        hass.config_entries.async_get_entry(entry.config_entry_id)
        if entry.config_entry_id
        else None
    )
    rows = owner.data.get("datapoints", []) if owner and owner.domain == DOMAIN else []
    matches = (
        [
            row
            for row in rows
            if isinstance(row, Mapping)
            and entry.unique_id
            == f"{entry.config_entry_id}_datapoint_{row.get('datapoint_id')}"
        ]
        if isinstance(rows, list)
        else []
    )
    if len(matches) != 1:
        raise ValueError("historical_source_role_mismatch")
    try:
        config = DatapointConfig.from_dict(matches[0])
    except KeyError, TypeError, ValueError:
        raise ValueError("historical_source_role_mismatch") from None
    if (
        config.kind != "humidity"
        or config.time_basis != "current"
        or datapoint_observation_role(hass, config) != "measured_humidity"
    ):
        raise ValueError("historical_source_role_mismatch")
    output = _entity_identity(hass, entry)
    for binding in config.sources:
        _require_equivalent(
            hass, _entity_identity(hass, _entry(hass, binding.entity)), output
        )


def _physical_serial(device: dr.DeviceEntry) -> tuple[str, list[str]]:
    """Normalize physical evidence without traversing device relationships."""
    serial = (device.serial_number or "").strip()
    identifiers = sorted(
        value.strip()
        for domain, value in device.identifiers
        if domain == "ecobee" and value.strip()
    )
    if len(identifiers) > 1 or (serial and identifiers and serial != identifiers[0]):
        raise ValueError("historical_source_identity_mismatch")
    if not identifiers and (
        not serial
        or (device.manufacturer or "").strip().casefold()
        not in {"ecobee", "ecobee inc.", "ecobee inc"}
    ):
        raise ValueError("historical_source_identity_unproven")
    return serial or identifiers[0], identifiers


def _parent_serial(hass: HomeAssistant, device: dr.DeviceEntry) -> str | None:
    if not device.via_device_id:
        return None
    parent = dr.async_get(hass).async_get(device.via_device_id)
    if not isinstance(parent, dr.DeviceEntry):
        raise ValueError("historical_source_identity_unproven")  # noqa: TRY004 - public validation contract
    return _physical_serial(parent)[0]


def _hardware_identity(hass: HomeAssistant, device: dr.DeviceEntry) -> dict[str, Any]:
    serial, identifiers = _physical_serial(device)
    return {
        "device_id": device.id,
        "serial": serial,
        "ecobee_identifiers": identifiers,
        "parent_device_id": device.via_device_id,
        "parent_serial": _parent_serial(hass, device),
    }


def _entity_identity(hass: HomeAssistant, entry: er.RegistryEntry) -> dict[str, Any]:
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    if not isinstance(device, dr.DeviceEntry):
        raise ValueError("historical_source_identity_unproven")  # noqa: TRY004 - public validation contract
    return {
        "registry_id": entry.id,
        "platform": entry.platform,
        "domain": entry.domain,
        "unique_id": entry.unique_id,
        "config_entry_id": entry.config_entry_id,
        "hardware": _hardware_identity(hass, device),
    }


def _require_equivalent(
    hass: HomeAssistant, first: dict[str, Any], second: dict[str, Any]
) -> None:
    left, right = first["hardware"], second["hardware"]
    if left["serial"] != right["serial"] or (
        left["parent_serial"]
        and right["parent_serial"]
        and left["parent_serial"] != right["parent_serial"]
    ):
        raise ValueError("historical_source_identity_mismatch")
    if left["device_id"] == right["device_id"]:
        return
    # A short remote serial cannot pick between thermostats when the cloud
    # registry omits its parent. Do not select one of multiple native parents.
    registry = dr.async_get(hass)
    # Core 2026.8 exposes a mapping; 2026.9 exposes a collection of entries.
    devices = registry.devices
    entries = devices.values() if isinstance(devices, Mapping) else devices
    parents = set()
    for device in entries:
        if not isinstance(device, dr.DeviceEntry) or not device.via_device_id:
            continue
        try:
            serial, _ = _physical_serial(device)
        except ValueError:
            # Unrelated registry devices need not expose an Ecobee identity.
            continue
        if serial == left["serial"] and (parent_serial := _parent_serial(hass, device)):
            parents.add(parent_serial)
    if len(parents) > 1:
        raise ValueError("historical_source_identity_unproven")


async def _beestat_source(
    hass: HomeAssistant,
    statistic_id: str,
    quantity: str,
    anchor: dict[str, Any],
    *,
    context: Context | None,
    validation_cache: dict[str, Any] | None,
) -> dict[str, Any]:
    entries = [
        entry
        for entry in hass.config_entries.async_entries("beestat_statistics")
        if entry.state is ConfigEntryState.LOADED
    ]
    if not 1 <= len(entries) <= 4:
        raise ValueError("historical_beestat_unavailable")
    matches: list[dict[str, Any]] = []
    for entry in entries:
        response = await _configuration(
            hass, entry.entry_id, context=context, validation_cache=validation_cache
        )
        for row, parent in _configured_matches(response, statistic_id, quantity):
            matches.append(
                _beestat_binding(hass, entry.entry_id, row, parent, quantity, anchor)
            )
    if len(matches) > 1:
        raise ValueError("historical_beestat_mapping_ambiguous")
    if not matches:
        raise ValueError("historical_unsupported_source")
    return matches[0]


async def _configuration(
    hass: HomeAssistant,
    entry_id: str,
    *,
    context: Context | None,
    validation_cache: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep only a bounded projection in the caller's single validation phase."""
    key = f"beestat_configuration:{entry_id}"
    if validation_cache is not None and key in validation_cache:
        cached = validation_cache[key]
        if isinstance(cached, dict):
            return cached
        raise ValueError("historical_beestat_mapping_invalid")
    try:
        async with asyncio.timeout(10):
            response = await hass.services.async_call(
                "beestat_statistics",
                "get_configuration",
                {"config_entry_id": entry_id},
                blocking=True,
                return_response=True,
                context=context,
            )
    except HomeAssistantError, TimeoutError:
        raise ValueError("historical_beestat_unavailable") from None
    if not isinstance(response, Mapping) or response.get("config_entry_id") != entry_id:
        raise ValueError("historical_beestat_mapping_invalid")
    effective = response.get("effective_configuration")
    if not isinstance(effective, Mapping):
        raise ValueError("historical_beestat_mapping_invalid")  # noqa: TRY004 - public validation contract
    thermostat_fields = {
        "thermostat_id",
        "slug",
        "climate_entity_id",
        "temperature_entity_id",
    }
    sensor_fields = {
        "sensor_id",
        "thermostat_id",
        "thermostat_slug",
        "slug",
        "temperature_entity_id",
        "include_temperature",
        "include_air_quality",
        "include_co2",
        "include_voc",
    }
    projection: dict[str, Any] = {
        "config_entry_id": entry_id,
        "effective_configuration": {},
    }
    for name, fields in (
        ("thermostats", thermostat_fields),
        ("sensors", sensor_fields),
    ):
        projection["effective_configuration"][name] = [
            _project_row(row, fields) for row in _rows(effective.get(name))
        ]
    if validation_cache is not None:
        validation_cache[key] = projection
    return projection


def _project_row(row: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    result = {}
    for field in fields:
        if field not in row:
            continue
        value = row[field]
        if (value is not None and not isinstance(value, str | int | bool)) or (
            isinstance(value, str) and len(value) > 255
        ):
            raise ValueError("historical_beestat_mapping_invalid")
        result[field] = value
    return result


def _rows(value: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or len(value) > 256
        or not all(isinstance(row, dict) for row in value)
    ):
        raise ValueError("historical_beestat_mapping_invalid")
    return value


def _resource_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("historical_beestat_mapping_invalid")
    return value


def _configured_matches(
    response: Mapping[str, Any], statistic_id: str, quantity: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    effective = response.get("effective_configuration")
    if not isinstance(effective, Mapping):
        raise ValueError("historical_beestat_mapping_invalid")  # noqa: TRY004 - public validation contract
    thermostats = _rows(effective.get("thermostats"))
    sensors = _rows(effective.get("sensors"))
    parents = {_resource_id(row.get("thermostat_id")): row for row in thermostats}
    if len(parents) != len(thermostats):
        raise ValueError("historical_beestat_mapping_ambiguous")
    if quantity in {"humidity", "weather_temperature", "weather_humidity"}:
        suffix = {
            "humidity": "indoor_humidity",
            "weather_temperature": "outdoor_temperature",
            "weather_humidity": "outdoor_humidity",
        }[quantity]
        return [
            (row, row)
            for row in thermostats
            if isinstance(row.get("slug"), str)
            and statistic_id == f"beestat:{row['slug']}_{suffix}"
        ]
    if quantity not in _SENSOR_SPECS:
        return []
    suffix, flag, _unit = _SENSOR_SPECS[quantity]
    found = []
    for row in sensors:
        if (
            not isinstance(row.get("slug"), str)
            or statistic_id != f"beestat:{row['slug']}_{suffix}"
            or row.get(flag) is not True
        ):
            continue
        _resource_id(row.get("sensor_id"))
        parent = parents.get(_resource_id(row.get("thermostat_id")))
        if parent is None or row.get("thermostat_slug") != parent.get("slug"):
            raise ValueError("historical_beestat_mapping_invalid")
        found.append((row, parent))
    return found


def _beestat_binding(
    hass: HomeAssistant,
    entry_id: str,
    row: dict[str, Any],
    parent: dict[str, Any],
    quantity: str,
    anchor: dict[str, Any],
) -> dict[str, Any]:
    if quantity.startswith("weather_"):
        return _beestat_weather_binding(hass, entry_id, parent, quantity, anchor)
    field = "climate_entity_id" if quantity == "humidity" else "temperature_entity_id"
    reference = row.get(field)
    parent_reference = parent.get("climate_entity_id")
    if not isinstance(reference, str) or not isinstance(parent_reference, str):
        raise ValueError("historical_beestat_mapping_invalid")  # noqa: TRY004 - public validation contract
    mapped = _entry(hass, reference)
    if quantity != "humidity":
        _validate_role(hass, mapped, "temperature")
    elif mapped.domain != "climate":
        raise ValueError("historical_source_role_mismatch")
    mapping_identity = _entity_identity(hass, mapped)
    parent_identity = _entity_identity(hass, _entry(hass, parent_reference))
    if parent_identity["domain"] != "climate":
        raise ValueError("historical_source_role_mismatch")
    _require_equivalent(hass, mapping_identity, anchor)
    hardware = mapping_identity["hardware"]
    parent_serial = parent_identity["hardware"]["serial"]
    if (hardware["parent_serial"] and hardware["parent_serial"] != parent_serial) or (
        mapped.platform == "ecobee"
        and "-ei:0-" in mapped.unique_id
        and hardware["serial"] != parent_serial
    ):
        raise ValueError("historical_source_identity_mismatch")
    thermostat_id = _resource_id(parent.get("thermostat_id"))
    evidence = {
        "basis": "beestat_owner_association_requires_confirmation",
        "beestat_config_entry_id": entry_id,
        "resource_kind": "thermostat" if quantity == "humidity" else "sensor",
        "resource_id": thermostat_id if quantity == "humidity" else row["sensor_id"],
        "thermostat_id": thermostat_id,
        "mapping": mapping_identity,
        "parent": parent_identity,
        "anchor": anchor,
        "quantity": quantity,
        "historical_continuity": "unknown",
        "provider_serial_identity": "unproven",
    }
    if quantity == "aqi":
        evidence["normalization"] = (
            "provider_round_raw_div350_times100_before_aggregation"
        )
    result = {
        "method": (
            "beestat_legacy_daily_humidity_summary"
            if quantity == "humidity"
            else "beestat_legacy_daily_sample_mean"
        ),
        "transformation": "identity",
        "identity_evidence": evidence,
    }
    result.update(_timing(hass, entry_id, thermostat_id))
    return result


def _beestat_weather_binding(
    hass: HomeAssistant,
    entry_id: str,
    thermostat: dict[str, Any],
    quantity: str,
    anchor: dict[str, Any],
) -> dict[str, Any]:
    reference = thermostat.get("climate_entity_id")
    if not isinstance(reference, str):
        raise ValueError("historical_beestat_mapping_invalid")  # noqa: TRY004 - public validation contract
    parent = _entry(hass, reference)
    if parent.domain != "climate":
        raise ValueError("historical_source_role_mismatch")
    parent_identity = _entity_identity(hass, parent)
    serial = parent_identity["hardware"]["serial"]
    candidates = []
    for row in er.async_get(hass).entities.values():
        if (
            row.platform != "ecobee"
            or row.domain != "weather"
            or row.unique_id != serial
        ):
            continue
        identity = _entity_identity(hass, row)
        if identity["hardware"]["serial"] == serial:
            candidates.append(row)
    if len(candidates) != 1:
        raise ValueError("historical_source_identity_unproven")
    native = candidates[0]
    anchor_entry = _entry(hass, anchor["registry_id"])
    sources = [(native, hass.states.get(native.entity_id))]
    if native.id != anchor_entry.id:
        sources.append((anchor_entry, hass.states.get(anchor_entry.entity_id)))
    try:
        feed = validate_weather_feed(sources, role=quantity.removeprefix("weather_"))
    except ValueError:
        raise ValueError("historical_source_identity_mismatch") from None
    thermostat_id = _resource_id(thermostat.get("thermostat_id"))
    result: dict[str, Any] = {
        "method": "beestat_legacy_daily_weather_summary",
        "transformation": "identity",
        "identity_evidence": {
            "basis": "beestat_owner_association_requires_confirmation",
            "beestat_config_entry_id": entry_id,
            "resource_kind": "thermostat",
            "resource_id": thermostat_id,
            "thermostat_id": thermostat_id,
            "parent": parent_identity,
            "mapping": _entity_identity(hass, native),
            "anchor": anchor,
            "quantity": quantity,
            "current_native_feed": {
                "config_entry_id": feed.config_entry_id,
                "station": feed.station,
            },
            "historical_continuity": "unknown",
            "provider_serial_identity": "unproven",
            "historical_provider_station": "unknown",
        },
    }
    result.update(_timing(hass, entry_id, thermostat_id))
    return result


def _diagnostic_entry(
    hass: HomeAssistant, config_entry_id: str, unique_id: str
) -> er.RegistryEntry | None:
    rows = [
        row
        for row in er.async_get(hass).entities.values()
        if row.domain == "sensor"
        and row.platform == "beestat_statistics"
        and row.config_entry_id == config_entry_id
        and row.unique_id == unique_id
    ]
    if len(rows) != 1 or rows[0].disabled_by is not None:
        return None
    return rows[0]


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or (stamp := dt_util.parse_datetime(value)) is None:
        return None
    if stamp.tzinfo is None or stamp > dt_util.utcnow():
        return None
    return dt_util.as_utc(stamp).isoformat()


def _timing(hass: HomeAssistant, entry_id: str, thermostat_id: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    entry = _diagnostic_entry(
        hass, entry_id, f"thermostat_{thermostat_id}_cloud_data_end"
    )
    state = hass.states.get(entry.entity_id) if entry else None
    if entry and state and (stamp := _timestamp(state.state)):
        result["provider_data_through"] = stamp
        result["provider_data_through_ref"] = entry.id
        if beginning := _timestamp(state.attributes.get("data_begin")):
            result["provider_window_begin"] = beginning
    imported = _diagnostic_entry(hass, entry_id, "statistics_last_import_success")
    state = hass.states.get(imported.entity_id) if imported else None
    if state and (stamp := _timestamp(state.state)):
        result["import_completed_at"] = stamp
    return result
