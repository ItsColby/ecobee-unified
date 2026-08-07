"""Pure data models and normalization for Ecobee Unified."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isclose
from types import MappingProxyType
from typing import Any

from .const import MAX_ATTRIBUTE_ITEMS, MAX_ATTRIBUTE_TEXT

UNAVAILABLE_STATES = frozenset({"unknown", "unavailable"})


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
    scheduled_profile_entity: str | None = None
    next_transition_entity: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MappingConfig:
        """Create a validated mapping from config-entry data."""

        return cls(
            mapping_id=str(value["mapping_id"]),
            name=str(value["name"]),
            homekit_entity=str(value["homekit_entity"]),
            ecobee_entity=str(value["ecobee_entity"]),
            scheduled_profile_entity=_optional_text(
                value.get("scheduled_profile_entity")
            ),
            next_transition_entity=_optional_text(value.get("next_transition_entity")),
        )

    def as_dict(self) -> dict[str, str]:
        """Serialize for config-entry storage."""

        result = {
            "mapping_id": self.mapping_id,
            "name": self.name,
            "homekit_entity": self.homekit_entity,
            "ecobee_entity": self.ecobee_entity,
        }
        if self.scheduled_profile_entity:
            result["scheduled_profile_entity"] = self.scheduled_profile_entity
        if self.next_transition_entity:
            result["next_transition_entity"] = self.next_transition_entity
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
    equipment_running: str | None
    active_sensors: tuple[str, ...]
    minimum_fan_runtime: int | None
    scheduled_profile: str | None
    next_transition: str | None
    source_health: Mapping[str, SourceHealth]
    source_ages: Mapping[str, int | None]
    provenance: Mapping[str, str]
    confirmation_values: Mapping[str, Any]
    degradation: tuple[str, ...]
    command: CommandSummary


STANDARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("hvac_action", "hvac_action"),
    ("current_temperature", "current_temperature"),
    ("current_humidity", "current_humidity"),
    ("target_temperature", "temperature"),
    ("target_temperature_low", "target_temp_low"),
    ("target_temperature_high", "target_temp_high"),
    ("fan_mode", "fan_mode"),
    ("min_temp", "min_temp"),
    ("max_temp", "max_temp"),
    ("target_temperature_step", "target_temp_step"),
    ("temperature_unit", "unit_of_measurement"),
)


def build_snapshot(
    mapping_id: str,
    homekit: RawSource,
    ecobee: RawSource,
    scheduled_profile: RawSource | None = None,
    next_transition: RawSource | None = None,
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

    for target, attribute in STANDARD_FIELDS:
        value, owner = _select_attribute(homekit, ecobee, attribute)
        values[target] = value
        if owner:
            provenance[target] = owner
            if owner == "ecobee":
                degradation.add("homekit_read_fallback")

    current_temperature = _number(values["current_temperature"])
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
            "beestat": _combined_optional_health(scheduled_profile, next_transition),
        }
    )
    source_ages = MappingProxyType(
        {
            "homekit": homekit.age_seconds,
            "ecobee": ecobee.age_seconds,
            "beestat": _youngest_age(scheduled_profile, next_transition),
        }
    )

    available = hvac_mode is not None and current_temperature is not None
    if not available:
        degradation.add("required_climate_semantics_unavailable")

    return NormalizedSnapshot(
        mapping_id=mapping_id,
        available=available,
        homekit_writable=homekit.usable,
        hvac_mode=hvac_mode,
        hvac_action=_text(values["hvac_action"]),
        current_temperature=current_temperature,
        current_humidity=_number(values["current_humidity"]),
        target_temperature=_number(values["target_temperature"]),
        target_temperature_low=_number(values["target_temperature_low"]),
        target_temperature_high=_number(values["target_temperature_high"]),
        fan_mode=_text(values["fan_mode"]),
        hvac_modes=hvac_modes,
        fan_modes=fan_modes,
        supported_features=supported_features,
        min_temp=_number(values["min_temp"]),
        max_temp=_number(values["max_temp"]),
        target_temperature_step=_number(values["target_temperature_step"]),
        temperature_unit=_text(values["temperature_unit"]),
        preset_mode=_text(ecobee.attributes.get("preset_mode"))
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
        minimum_fan_runtime=_integer(ecobee.attributes.get("fan_min_on_time"))
        if ecobee.usable
        else None,
        scheduled_profile=_optional_source_state(scheduled_profile),
        next_transition=_optional_source_state(next_transition),
        source_health=source_health,
        source_ages=source_ages,
        provenance=MappingProxyType(provenance),
        confirmation_values=MappingProxyType(_confirmation_values(ecobee)),
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
        elif isinstance(wanted, int | float) and isinstance(actual, int | float):
            if not isclose(float(actual), float(wanted), abs_tol=0.11):
                return False
        elif actual != wanted:
            return False
    return bool(expected)


def _confirmation_values(ecobee: RawSource) -> dict[str, Any]:
    """Normalize only Ecobee fields used to observe HomeKit commands."""

    if not ecobee.usable:
        return {}
    return {
        "hvac_mode": ecobee.state,
        "target_temperature": _number(ecobee.attributes.get("temperature")),
        "target_temperature_low": _number(ecobee.attributes.get("target_temp_low")),
        "target_temperature_high": _number(ecobee.attributes.get("target_temp_high")),
        "fan_mode": _text(ecobee.attributes.get("fan_mode")),
    }


def _select_state(
    primary: RawSource, fallback: RawSource
) -> tuple[str | None, str | None]:
    if primary.usable:
        return primary.state, "homekit"
    if fallback.usable:
        return fallback.state, "ecobee"
    return None, None


def _select_attribute(
    primary: RawSource, fallback: RawSource, key: str
) -> tuple[Any, str | None]:
    if primary.usable and _present(primary.attributes.get(key)):
        return primary.attributes[key], "homekit"
    if fallback.usable and _present(fallback.attributes.get(key)):
        return fallback.attributes[key], "ecobee"
    return None, None


def _present(value: Any) -> bool:
    return value is not None and value not in UNAVAILABLE_STATES


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value not in UNAVAILABLE_STATES else None


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_text(value: Any) -> str | None:
    text = _text(value)
    return text[:MAX_ATTRIBUTE_TEXT] if text else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)


def _bounded_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(
        item[:MAX_ATTRIBUTE_TEXT]
        for item in value[:MAX_ATTRIBUTE_ITEMS]
        if isinstance(item, str)
    )


def _optional_source_state(source: RawSource | None) -> str | None:
    return _bounded_text(source.state) if source and source.usable else None


def _combined_optional_health(
    first: RawSource | None, second: RawSource | None
) -> SourceHealth:
    selected = [source for source in (first, second) if source is not None]
    if not selected:
        return SourceHealth.MISSING
    if any(source.health is SourceHealth.HEALTHY for source in selected):
        return SourceHealth.HEALTHY
    if any(source.health is SourceHealth.STALE for source in selected):
        return SourceHealth.STALE
    if any(source.health is SourceHealth.UNAVAILABLE for source in selected):
        return SourceHealth.UNAVAILABLE
    return SourceHealth.MISSING


def _youngest_age(first: RawSource | None, second: RawSource | None) -> int | None:
    ages = [
        source.age_seconds
        for source in (first, second)
        if source is not None and source.age_seconds is not None
    ]
    return min(ages) if ages else None
