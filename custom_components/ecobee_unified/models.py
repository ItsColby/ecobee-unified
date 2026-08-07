"""Pure data models and normalization for Ecobee Unified."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isclose, isfinite
from types import MappingProxyType
from typing import Any

from .const import MAX_ATTRIBUTE_ITEMS, MAX_ATTRIBUTE_TEXT

UNAVAILABLE_STATES = frozenset({"unknown", "unavailable"})
HVAC_MODES = frozenset({"off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"})
HVAC_ACTIONS = frozenset(
    {"cooling", "defrosting", "drying", "fan", "heating", "idle", "off", "preheating"}
)
TEMPERATURE_UNITS = frozenset({"°C", "°F", "K"})


class SourceHealth(StrEnum):
    """Health of one explicitly mapped source."""

    HEALTHY = "healthy"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"


class CommandStatus(StrEnum):
    """Bounded status for the most recent command."""

    NONE = "none"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MappingConfig:
    """Stable identity and explicit sources for one thermostat mapping."""

    mapping_id: str
    name: str
    homekit_entity: str
    ecobee_entity: str
    homekit_preset_entity: str | None = None
    homekit_clear_hold_entity: str | None = None
    ecobee_aqi_entity: str | None = None
    ecobee_co2_entity: str | None = None
    ecobee_voc_entity: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MappingConfig:
        """Create a validated mapping from config-entry data."""

        return cls(
            mapping_id=str(value["mapping_id"]),
            name=str(value["name"]),
            homekit_entity=str(value["homekit_entity"]),
            ecobee_entity=str(value["ecobee_entity"]),
            homekit_preset_entity=_optional_text(value.get("homekit_preset_entity")),
            homekit_clear_hold_entity=_optional_text(
                value.get("homekit_clear_hold_entity")
            ),
            ecobee_aqi_entity=_optional_text(value.get("ecobee_aqi_entity")),
            ecobee_co2_entity=_optional_text(value.get("ecobee_co2_entity")),
            ecobee_voc_entity=_optional_text(value.get("ecobee_voc_entity")),
        )

    def as_dict(self) -> dict[str, str]:
        """Serialize for config-entry storage."""

        result = {
            "mapping_id": self.mapping_id,
            "name": self.name,
            "homekit_entity": self.homekit_entity,
            "ecobee_entity": self.ecobee_entity,
        }
        result.update(
            {
                key: value
                for key, value in (
                    ("homekit_preset_entity", self.homekit_preset_entity),
                    ("homekit_clear_hold_entity", self.homekit_clear_hold_entity),
                    ("ecobee_aqi_entity", self.ecobee_aqi_entity),
                    ("ecobee_co2_entity", self.ecobee_co2_entity),
                    ("ecobee_voc_entity", self.ecobee_voc_entity),
                )
                if value
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class RawSource:
    """Bounded state-machine input for one selected source."""

    state: str | None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    age_seconds: int | None = None
    health: SourceHealth = SourceHealth.MISSING

    @property
    def usable(self) -> bool:
        """Return whether this source can supply current semantics."""

        return (
            self.health is SourceHealth.HEALTHY
            and self.state is not None
            and self.state not in UNAVAILABLE_STATES
        )


@dataclass(frozen=True, slots=True)
class CommandSummary:
    """Privacy-safe recent command state projected into snapshots."""

    revision: int = 0
    operation: str | None = None
    status: CommandStatus = CommandStatus.NONE
    age_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedSnapshot:
    """Single immutable projection input for one thermostat mapping."""

    mapping_id: str
    available: bool
    homekit_writable: bool
    homekit_preset_writable: bool
    ecobee_writable: bool
    hvac_mode: str | None
    hvac_action: str | None
    current_temperature: float | None
    current_humidity: float | None
    target_temperature: float | None
    target_temperature_low: float | None
    target_temperature_high: float | None
    fan_mode: str | None
    hvac_modes: tuple[str, ...]
    fan_modes: tuple[str, ...]
    supported_features: int
    min_temp: float | None
    max_temp: float | None
    target_temperature_step: float | None
    temperature_unit: str | None
    preset_mode: str | None
    preset_modes: tuple[str, ...]
    ecobee_preset_mode: str | None
    climate_mode: str | None
    equipment_running: str | None
    active_sensors: tuple[str, ...]
    minimum_fan_runtime: int | None
    air_quality_index: float | None
    co2: float | None
    voc: float | None
    source_health: Mapping[str, SourceHealth]
    source_ages: Mapping[str, int | None]
    provenance: Mapping[str, str]
    confirmation_values: Mapping[str, Any]
    degradation: tuple[str, ...]
    command: CommandSummary


STANDARD_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("hvac_action", "hvac_action", "hvac_action"),
    ("current_temperature", "current_temperature", "number"),
    ("current_humidity", "current_humidity", "humidity"),
    ("target_temperature", "temperature", "number"),
    ("target_temperature_low", "target_temp_low", "number"),
    ("target_temperature_high", "target_temp_high", "number"),
    ("fan_mode", "fan_mode", "text"),
    ("min_temp", "min_temp", "number"),
    ("max_temp", "max_temp", "number"),
    ("target_temperature_step", "target_temp_step", "positive_number"),
    ("temperature_unit", "unit_of_measurement", "temperature_unit"),
)


def build_snapshot(
    mapping_id: str,
    homekit: RawSource,
    ecobee: RawSource,
    homekit_preset: RawSource | None = None,
    air_quality_index: RawSource | None = None,
    co2: RawSource | None = None,
    voc: RawSource | None = None,
    command: CommandSummary | None = None,
) -> NormalizedSnapshot:
    """Normalize each selected source exactly once with deterministic ownership."""

    values: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    degradation: set[str] = set()

    hvac_mode, owner = _select_state(homekit, ecobee)
    values["hvac_mode"] = hvac_mode
    if owner:
        provenance["hvac_mode"] = owner
        if owner == "ecobee":
            degradation.add("homekit_read_fallback")
    else:
        degradation.add("hvac_mode_unavailable")

    for target, attribute, value_type in STANDARD_FIELDS:
        value, owner = _select_attribute(homekit, ecobee, attribute, value_type)
        values[target] = value
        if owner:
            provenance[target] = owner
            if owner == "ecobee":
                degradation.add("homekit_read_fallback")

    current_temperature = values["current_temperature"]
    if current_temperature is None:
        degradation.add("current_temperature_unavailable")

    primary_capabilities = homekit if homekit.usable else ecobee
    hvac_modes = _bounded_strings(primary_capabilities.attributes.get("hvac_modes"))
    fan_modes = _bounded_strings(primary_capabilities.attributes.get("fan_modes"))
    supported_features = _integer(
        homekit.attributes.get("supported_features") if homekit.usable else 0
    )

    source_health = MappingProxyType(
        {
            "homekit": homekit.health,
            "ecobee": ecobee.health,
            "homekit_preset": _optional_health(homekit_preset),
            "air_quality_index": _optional_health(air_quality_index),
            "co2": _optional_health(co2),
            "voc": _optional_health(voc),
        }
    )
    source_ages = MappingProxyType(
        {
            "homekit": homekit.age_seconds,
            "ecobee": ecobee.age_seconds,
            "homekit_preset": _optional_age(homekit_preset),
            "air_quality_index": _optional_age(air_quality_index),
            "co2": _optional_age(co2),
            "voc": _optional_age(voc),
        }
    )

    if not ecobee.usable:
        degradation.add("ecobee_vendor_context_unavailable")
    for source_name, source in (
        ("homekit_preset", homekit_preset),
        ("air_quality_index", air_quality_index),
        ("co2", co2),
        ("voc", voc),
    ):
        if source is not None and not source.usable:
            degradation.add(f"{source_name}_unavailable")

    available = hvac_mode is not None and current_temperature is not None
    if not available:
        degradation.add("required_climate_semantics_unavailable")

    return NormalizedSnapshot(
        mapping_id=mapping_id,
        available=available,
        homekit_writable=homekit.usable,
        homekit_preset_writable=bool(homekit_preset and homekit_preset.usable),
        ecobee_writable=ecobee.usable,
        hvac_mode=hvac_mode,
        hvac_action=values["hvac_action"],
        current_temperature=current_temperature,
        current_humidity=values["current_humidity"],
        target_temperature=values["target_temperature"],
        target_temperature_low=values["target_temperature_low"],
        target_temperature_high=values["target_temperature_high"],
        fan_mode=values["fan_mode"],
        hvac_modes=hvac_modes,
        fan_modes=fan_modes,
        supported_features=supported_features,
        min_temp=values["min_temp"],
        max_temp=values["max_temp"],
        target_temperature_step=values["target_temperature_step"],
        temperature_unit=values["temperature_unit"],
        preset_mode=_optional_source_state(homekit_preset),
        preset_modes=(
            _bounded_strings(homekit_preset.attributes.get("options"))
            if homekit_preset and homekit_preset.usable
            else ()
        ),
        ecobee_preset_mode=(
            _bounded_text(ecobee.attributes.get("preset_mode"))
            if ecobee.usable
            else None
        ),
        climate_mode=_bounded_text(ecobee.attributes.get("climate_mode"))
        if ecobee.usable
        else None,
        equipment_running=_bounded_text(ecobee.attributes.get("equipment_running"))
        if ecobee.usable
        else None,
        active_sensors=_bounded_strings(
            ecobee.attributes.get("active_sensors")
            or ecobee.attributes.get("active_comfort_sensors")
        )
        if ecobee.usable
        else (),
        minimum_fan_runtime=_bounded_integer(
            ecobee.attributes.get("fan_min_on_time"), 0, 60
        )
        if ecobee.usable
        else None,
        air_quality_index=_optional_source_number(air_quality_index),
        co2=_optional_source_number(co2),
        voc=_optional_source_number(voc),
        source_health=source_health,
        source_ages=source_ages,
        provenance=MappingProxyType(provenance),
        confirmation_values=MappingProxyType(
            _confirmation_values(ecobee, homekit_preset)
        ),
        degradation=tuple(sorted(degradation)),
        command=command or CommandSummary(),
    )


def command_matches(snapshot: NormalizedSnapshot, expected: Mapping[str, Any]) -> bool:
    """Return whether an Ecobee observation confirms the current revision."""

    for field_name, wanted in expected.items():
        actual = snapshot.confirmation_values.get(field_name)
        if wanted == "__not_off__":
            if actual in {None, "off"}:
                return False
        elif wanted == "__reported__":
            if actual is None:
                return False
        elif isinstance(wanted, int | float) and isinstance(actual, int | float):
            if not isclose(float(actual), float(wanted), abs_tol=0.11):
                return False
        elif actual != wanted:
            return False
    return bool(expected)


def _confirmation_values(
    ecobee: RawSource, homekit_preset: RawSource | None
) -> dict[str, Any]:
    """Normalize only fields used to observe a current command revision."""

    values: dict[str, Any] = {}
    if ecobee.usable:
        values.update(
            {
                "hvac_mode": _hvac_mode(ecobee.state),
                "target_temperature": _number(ecobee.attributes.get("temperature")),
                "target_temperature_low": _number(
                    ecobee.attributes.get("target_temp_low")
                ),
                "target_temperature_high": _number(
                    ecobee.attributes.get("target_temp_high")
                ),
                "fan_mode": _bounded_text(ecobee.attributes.get("fan_mode")),
                "minimum_fan_runtime": _bounded_integer(
                    ecobee.attributes.get("fan_min_on_time"), 0, 60
                ),
            }
        )
    if homekit_preset is not None and homekit_preset.usable:
        values["preset_mode"] = _bounded_text(homekit_preset.state)
    return {key: value for key, value in values.items() if value is not None}


def _select_state(
    primary: RawSource, fallback: RawSource
) -> tuple[str | None, str | None]:
    primary_value = _hvac_mode(primary.state) if primary.usable else None
    if primary_value is not None:
        return primary_value, "homekit"
    fallback_value = _hvac_mode(fallback.state) if fallback.usable else None
    if fallback_value is not None:
        return fallback_value, "ecobee"
    return None, None


def _select_attribute(
    primary: RawSource, fallback: RawSource, key: str, value_type: str
) -> tuple[Any, str | None]:
    primary_value = (
        _normalize_field(primary.attributes.get(key), value_type)
        if primary.usable
        else None
    )
    if primary_value is not None:
        return primary_value, "homekit"
    fallback_value = (
        _normalize_field(fallback.attributes.get(key), value_type)
        if fallback.usable
        else None
    )
    if fallback_value is not None:
        return fallback_value, "ecobee"
    return None, None


def _normalize_field(value: Any, value_type: str) -> Any:
    if value_type == "number":
        return _number(value)
    if value_type == "positive_number":
        number = _number(value)
        return number if number is not None and number > 0 else None
    if value_type == "humidity":
        number = _number(value)
        return number if number is not None and 0 <= number <= 100 else None
    if value_type == "hvac_action":
        text = _text(value)
        return text if text in HVAC_ACTIONS else None
    if value_type == "temperature_unit":
        text = _text(value)
        return text if text in TEMPERATURE_UNITS else None
    if value_type == "text":
        return _bounded_text(value)
    raise ValueError(f"Unknown field normalizer: {value_type}")


def _hvac_mode(value: Any) -> str | None:
    text = _text(value)
    return text if text in HVAC_MODES else None


def _text(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str) and value and value not in UNAVAILABLE_STATES
        else None
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_text(value: Any) -> str | None:
    text = _text(value)
    return text[:MAX_ATTRIBUTE_TEXT] if text else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)


def _bounded_integer(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = int(value)
    return number if minimum <= number <= maximum else None


def _bounded_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    result: list[str] = []
    for item in value:
        text = _bounded_text(item)
        if text and text not in result:
            result.append(text)
        if len(result) == MAX_ATTRIBUTE_ITEMS:
            break
    return tuple(result)


def _optional_source_state(source: RawSource | None) -> str | None:
    return _bounded_text(source.state) if source and source.usable else None


def _optional_source_number(source: RawSource | None) -> float | None:
    if source is None or not source.usable or source.state is None:
        return None
    try:
        value = float(source.state)
    except ValueError:
        return None
    return value if isfinite(value) else None


def _optional_health(source: RawSource | None) -> SourceHealth:
    return source.health if source is not None else SourceHealth.MISSING


def _optional_age(source: RawSource | None) -> int | None:
    return source.age_seconds if source is not None else None
