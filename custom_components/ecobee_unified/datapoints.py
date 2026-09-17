"""Explicit, read-only composition of equivalent Ecobee observations."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Literal

from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import (
    Event,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import DOMAIN
from .source_contracts import homekit_action_contract_valid
from .weather_source import (
    WeatherSnapshot,
    validate_weather_feed,
    weather_observation,
    weather_snapshot,
)

type DatapointKind = Literal[
    "temperature",
    "humidity",
    "occupancy",
    "motion",
    "battery",
    "profile",
    "duration",
    "number",
    "text",
    "configured_membership",
    "weather",
]
type DatapointValue = float | bool | str | tuple[str, ...] | WeatherSnapshot | None
type ValueRepresentation = Literal["label", "option", "reference"]

KINDS = frozenset(
    {
        "temperature",
        "humidity",
        "occupancy",
        "motion",
        "battery",
        "profile",
        "duration",
        "number",
        "text",
        "configured_membership",
        "weather",
    }
)
BINARY_KINDS = frozenset({"occupancy", "motion"})
_PLATFORMS = frozenset(
    {"homekit_controller", "ecobee", "beestat_statistics", "battery_notes"}
)
_TEMPERATURE_UNITS = frozenset({"°C", "°F", "K"})
_DURATION_SECONDS = {"s": 1, "min": 60, "h": 3600, "d": 86400}
_EMPTY = frozenset({"unknown", "unavailable"})
_TEXT_LIMIT = 255
_ECOBEE_MANUFACTURERS = frozenset({"ecobee", "ecobee inc", "ecobee inc."})


def _text(value: Any, reason: str, *, limit: int = _TEXT_LIMIT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(reason)
    return value


def _optional_text(value: Any, reason: str) -> str | None:
    return None if value is None else _text(value, reason)


def _nonnegative(value: Any, reason: str) -> float:
    if isinstance(value, bool):
        raise ValueError(reason)  # noqa: TRY004 -- One public validation error type.
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError(reason) from err
    if not isfinite(number) or number < 0:
        raise ValueError(reason)
    return number


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """A registry UUID and a bounded public state/attribute selection."""

    entity: str
    attribute: str | None = None
    timestamp_attribute: str | None = None
    unit: str | None = None
    max_age_seconds: float | None = None

    def __post_init__(self) -> None:
        _text(self.entity, "invalid_source_reference")
        for value in (self.attribute, self.timestamp_attribute, self.unit):
            _optional_text(value, "invalid_source_binding")
        if self.max_age_seconds is not None:
            object.__setattr__(
                self,
                "max_age_seconds",
                _nonnegative(self.max_age_seconds, "invalid_source_max_age"),
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SourceBinding:
        """Parse saved selections without silently replacing invalid fields."""
        return cls(
            entity=value["entity"],
            attribute=value.get("attribute"),
            timestamp_attribute=value.get("timestamp_attribute"),
            unit=value.get("unit"),
            max_age_seconds=value.get("max_age_seconds"),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the persisted contract."""
        return {
            "entity": self.entity,
            "attribute": self.attribute,
            "timestamp_attribute": self.timestamp_attribute,
            "unit": self.unit,
            "max_age_seconds": self.max_age_seconds,
        }


@dataclass(frozen=True, slots=True)
class DatapointConfig:
    """One semantic, time basis, output unit, and ordered source policy."""

    datapoint_id: str
    name: str
    kind: DatapointKind
    sources: tuple[SourceBinding, ...]
    unit: str | None = None
    time_basis: Literal["current", "interval"] = "current"
    interval_seconds: float | None = None
    max_age_seconds: float = 0
    fallback: bool = True
    semantic: str | None = None
    weather_station: str | None = None
    weather_config_entry_id: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.datapoint_id):
            raise ValueError("invalid_datapoint_id")
        _text(self.name, "invalid_datapoint_name")
        if self.kind not in KINDS:
            raise ValueError("invalid_datapoint_kind")
        if not isinstance(self.sources, tuple) or not 2 <= len(self.sources) <= 8:
            raise ValueError("invalid_source_count")
        if len({(item.entity, item.attribute) for item in self.sources}) != len(
            self.sources
        ):
            raise ValueError("duplicate_datapoint_source")
        if self.time_basis not in {"current", "interval"}:
            raise ValueError("invalid_time_basis")
        if self.time_basis == "interval":
            if (
                self.interval_seconds is None
                or _nonnegative(self.interval_seconds, "invalid_interval") == 0
            ):
                raise ValueError("invalid_interval")
            if any(item.timestamp_attribute is None for item in self.sources):
                raise ValueError("interval_timestamp_required")
        elif self.interval_seconds is not None:
            raise ValueError("unexpected_interval")
        object.__setattr__(
            self,
            "max_age_seconds",
            _nonnegative(self.max_age_seconds, "invalid_max_age"),
        )
        if self.interval_seconds is not None:
            object.__setattr__(
                self,
                "interval_seconds",
                _nonnegative(self.interval_seconds, "invalid_interval"),
            )
        if not isinstance(self.fallback, bool):
            raise ValueError("invalid_fallback")  # noqa: TRY004 -- Public validation contract.
        _optional_text(self.semantic, "invalid_semantic")
        _validate_output_unit(self)
        _validate_weather_config(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DatapointConfig:
        """Restore stable mapping identities, including temporarily missing sources."""
        return cls(
            datapoint_id=value["datapoint_id"],
            name=value["name"],
            kind=value["kind"],
            sources=tuple(SourceBinding.from_dict(item) for item in value["sources"]),
            unit=value.get("unit"),
            time_basis=value.get("time_basis", "current"),
            interval_seconds=value.get("interval_seconds"),
            max_age_seconds=value.get("max_age_seconds", 0),
            fallback=value.get("fallback", True),
            semantic=value.get("semantic"),
            weather_station=value.get("weather_station"),
            weather_config_entry_id=value.get("weather_config_entry_id"),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the complete portable saved configuration."""
        return {
            "datapoint_id": self.datapoint_id,
            "name": self.name,
            "kind": self.kind,
            "sources": [item.as_dict() for item in self.sources],
            "unit": self.unit,
            "time_basis": self.time_basis,
            "interval_seconds": self.interval_seconds,
            "max_age_seconds": self.max_age_seconds,
            "fallback": self.fallback,
            "semantic": self.semantic,
            "weather_station": self.weather_station,
            "weather_config_entry_id": self.weather_config_entry_id,
        }


def _validate_output_unit(config: DatapointConfig) -> None:
    if config.kind == "temperature":
        if config.unit not in _TEMPERATURE_UNITS:
            raise ValueError("invalid_temperature_unit")
        allowed = (
            {"weather_temperature"}
            if config.weather_config_entry_id
            else {"control_temperature", "physical_temperature"}
        )
        if config.semantic not in allowed:
            raise ValueError("temperature_semantic_required")
    elif config.kind in {"humidity", "battery"}:
        if config.unit != "%":
            raise ValueError("invalid_percentage_unit")
    elif config.kind == "duration":
        _validate_duration_config(config)
    elif config.kind in BINARY_KINDS | {
        "profile",
        "text",
        "configured_membership",
        "weather",
    }:
        if config.unit is not None:
            raise ValueError("unexpected_unit")
        if config.kind == "configured_membership" and config.time_basis != "current":
            raise ValueError("invalid_time_basis")
    else:
        _optional_text(config.unit, "invalid_unit")


def _validate_weather_config(config: DatapointConfig) -> None:
    has_weather = (
        config.weather_station is not None or config.weather_config_entry_id is not None
    )
    if not has_weather and config.kind != "weather":
        return
    _text(config.weather_station, "weather_identity_required", limit=128)
    _text(config.weather_config_entry_id, "weather_identity_required")
    if config.time_basis != "current":
        raise ValueError("invalid_time_basis")
    if config.kind == "number" and config.unit is None:
        raise ValueError("weather_unit_mismatch")
    role = _weather_role(config)
    if role == "weather" and any(
        binding.unit is not None for binding in config.sources
    ):
        raise ValueError("weather_unit_mismatch")
    expected_attribute = None if role in {"condition", "weather"} else role
    if any(
        binding.attribute != expected_attribute
        or binding.timestamp_attribute is not None
        for binding in config.sources
    ):
        raise ValueError("invalid_weather_role")


def _weather_role(config: DatapointConfig) -> str:
    if config.kind == "weather":
        return "weather"
    if config.kind in {"temperature", "humidity"}:
        return config.kind
    if config.kind == "text":
        return "condition"
    if config.kind == "number" and config.sources[0].attribute in {
        "pressure",
        "wind_bearing",
        "wind_speed",
        "visibility",
    }:
        return str(config.sources[0].attribute)
    raise ValueError("invalid_weather_role")


def _validate_duration_config(config: DatapointConfig) -> None:
    if config.unit not in _DURATION_SECONDS:
        raise ValueError("invalid_duration_unit")
    if config.semantic not in {"elapsed_duration", "minimum_fan_runtime_per_hour"}:
        raise ValueError("duration_semantic_required")
    if (
        config.semantic == "minimum_fan_runtime_per_hour"
        and config.time_basis != "current"
    ):
        raise ValueError("invalid_time_basis")


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """A bounded source-index status, safe to reuse in diagnostics."""

    index: int
    status: str


@dataclass(frozen=True, slots=True)
class SourceContext:
    """Bounded selected-source meaning; never proof of cross-source agreement."""

    timing_quality: str
    membership_basis: str | None = None
    profile_name: str | None = None
    profile_ref: str | None = None
    preset_mode: str | None = None
    program_profile_name: str | None = None
    weather_station: str | None = None
    provider_reported_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _Observation:
    value: DatapointValue
    entity_id: str
    reported_at: datetime
    observed_at: datetime | None
    freshness_timestamp: datetime | None
    representation: ValueRepresentation | None = None
    context: SourceContext | None = None


@dataclass(frozen=True, slots=True)
class DatapointSnapshot:
    """One selection; timestamps never claim an unprovided device observation."""

    value: DatapointValue
    available: bool
    status: str
    selected_source: str | None
    reported_at: datetime | None
    observed_at: datetime | None
    source_statuses: tuple[SourceStatus, ...]
    fallback_used: bool = False
    value_representation: ValueRepresentation | None = None
    source_context: SourceContext | None = None


def _registry_entry(hass: HomeAssistant, reference: str) -> er.RegistryEntry | None:
    registry = er.async_get(hass)
    resolved = er.async_resolve_entity_id(registry, reference)
    entry = registry.async_get(resolved) if resolved else None
    # Persist registry UUIDs; an old entity_id must not silently bind a replacement.
    return entry if entry is not None and entry.id == reference else None


def _require_enabled(
    hass: HomeAssistant, entry: er.RegistryEntry, *, reason: str = "source_disabled"
) -> None:
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    if entry.disabled_by is not None or (
        isinstance(device, dr.DeviceEntry) and device.disabled_by is not None
    ):
        raise ValueError(reason)


def _identity_error(hass: HomeAssistant, config: DatapointConfig) -> str | None:
    """Prove Ecobee origin and a single serial, rejecting association contradictions."""
    if config.weather_config_entry_id is not None:
        return None  # Each alias independently proves the persisted weather feed.
    return _source_identity_error(hass, config.sources)


def _source_identity_error(
    hass: HomeAssistant, sources: tuple[SourceBinding, ...]
) -> str | None:
    """Reuse native device proof for one group or both sides of a source edit."""
    devices: dict[str, dr.DeviceEntry] = {}
    platforms: dict[str, set[str]] = {}
    for binding in sources:
        entry = _registry_entry(hass, binding.entity)
        if entry is None:
            continue
        if entry.platform not in _PLATFORMS:
            return "unsupported_source_platform"
        device = (
            dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
        )
        if not isinstance(device, dr.DeviceEntry):
            return "source_identity_unproven"
        devices[device.id] = device
        platforms.setdefault(device.id, set()).add(entry.platform)
    return _compare_devices(devices, platforms)


def _compare_devices(
    devices: dict[str, dr.DeviceEntry], platforms: dict[str, set[str]]
) -> str | None:
    identities: set[str] = set()
    proven_ecobee = False
    for device_id, device in devices.items():
        ecobee_ids = {
            value.strip().casefold()
            for domain, value in device.identifiers
            if domain == "ecobee" and value.strip()
        }
        serial = (device.serial_number or "").strip().casefold()
        ecobee_manufacturer = (
            device.manufacturer or ""
        ).strip().casefold() in _ECOBEE_MANUFACTURERS
        if len(ecobee_ids) > 1 or (ecobee_ids and serial and serial not in ecobee_ids):
            return "source_identity_mismatch"
        if ecobee_ids:
            identities.update(ecobee_ids)
            proven_ecobee = True
        elif "homekit_controller" in platforms[device_id] and serial:
            identities.add(serial)
            proven_ecobee |= ecobee_manufacturer
        elif serial and ecobee_manufacturer:
            identities.add(serial)
            proven_ecobee = True
        else:
            return "source_identity_unproven"
    if len(identities) > 1:
        return "source_identity_mismatch"
    return None if proven_ecobee and identities else "source_identity_unproven"


def validate_datapoint_edit_sources(
    hass: HomeAssistant, previous: DatapointConfig, current: DatapointConfig
) -> None:
    """Keep a saved feed/subject and observation roles behind one output identity.

    Unchanged registry bindings retain their meaning without inventing proof for
    an opaque role. Replacing them requires native cross-source evidence; units,
    device classes, and coincident values alone do not establish that evidence.
    """
    if reason := _observation_role_error(hass, current):
        raise ValueError(reason)
    previous_feed = (previous.weather_config_entry_id, previous.weather_station)
    current_feed = (current.weather_config_entry_id, current.weather_station)
    if previous_feed != current_feed:
        raise ValueError("datapoint_meaning_change")
    if current.weather_config_entry_id is not None:
        if _weather_role(previous) != _weather_role(current):
            raise ValueError("datapoint_meaning_change")
        return
    previous_bindings = {(item.entity, item.attribute) for item in previous.sources}
    current_bindings = {(item.entity, item.attribute) for item in current.sources}
    if previous_bindings == current_bindings:
        return
    # A missing old registry entry cannot prove what a replacement would replace.
    # Do not let a surviving source silently stand in for an unknown old binding.
    combined = previous.sources + current.sources
    if any(_registry_entry(hass, item.entity) is None for item in combined):
        raise ValueError("datapoint_meaning_change")
    if _source_identity_error(hass, combined) is not None:
        raise ValueError("datapoint_meaning_change")
    if _edit_roles(hass, previous) != _edit_roles(hass, current):
        raise ValueError("datapoint_meaning_change")


def _edit_roles(hass: HomeAssistant, config: DatapointConfig) -> set[tuple[str, ...]]:
    roles: set[tuple[str, ...]] = set()
    for binding in config.sources:
        entry = _registry_entry(hass, binding.entity)
        try:
            role = (
                _native_edit_role(hass, config, binding, entry)
                if entry is not None
                else None
            )
        except ValueError:
            role = None
        roles.add(
            ("role", role)
            if role is not None
            else ("binding", binding.entity, binding.attribute or "")
        )
    return roles


def _observation_role_error(hass: HomeAssistant, config: DatapointConfig) -> str | None:
    """Reject known contradictions independently of individual source availability."""
    if config.weather_config_entry_id is not None:
        return None  # Weather configuration already requires one exact feed and role.
    if config.kind == "configured_membership":
        # Both admitted membership bases are intentional alternatives; the selected
        # source retains its active-preset/program context instead of asserting sameness.
        return None
    roles = {token[1] for token in _edit_roles(hass, config) if token[0] == "role"}
    return "source_observation_role_mismatch" if len(roles) > 1 else None


def datapoint_observation_role(
    hass: HomeAssistant, config: DatapointConfig
) -> str | None:
    """Prove one native role across all bindings for a stricter typed consumer.

    An opaque or missing source cannot establish a role for the complete output.
    This describes current registry evidence, not historical subject continuity.
    """
    if _identity_error(hass, config) is not None:
        return None
    roles = _edit_roles(hass, config)
    if len(roles) != 1:
        return None
    token = next(iter(roles))
    return token[1] if token[0] == "role" else None


def _native_edit_role(
    hass: HomeAssistant,
    config: DatapointConfig,
    binding: SourceBinding,
    entry: er.RegistryEntry,
) -> str | None:
    """Normalize only native roles, leaving unknown attributes and states opaque."""
    if config.kind == "temperature":
        return _temperature_semantic(hass, entry, binding)
    if config.kind == "profile":
        _profile_representation(hass, entry, binding)
        return "current_profile"
    if config.kind == "configured_membership":
        _membership_metadata(entry, binding)
        return (
            "active_preset_membership"
            if entry.platform == "ecobee"
            else "current_program_membership"
        )
    if config.kind == "duration":
        _validate_duration_semantic(config, entry, binding)
        return (
            "minimum_fan_runtime_per_hour"
            if config.semantic == "minimum_fan_runtime_per_hour"
            else None
        )
    return _native_measurement_role(config.kind, binding, entry)


def _native_measurement_role(
    kind: DatapointKind, binding: SourceBinding, entry: er.RegistryEntry
) -> str | None:
    if kind == "humidity" and entry.domain == "climate":
        return {
            "current_humidity": "measured_humidity",
            "humidity": "target_humidity",
        }.get(binding.attribute or "")
    # Native Ecobee capability entities use the capability class as the final
    # unique-ID component. HomeKit instance IDs and generic elapsed-duration
    # sources do not independently identify a narrower observation role.
    if entry.platform != "ecobee" or binding.attribute is not None:
        return None
    if (
        kind == "humidity"
        and entry.domain == "sensor"
        and entry.unique_id.endswith("-humidity")
    ):
        return "measured_humidity"
    if (
        kind == "occupancy"
        and entry.domain == "binary_sensor"
        and entry.unique_id.endswith("-occupancy")
    ):
        return "occupancy"
    return None


def _temperature_semantic(
    hass: HomeAssistant, entry: er.RegistryEntry, binding: SourceBinding
) -> str | None:
    if entry.domain == "climate" and binding.attribute == "current_temperature":
        return "control_temperature"
    if binding.attribute is not None or entry.domain != "sensor":
        return None
    if entry.platform == "ecobee" and entry.unique_id.endswith("-temperature"):
        return "physical_temperature"
    if entry.platform == "homekit_controller":
        if entry.unique_id.endswith("_1_16_19"):
            return "control_temperature"
        device = (
            dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
        )
        if isinstance(device, dr.DeviceEntry) and device.model == "EBERS41":
            return "physical_temperature"
    return None


def _source_metadata(
    hass: HomeAssistant,
    config: DatapointConfig,
    binding: SourceBinding,
    entry: er.RegistryEntry,
    state: State | None,
) -> str | None:
    attributes = state.attributes if state is not None else {}
    if config.kind == "profile":
        _profile_representation(hass, entry, binding)
    if binding.attribute is not None:
        return _attribute_metadata(hass, config, binding, entry, attributes)
    if config.kind == "configured_membership":
        raise ValueError("membership_attribute_required")
    domains = {"binary_sensor"} if config.kind in BINARY_KINDS else {"sensor"}
    if config.kind == "profile":
        domains = {"select", "sensor"}
    elif config.kind in {"duration", "number"}:
        domains.add("number")
    if entry.domain not in domains:
        raise ValueError("invalid_source_domain")
    if config.kind == "duration":
        _validate_duration_semantic(config, entry, binding)
    device_class = attributes.get(ATTR_DEVICE_CLASS, entry.original_device_class)
    expected_class = (
        config.kind
        if config.kind
        in {"temperature", "humidity", "battery", "duration", "occupancy", "motion"}
        else None
    )
    classless_number = entry.domain == "number" and device_class is None
    if (
        expected_class is not None
        and device_class != expected_class
        and not classless_number
    ):
        raise ValueError("source_device_class_mismatch")
    allowed_classes = (
        {None, "", "enum"} if config.kind in {"text", "profile"} else {None, ""}
    )
    if expected_class is None and device_class not in allowed_classes:
        raise ValueError("source_device_class_mismatch")
    if (
        config.kind == "temperature"
        and _temperature_semantic(hass, entry, binding) != config.semantic
    ):
        raise ValueError("temperature_semantic_mismatch")
    unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT, entry.unit_of_measurement)
    return _assert_unit(binding, unit)


def _profile_representation(
    hass: HomeAssistant, entry: er.RegistryEntry, binding: SourceBinding
) -> ValueRepresentation:
    """Identify only a native current-profile role, keeping its exact text format."""
    if (
        entry.platform == "homekit_controller"
        and binding.attribute is None
        and entry.translation_key == "ecobee_mode"
        and homekit_action_contract_valid(hass, binding.entity, "preset")
    ):
        return "option"
    if (
        entry.platform == "ecobee"
        and entry.domain == "climate"
        and binding.attribute == "climate_mode"
    ):
        return "label"
    if (
        entry.platform == "beestat_statistics"
        and entry.domain == "sensor"
        and entry.translation_key == "current_comfort_profile"
    ):
        if binding.attribute is None:
            return "label"
        if binding.attribute == "profile_ref":
            return "reference"
    raise ValueError("invalid_profile_source")


def _attribute_metadata(
    hass: HomeAssistant,
    config: DatapointConfig,
    binding: SourceBinding,
    entry: er.RegistryEntry,
    attributes: Mapping[str, Any],
) -> str | None:
    if config.kind == "configured_membership":
        _membership_metadata(entry, binding)
        return None
    known: dict[str, tuple[str, str | None]] = {}
    if entry.domain == "climate":
        known = {
            "current_temperature": (
                "temperature",
                attributes.get(
                    ATTR_UNIT_OF_MEASUREMENT, hass.config.units.temperature_unit
                ),
            ),
            "current_humidity": ("humidity", "%"),
            "humidity": ("humidity", "%"),
            "fan_min_on_time": ("duration", "min"),
            "climate_mode": ("profile", None),
            "preset_mode": ("text" if entry.platform == "ecobee" else "profile", None),
            "equipment_running": ("text", None),
            "hvac_action": ("text", None),
        }
    if binding.attribute in known:
        kind, unit = known[binding.attribute]
        if config.kind != kind:
            raise ValueError("source_attribute_semantic_mismatch")
        if kind == "temperature" and config.semantic != "control_temperature":
            raise ValueError("temperature_semantic_mismatch")
        if kind == "duration":
            _validate_duration_semantic(config, entry, binding)
        return _assert_unit(binding, unit)
    if config.kind == "temperature":
        raise ValueError("temperature_semantic_unproven")
    if config.kind in BINARY_KINDS or config.kind in {"profile", "text"}:
        if binding.unit is not None:
            raise ValueError("unexpected_unit")
    elif binding.unit is None:
        raise ValueError("source_attribute_unit_required")
    if config.kind == "duration":
        _validate_duration_semantic(config, entry, binding)
    return binding.unit


def _membership_metadata(entry: er.RegistryEntry, binding: SourceBinding) -> None:
    native_membership = (
        entry.platform == "ecobee"
        and entry.domain == "climate"
        and binding.attribute == "active_sensors"
    ) or (
        entry.platform == "beestat_statistics"
        and entry.domain == "sensor"
        and binding.attribute in {"profile_sensors", "configured_sensor_names"}
    )
    if not native_membership:
        raise ValueError("membership_attribute_required")
    valid_timestamp = binding.timestamp_attribute is None or (
        entry.platform == "beestat_statistics"
        and binding.timestamp_attribute == "metadata_synced_at"
    )
    if not valid_timestamp:
        raise ValueError("membership_timestamp_invalid")
    _assert_unit(binding, None)


def _membership_context(
    entry: er.RegistryEntry,
    binding: SourceBinding,
    state: State,
    now: datetime,
    reported: datetime,
) -> tuple[SourceContext, datetime | None, datetime | None]:
    attributes = state.attributes
    if entry.platform == "ecobee":
        return (
            SourceContext(
                timing_quality="ha_report_only",
                membership_basis="native_active_preset",
                preset_mode=_optional_text(
                    attributes.get("preset_mode"), "membership_profile_context_invalid"
                ),
                program_profile_name=_optional_text(
                    attributes.get("climate_mode"), "membership_profile_context_invalid"
                ),
            ),
            None,
            reported,
        )
    profile_ref = _text(
        attributes.get("profile_ref"), "membership_profile_context_missing"
    )
    profile_name = (
        state.state
        if binding.attribute == "profile_sensors"
        else attributes.get("profile_name")
    )
    profile_name = _optional_text(profile_name, "membership_profile_context_invalid")
    raw_timestamp = attributes.get("metadata_synced_at")
    observed = _timestamp(raw_timestamp, now) if raw_timestamp is not None else None
    return (
        SourceContext(
            timing_quality="metadata_observed" if observed else "metadata_time_unknown",
            membership_basis="current_program_profile",
            profile_ref=profile_ref,
            profile_name=profile_name,
        ),
        observed,
        observed,
    )


def _validate_duration_semantic(
    config: DatapointConfig, entry: er.RegistryEntry, binding: SourceBinding
) -> None:
    fan_setting = entry.platform == "ecobee" and (
        (entry.domain == "climate" and binding.attribute == "fan_min_on_time")
        or (
            entry.domain == "number"
            and binding.attribute is None
            and entry.translation_key == "fan_min_on_time"
        )
    )
    expected = "minimum_fan_runtime_per_hour" if fan_setting else "elapsed_duration"
    if config.semantic != expected or (entry.domain == "number" and not fan_setting):
        raise ValueError("duration_semantic_mismatch")


def _assert_unit(binding: SourceBinding, native: Any) -> str | None:
    unit = str(native) if native is not None else None
    if binding.unit is not None and binding.unit != unit:
        raise ValueError("source_unit_assertion_mismatch")
    return unit


def _validate_unit(config: DatapointConfig, unit: str | None) -> None:
    allowed = (
        _TEMPERATURE_UNITS
        if config.kind == "temperature"
        else _DURATION_SECONDS
        if config.kind == "duration"
        else {config.unit}
    )
    if unit not in allowed:
        raise ValueError("source_unit_mismatch")


def validate_datapoint(hass: HomeAssistant, config: DatapointConfig) -> None:
    """Validate a new mapping against native public registry and state metadata."""
    if config.weather_config_entry_id is not None:
        _validate_weather_datapoint(hass, config)
        return
    if reason := _identity_error(hass, config) or _observation_role_error(hass, config):
        raise ValueError(reason)
    for binding in config.sources:
        entry = _registry_entry(hass, binding.entity)
        if entry is None:
            raise ValueError("source_missing")
        _require_enabled(hass, entry, reason="source_missing")
        state = hass.states.get(entry.entity_id)
        if binding.attribute is not None and (
            state is None or binding.attribute not in state.attributes
        ):
            raise ValueError("source_attribute_missing")
        if binding.timestamp_attribute is not None and (
            state is None or binding.timestamp_attribute not in state.attributes
        ):
            raise ValueError("source_timestamp_missing")
        unit = _source_metadata(hass, config, binding, entry, state)
        _validate_unit(config, unit)
        if state is not None and state.state not in _EMPTY:
            raw = (
                state.attributes[binding.attribute]
                if binding.attribute
                else state.state
            )
            _value(config, raw, unit)
            if config.kind == "configured_membership":
                _membership_context(
                    entry, binding, state, dt_util.utcnow(), state.last_reported
                )
            if binding.timestamp_attribute:
                _timestamp(
                    state.attributes[binding.timestamp_attribute], dt_util.utcnow()
                )


def _validate_weather_datapoint(hass: HomeAssistant, config: DatapointConfig) -> None:
    sources: list[tuple[er.RegistryEntry, State | None]] = []
    for binding in config.sources:
        entry = _registry_entry(hass, binding.entity)
        if entry is None:
            raise ValueError("source_missing")
        _require_enabled(hass, entry, reason="source_missing")
        sources.append((entry, hass.states.get(entry.entity_id)))
    role = _weather_role(config)
    validate_weather_feed(
        sources,
        expected_station=config.weather_station,
        role=None if role == "weather" else role,
        output_unit=config.unit,
    )
    now = dt_util.utcnow()
    for binding, (entry, state) in zip(config.sources, sources, strict=True):
        _weather_read(hass, config, binding, entry, state, now)


def _weather_read(
    hass: HomeAssistant,
    config: DatapointConfig,
    binding: SourceBinding,
    entry: er.RegistryEntry,
    state: State | None,
    now: datetime,
) -> _Observation:
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    identifiers = (
        {value for domain, value in device.identifiers if domain == "ecobee"}
        if isinstance(device, dr.DeviceEntry)
        else set()
    )
    if identifiers != {entry.unique_id}:
        raise ValueError("weather_device_identity_mismatch")
    assert (
        config.weather_station is not None
        and config.weather_config_entry_id is not None
    )
    kwargs = {
        "expected_station": config.weather_station,
        "expected_config_entry_id": config.weather_config_entry_id,
    }
    role = _weather_role(config)
    value: DatapointValue
    if role == "weather":
        full_weather = weather_snapshot(entry, state, **kwargs, now=now)
        value = full_weather
        provider_time = full_weather.provider_reported_at
        reported = full_weather.ha_reported_at
    else:
        reading = weather_observation(
            entry, state, role, **kwargs, output_unit=config.unit, now=now
        )
        if binding.unit is not None:
            native = weather_observation(entry, state, role, **kwargs, now=now)
            if native.unit != binding.unit:
                raise ValueError("source_unit_assertion_mismatch")
        value = reading.value
        provider_time = reading.provider_reported_at
        reported = reading.ha_reported_at
    context = SourceContext(
        timing_quality="provider_reported",
        weather_station=config.weather_station,
        provider_reported_at=provider_time,
    )
    return _Observation(
        value, entry.entity_id, reported, None, provider_time, context=context
    )


def _value(config: DatapointConfig, raw: Any, unit: str | None) -> DatapointValue:
    if config.kind == "configured_membership":
        return _membership(raw)
    if config.kind in BINARY_KINDS:
        if raw not in ("on", "off"):
            raise ValueError("invalid_boolean")
        return bool(raw == "on")
    if config.kind in {"profile", "text"}:
        result = _text(raw, "invalid_text")
        if result in _EMPTY:
            raise ValueError("unknown")
        return result
    return _numeric_value(config, _finite_number(raw), unit)


def _numeric_value(config: DatapointConfig, number: float, unit: str | None) -> float:
    if config.kind == "temperature":
        assert unit is not None and config.unit is not None
        kelvin = TemperatureConverter.convert(number, unit, "K")
        if not isfinite(kelvin) or kelvin < 0:
            raise ValueError("invalid_temperature")
        number = TemperatureConverter.convert(number, unit, config.unit)
    elif config.kind in {"humidity", "battery"} and not 0 <= number <= 100:
        raise ValueError("invalid_percentage")
    elif config.kind == "duration":
        assert unit is not None and config.unit is not None
        if number < 0:
            raise ValueError("invalid_duration")
        if (
            config.semantic == "minimum_fan_runtime_per_hour"
            and number * _DURATION_SECONDS[unit] > 3600
        ):
            raise ValueError("invalid_fan_runtime")
        number = number * _DURATION_SECONDS[unit] / _DURATION_SECONDS[config.unit]
    if not isfinite(number):
        raise ValueError("invalid_number")
    return number


def _membership(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list | tuple) or len(raw) > 32:
        raise ValueError("invalid_membership")
    members = tuple(_text(item, "invalid_membership") for item in raw)
    if len(set(members)) != len(members):
        raise ValueError("invalid_membership")
    return tuple(sorted(members))


def _finite_number(raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueError("invalid_number")  # noqa: TRY004 -- Public validation contract.
    try:
        number = float(raw)
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError("invalid_number") from err
    if not isfinite(number):
        raise ValueError("invalid_number")
    return number


def _timestamp(raw: Any, now: datetime) -> datetime:
    result = dt_util.parse_datetime(raw) if isinstance(raw, str) else raw
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("invalid_timestamp")
    result = dt_util.as_utc(result)
    if (result - now).total_seconds() > 60:
        raise ValueError("future_timestamp")
    return result


class DatapointManager:
    """Own subscriptions, deterministic selection and one freshness boundary timer."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, configs: tuple[DatapointConfig, ...]
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.configs = configs
        if len({item.datapoint_id for item in configs}) != len(configs):
            raise ValueError("duplicate_datapoint_id")
        self._snapshots: dict[str, DatapointSnapshot] = {}
        self._unsub: list[Callable[[], None]] = []
        self._state_unsub: list[Callable[[], None]] = []
        self._timer: Callable[[], None] | None = None
        self._running = False
        self._entities: set[str] = set()
        self._devices: set[str] = set()
        self._references = {
            source.entity for config in configs for source in config.sources
        }
        self._reported: dict[str, datetime] = {}

    def signal(self, datapoint_id: str) -> str:
        """Return the entry-scoped projection update signal."""
        return f"{DOMAIN}_{self.entry_id}_datapoint_{datapoint_id}"

    def unique_id(self, datapoint_id: str) -> str:
        """Return the stable entry-scoped entity identity."""
        return f"{self.entry_id}_datapoint_{datapoint_id}"

    def resolve_entity_id(self, reference: str) -> str | None:
        """Resolve only the saved registry identity."""
        entry = _registry_entry(self.hass, reference)
        return entry.entity_id if entry else None

    def snapshot(self, datapoint_id: str) -> DatapointSnapshot:
        """Return immutable cached normalized state."""
        return self._snapshots[datapoint_id]

    async def async_start(self) -> None:
        """Start the cache-only runtime."""
        if self._running:
            return
        self._running = True
        self._unsub = [
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_event
            ),
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._device_event
            ),
        ]
        self._subscribe()
        self._refresh()

    async def async_stop(self) -> None:
        """Fence callbacks and release every listener and deadline."""
        self._running = False
        for unsubscribe in (*self._unsub, *self._state_unsub):
            unsubscribe()
        self._unsub.clear()
        self._state_unsub.clear()
        if self._timer:
            self._timer()
            self._timer = None
        self._reported.clear()

    def _subscribe(self) -> None:
        for unsubscribe in self._state_unsub:
            unsubscribe()
        self._state_unsub.clear()
        entries = [
            entry
            for reference in self._references
            if (entry := _registry_entry(self.hass, reference)) is not None
        ]
        self._entities = {entry.entity_id for entry in entries}
        self._reported = {
            entity_id: timestamp
            for entity_id, timestamp in self._reported.items()
            if entity_id in self._entities
        }
        self._devices = {entry.device_id for entry in entries if entry.device_id}
        if self._entities:
            self._state_unsub.append(
                async_track_state_change_event(
                    self.hass, self._entities, self._state_event
                )
            )
        freshness_entities = {
            resolved
            for config in self.configs
            for binding in config.sources
            if _source_max_age(config, binding)
            if (resolved := self.resolve_entity_id(binding.entity))
        }
        if freshness_entities:
            self._state_unsub.append(
                async_track_state_report_event(
                    self.hass, freshness_entities, self._report_event
                )
            )

    @callback
    def _state_event(self, event: Event[EventStateChangedData]) -> None:
        self._reported.pop(event.data["entity_id"], None)
        self._refresh()

    @callback
    def _report_event(self, event: Event[EventStateReportedData]) -> None:
        self._reported[event.data["entity_id"]] = event.data["last_reported"]
        self._refresh()

    @callback
    def _registry_event(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        if not self._running:
            return
        affected = {event.data["entity_id"]}
        old_entity_id = event.data.get("old_entity_id")
        if isinstance(old_entity_id, str):
            affected.add(old_entity_id)
        registry = er.async_get(self.hass)
        if self._entities.intersection(affected) or any(
            (entry := registry.async_get(entity_id)) is not None
            and (
                entry.id in self._references
                or (entry.platform == DOMAIN and entry.config_entry_id == self.entry_id)
            )
            for entity_id in affected
            if entity_id is not None
        ):
            self._subscribe()
            self._refresh()
            self._sync_device_links()

    @callback
    def _device_event(self, event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        if self._running and event.data["device_id"] in self._devices:
            self._subscribe()
            self._refresh()
            self._sync_device_links()

    def _sync_device_links(self) -> None:
        registry = er.async_get(self.hass)
        configs = {self.unique_id(item.datapoint_id): item for item in self.configs}
        for entity in er.async_entries_for_config_entry(registry, self.entry_id):
            config = configs.get(entity.unique_id)
            if (
                config is None
                or entity.platform != DOMAIN
                or entity.config_entry_id != self.entry_id
            ):
                continue
            source = (
                None
                if config.weather_config_entry_id
                else _registry_entry(self.hass, config.sources[0].entity)
            )
            device_id = source.device_id if source else None
            if device_id and dr.async_get(self.hass).async_get(device_id) is None:
                device_id = None
            if entity.device_id != device_id:
                registry.async_update_entity(entity.entity_id, device_id=device_id)

    @callback
    def _refresh(self, _now: datetime | None = None) -> None:
        if not self._running:
            return
        if self._timer:
            self._timer()
            self._timer = None
        now = dt_util.utcnow()
        deadlines: list[float] = []
        for config in self.configs:
            snapshot, remaining = self._evaluate(config, now)
            previous = self._snapshots.get(config.datapoint_id)
            self._snapshots[config.datapoint_id] = snapshot
            deadlines.extend(remaining)
            if snapshot != previous:
                async_dispatcher_send(self.hass, self.signal(config.datapoint_id))
        if deadlines:
            self._timer = async_call_later(self.hass, min(deadlines), self._refresh)

    def _evaluate(
        self, config: DatapointConfig, now: datetime
    ) -> tuple[DatapointSnapshot, list[float]]:
        group_error = _identity_error(self.hass, config) or _observation_role_error(
            self.hass, config
        )
        statuses: list[SourceStatus] = []
        deadlines: list[float] = []
        selected: tuple[_Observation, int] | None = None
        for index, binding in enumerate(config.sources):
            try:
                if group_error:
                    raise ValueError(group_error)
                observation = self._read(config, binding, now)
                cutoff = _source_max_age(config, binding)
                if cutoff:
                    if observation.freshness_timestamp is None:
                        raise ValueError("metadata_time_unknown")
                    age = (now - observation.freshness_timestamp).total_seconds()
                    remaining = cutoff - age
                    if remaining <= 0:
                        raise ValueError("stale")
                    deadlines.append(remaining)
                quality = (
                    "metadata_time_unknown"
                    if observation.context
                    and observation.context.timing_quality == "metadata_time_unknown"
                    else "available"
                )
                statuses.append(SourceStatus(index, quality))
                if selected is None and (index == 0 or config.fallback):
                    selected = observation, index
            except ValueError as err:
                statuses.append(SourceStatus(index, str(err)))
        if selected is None:
            return DatapointSnapshot(
                None,
                False,
                group_error or "no_usable_source",
                None,
                None,
                None,
                tuple(statuses),
            ), deadlines
        observation, index = selected
        quality = statuses[index].status
        return DatapointSnapshot(
            observation.value,
            True,
            quality if quality != "available" else "fallback" if index else "available",
            observation.entity_id,
            observation.reported_at,
            observation.observed_at,
            tuple(statuses),
            bool(index),
            observation.representation,
            observation.context,
        ), deadlines

    def _read(
        self, config: DatapointConfig, binding: SourceBinding, now: datetime
    ) -> _Observation:
        entry = _registry_entry(self.hass, binding.entity)
        if entry is None:
            raise ValueError("missing")
        _require_enabled(self.hass, entry)
        state = self.hass.states.get(entry.entity_id)
        if config.weather_config_entry_id is not None:
            return _weather_read(self.hass, config, binding, entry, state, now)
        if state is None:
            raise ValueError("missing")
        if state.state in _EMPTY:
            raise ValueError(state.state)
        unit = _source_metadata(self.hass, config, binding, entry, state)
        _validate_unit(config, unit)
        raw = state.state
        if binding.attribute is not None:
            if binding.attribute not in state.attributes:
                raise ValueError("attribute_missing")
            raw = state.attributes[binding.attribute]
        observed = None
        if binding.timestamp_attribute is not None:
            observed = _timestamp(
                state.attributes.get(binding.timestamp_attribute), now
            )
        value = _value(config, raw, unit)
        reported = self._reported.get(entry.entity_id, state.last_reported)
        representation = (
            _profile_representation(self.hass, entry, binding)
            if config.kind == "profile"
            else None
        )
        context = None
        freshness: datetime | None = observed or reported
        if config.kind == "configured_membership":
            context, observed, freshness = _membership_context(
                entry, binding, state, now, reported
            )
        return _Observation(
            value,
            entry.entity_id,
            reported,
            observed,
            freshness,
            representation,
            context,
        )


def _source_max_age(config: DatapointConfig, binding: SourceBinding) -> float:
    return (
        config.max_age_seconds
        if binding.max_age_seconds is None
        else binding.max_age_seconds
    )
