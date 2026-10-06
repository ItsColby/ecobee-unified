"""Multi-source datapoint forms and edit-meaning validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from math import isfinite
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)
from homeassistant.util.unit_conversion import TemperatureConverter

from .datapoints import (
    TEMPERATURE_ABSOLUTE_ZERO,
    DatapointConfig,
    SourceBinding,
    validate_datapoint,
    validate_datapoint_edit_sources,
)
from .flow_common import BOOLEAN_SELECTOR, SOURCE_SLOTS, _resolved_or_reference
from .weather_source import validate_weather_feed

DATAPOINT_DOMAINS = [
    "sensor",
    "binary_sensor",
    "climate",
    "select",
    "number",
    "weather",
]
DATAPOINT_SOURCE_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain=DATAPOINT_DOMAINS)
)
DATAPOINT_KINDS = (
    "temperature",
    "humidity",
    "occupancy",
    "motion",
    "battery",
    "profile",
    "configured_membership",
    "weather",
    "duration",
    "number",
    "text",
)


class _TemperatureBoundSelector(NumberSelector):
    """Keep HA's native number box without coercing booleans into bounds."""

    def __call__(self, data: Any) -> float:
        if isinstance(data, bool):
            raise vol.Invalid("invalid_accepted_range")
        return super().__call__(data)


def _datapoint_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Describe quantities and each ordered source without guessing names."""
    schema: dict[vol.Marker, Any] = {}
    for field, selector, default in (
        ("name", TextSelector(), ""),
        (
            "kind",
            SelectSelector(
                SelectSelectorConfig(
                    options=list(DATAPOINT_KINDS), translation_key="datapoint_kind"
                )
            ),
            "temperature",
        ),
        (
            "semantic",
            SelectSelector(
                SelectSelectorConfig(
                    options=[
                        "physical_temperature",
                        "control_temperature",
                        "weather_temperature",
                        "elapsed_duration",
                        "minimum_fan_runtime_per_hour",
                    ],
                    translation_key="datapoint_semantic",
                )
            ),
            "physical_temperature",
        ),
        (
            "time_basis",
            SelectSelector(
                SelectSelectorConfig(
                    options=["current", "interval"], translation_key="time_basis"
                )
            ),
            "current",
        ),
        (
            "max_age_seconds",
            NumberSelector(
                NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
            ),
            0,
        ),
        ("fallback", BOOLEAN_SELECTOR, True),
    ):
        schema[vol.Required(field, default=defaults.get(field, default))] = selector
    for field, selector in (
        ("unit", TextSelector()),
        (
            "minimum_value",
            _TemperatureBoundSelector(
                NumberSelectorConfig(step="any", mode=NumberSelectorMode.BOX)
            ),
        ),
        (
            "maximum_value",
            _TemperatureBoundSelector(
                NumberSelectorConfig(step="any", mode=NumberSelectorMode.BOX)
            ),
        ),
        (
            "interval_seconds",
            NumberSelector(
                NumberSelectorConfig(min=1, step=1, mode=NumberSelectorMode.BOX)
            ),
        ),
    ):
        schema[
            vol.Optional(field, description={"suggested_value": defaults.get(field)})
        ] = selector
    for slot in SOURCE_SLOTS:
        entity_field = f"{slot}_entity"
        marker = vol.Optional if slot == "tertiary" else vol.Required
        schema[
            marker(
                entity_field,
                description={"suggested_value": defaults.get(entity_field)},
            )
        ] = DATAPOINT_SOURCE_SELECTOR
        for suffix in ("attribute", "timestamp_attribute", "unit"):
            field = f"{slot}_{suffix}"
            schema[
                vol.Optional(
                    field, description={"suggested_value": defaults.get(field)}
                )
            ] = TextSelector()
        age_field = f"{slot}_max_age_seconds"
        schema[
            vol.Optional(
                age_field, description={"suggested_value": defaults.get(age_field)}
            )
        ] = NumberSelector(
            NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
        )
    schema[vol.Required("confirm_equivalence", default=False)] = BOOLEAN_SELECTOR
    return vol.Schema(schema)


def _datapoint_from_input(
    hass: Any, user_input: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    registry = er.async_get(hass)
    name = str(user_input.get("name", "")).strip()
    if not name or len(name) > 64:
        raise vol.Invalid("invalid_name")
    sources = []
    for slot in SOURCE_SLOTS:
        selected = user_input.get(f"{slot}_entity")
        if not selected:
            if slot != "tertiary":
                raise vol.Invalid("datapoint_source_missing")
            continue
        entity_id = er.async_resolve_entity_id(registry, str(selected))
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is None or entry.domain not in DATAPOINT_DOMAINS:
            raise vol.Invalid("datapoint_source_missing")
        sources.append(
            SourceBinding(
                entry.id,
                max_age_seconds=_datapoint_seconds(
                    user_input.get(f"{slot}_max_age_seconds"), optional=True
                ),
                **{
                    suffix: str(user_input.get(f"{slot}_{suffix}", "")).strip() or None
                    for suffix in ("attribute", "timestamp_attribute", "unit")
                },
            )
        )
    kind = str(user_input.get("kind", ""))
    config = DatapointConfig.from_dict(
        {
            "datapoint_id": str(current["datapoint_id"]) if current else uuid4().hex,
            "name": name,
            "kind": kind,
            "sources": [source.as_dict() for source in sources],
            "unit": str(user_input.get("unit", "")).strip() or None,
            "semantic": str(user_input.get("semantic", ""))
            if kind in {"temperature", "duration"}
            else (current or {}).get("semantic"),
            "time_basis": str(user_input.get("time_basis", "current")),
            "interval_seconds": _datapoint_seconds(
                user_input.get("interval_seconds"), optional=True, minimum=1
            ),
            "max_age_seconds": _datapoint_seconds(user_input.get("max_age_seconds", 0)),
            "fallback": user_input.get("fallback", True),
            **_weather_datapoint_identity(hass, sources),
        }
    )
    if current is not None:
        _validate_datapoint_edit_meaning(
            hass, DatapointConfig.from_dict(current), config
        )
    config = _datapoint_with_range(config, user_input, current)
    validate_datapoint(hass, config)
    if not user_input.get("confirm_equivalence", False) and (
        current is None or _datapoint_contract_changed(current, config)
    ):
        raise vol.Invalid("datapoint_equivalence_required")
    result = deepcopy(current or {})
    canonical = config.as_dict()
    # Clear nullable owned fields before overlay; retain unknown future fields.
    for field in (
        "unit",
        "interval_seconds",
        "semantic",
        "minimum_value",
        "maximum_value",
    ):
        result.pop(field, None)
    result.update(canonical)
    old_sources = {
        (source["entity"], source.get("attribute")): source
        for source in (current or {}).get("sources", [])
    }
    result["sources"] = [
        {
            **{
                key: value
                for key, value in old_sources.get(
                    (source["entity"], source.get("attribute")), {}
                ).items()
                if key
                not in {
                    "entity",
                    "attribute",
                    "timestamp_attribute",
                    "unit",
                    "max_age_seconds",
                }
            },
            **source,
        }
        for source in canonical["sources"]
    ]
    return result


def _datapoint_with_range(
    config: DatapointConfig,
    user_input: dict[str, Any],
    current: dict[str, Any] | None,
) -> DatapointConfig:
    """Apply bounds in output units without reinterpreting an existing range."""
    bounds = {
        field: _datapoint_bound(user_input.get(field))
        for field in ("minimum_value", "maximum_value")
    }
    if current is not None:
        previous = DatapointConfig.from_dict(current)
        if previous.unit != config.unit and any(
            getattr(previous, field) is not None for field in bounds
        ):
            if any(bounds[field] != getattr(previous, field) for field in bounds):
                raise vol.Invalid("datapoint_range_unit_change")
            assert previous.unit is not None and config.unit is not None
            bounds = {
                field: None
                if value is None
                else TEMPERATURE_ABSOLUTE_ZERO[config.unit]
                if value == TEMPERATURE_ABSOLUTE_ZERO[previous.unit]
                else TemperatureConverter.convert(value, previous.unit, config.unit)
                for field, value in bounds.items()
            }
    return replace(
        config,
        minimum_value=bounds["minimum_value"],
        maximum_value=bounds["maximum_value"],
    )


def _finite_float(value: Any, message: str) -> float:
    """Return one finite non-boolean number or raise one form validation error."""

    if isinstance(value, bool):
        raise vol.Invalid(message)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid(message) from err
    if not isfinite(number):
        raise vol.Invalid(message)
    return number


def _datapoint_bound(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _finite_float(value, "invalid_accepted_range")


def _validate_datapoint_edit_meaning(
    hass: HomeAssistant, previous: DatapointConfig, current: DatapointConfig
) -> None:
    """Keep one subject, quantity, role and time meaning behind a Recorder identity."""
    if (
        previous.kind != current.kind
        or (previous.semantic or previous.kind) != (current.semantic or current.kind)
        or previous.time_basis != current.time_basis
        or previous.interval_seconds != current.interval_seconds
        or (
            previous.unit != current.unit
            and current.kind not in {"temperature", "duration"}
        )
    ):
        raise vol.Invalid("datapoint_meaning_change")
    # Generic numbers/text have no narrower native quantity contract. A different
    # binding may carry a different meaning even when its unit or value matches.
    if current.kind in {"number", "text"} and {
        (source.entity, source.attribute) for source in previous.sources
    } != {(source.entity, source.attribute) for source in current.sources}:
        raise vol.Invalid("datapoint_meaning_change")
    validate_datapoint_edit_sources(hass, previous, current)


def _weather_datapoint_identity(
    hass: Any, sources: list[SourceBinding]
) -> dict[str, str]:
    """Capture the native station feed identity for explicitly selected weather aliases."""
    registry = er.async_get(hass)
    entries = [
        registry.async_get(entity_id)
        if (entity_id := er.async_resolve_entity_id(registry, binding.entity))
        else None
        for binding in sources
    ]
    if not any(entry is not None and entry.domain == "weather" for entry in entries):
        return {}
    if any(entry is None or entry.domain != "weather" for entry in entries):
        raise ValueError("weather_sources_required")
    observations = []
    for entry in entries:
        assert entry is not None
        state = hass.states.get(entry.entity_id)
        if state is None:
            raise ValueError("source_missing")
        observations.append((entry, state))
    identity = validate_weather_feed(observations)
    return {
        "weather_station": identity.station,
        "weather_config_entry_id": identity.config_entry_id,
    }


def _datapoint_seconds(
    value: Any, *, optional: bool = False, minimum: int = 0
) -> int | None:
    if optional and value in (None, ""):
        return None
    number = _finite_float(value, "datapoint_invalid_timing")
    if number != int(number) or number < minimum:
        raise vol.Invalid("datapoint_invalid_timing")
    return int(number)


def _datapoint_contract_changed(
    current: dict[str, Any], config: DatapointConfig
) -> bool:
    previous = DatapointConfig.from_dict(current)
    source_changes = [
        tuple(
            (source.entity, source.attribute, source.timestamp_attribute, source.unit)
            for source in item.sources
        )
        for item in (previous, config)
    ]
    return source_changes[0] != source_changes[1] or any(
        getattr(previous, field) != getattr(config, field)
        for field in (
            "kind",
            "semantic",
            "unit",
            "time_basis",
            "interval_seconds",
            "weather_station",
            "weather_config_entry_id",
        )
    )


def _validate_datapoint_collection(
    rows: list[dict[str, Any]], candidate: dict[str, Any]
) -> None:
    if any(row.get("datapoint_id") == candidate["datapoint_id"] for row in rows):
        raise vol.Invalid("invalid_datapoint_identity")
    for row in rows:
        if str(row.get("name", "")).strip().casefold() == candidate["name"].casefold():
            raise vol.Invalid("duplicate_datapoint_name")


def _datapoint_form_defaults(hass: Any, row: dict[str, Any]) -> dict[str, Any]:
    config = DatapointConfig.from_dict(row)
    defaults = {
        field: getattr(config, field)
        for field in (
            "name",
            "kind",
            "semantic",
            "unit",
            "time_basis",
            "interval_seconds",
            "max_age_seconds",
            "fallback",
            "minimum_value",
            "maximum_value",
        )
        if getattr(config, field) is not None
    }
    if config.kind not in {"temperature", "duration"}:
        defaults["semantic"] = "physical_temperature"
    registry = er.async_get(hass)
    for slot, source in zip(SOURCE_SLOTS, config.sources, strict=False):
        values = source.as_dict()
        reference = values["entity"]
        defaults[f"{slot}_entity"] = _resolved_or_reference(registry, reference)
        for field in ("attribute", "timestamp_attribute", "unit", "max_age_seconds"):
            if values.get(field) is not None:
                defaults[f"{slot}_{field}"] = values[field]
    return defaults
