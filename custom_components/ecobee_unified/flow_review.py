"""Changed-only review text for staged reconfiguration."""

from __future__ import annotations

import re
from html import escape
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
    CONF_ECOBEE_VOC_ENTITY,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_HOMEKIT_TEMPERATURE_ENTITY,
    CONF_MAPPING_ID,
    CONF_MAPPINGS,
    DOMAIN,
    NAME,
)
from .flow_mappings import OPTIONAL_SOURCE_KEYS


def _review_text(value: Any) -> str:
    """Keep source-owned names and attributes as literal Markdown text."""
    text = escape(" ".join(str(value).split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~])", r"\\\1", text)


def _review_entity(
    hass: HomeAssistant, reference: Any, ambiguous: set[str] | None = None
) -> str:
    if not reference:
        return "Not selected"
    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, str(reference))
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None:
        return str(reference) if "." in str(reference) else "Unavailable saved source"
    state = hass.states.get(entry.entity_id)
    name = (
        entry.name
        or (state.attributes.get("friendly_name") if state else None)
        or entry.original_name
        or entry.entity_id
    )
    platform = {
        "homekit_controller": "HomeKit Device",
        "ecobee": "Ecobee",
        "beestat_statistics": "Beestat Statistics",
        DOMAIN: NAME,
        "battery_notes": "Battery Notes",
    }.get(entry.platform, entry.platform.replace("_", " "))
    label = f"{name} ({platform})"
    # Native entity IDs disambiguate equal friendly names only when necessary.
    return f"{label} [{entry.entity_id}]" if ambiguous and label in ambiguous else label


def _review_age(value: Any) -> str:
    if value is None:
        return "inherit default"
    return "no age expiry" if value == 0 else f"{value:g} seconds"


def _review_sources(
    hass: HomeAssistant, row: dict[str, Any], ambiguous: set[str]
) -> str:
    sources = []
    for index, source in enumerate(row.get("sources", []), 1):
        name = _review_entity(hass, source.get("entity"), ambiguous)
        details = [source.get("attribute") or "state"]
        if source.get("unit"):
            details.append(f"declared unit: {source['unit']}")
        if source.get("timestamp_attribute"):
            details.append(f"timestamp: {source['timestamp_attribute']}")
        details.append(f"maximum age: {_review_age(source.get('max_age_seconds'))}")
        sources.append(f"{index}. {name} — {', '.join(details)}")
    return "; ".join(sources)


def _review_details(
    hass: HomeAssistant, collection: str, row: dict[str, Any], ambiguous: set[str]
) -> dict[str, str]:
    details = {"Name": str(row.get("name", "Unnamed"))}
    if collection == CONF_MAPPINGS:
        for field, label in (
            (CONF_HOMEKIT_ENTITY, "HomeKit thermostat"),
            (CONF_ECOBEE_ENTITY, "Ecobee thermostat"),
            (CONF_HOMEKIT_PRESET_ENTITY, "Comfort profile action"),
            (CONF_HOMEKIT_CLEAR_HOLD_ENTITY, "Clear hold action"),
            (CONF_HOMEKIT_TEMPERATURE_ENTITY, "Physical temperature"),
            (CONF_ECOBEE_AQI_ENTITY, "Air quality index"),
            (CONF_ECOBEE_CO2_ENTITY, "Carbon dioxide"),
            (CONF_ECOBEE_VOC_ENTITY, "VOC"),
            (CONF_ECOBEE_NOTIFY_ENTITY, "Notification action"),
        ):
            details[label] = _review_entity(hass, row.get(field), ambiguous)
        return details
    details["Meaning"] = str(row.get("semantic") or row.get("kind")).replace("_", " ")
    details["Output unit"] = row.get("unit") or "unitless"
    details["Sources in order"] = _review_sources(hass, row, ambiguous)
    details["Time basis"] = (
        f"fixed interval: {row.get('interval_seconds'):g} seconds"
        if row.get("time_basis") == "interval"
        else "current observation"
    )
    details["Default maximum age"] = _review_age(row.get("max_age_seconds", 0))
    details["Fallback"] = (
        "use the next valid source" if row.get("fallback", True) else "primary only"
    )
    lower, upper = row.get("minimum_value"), row.get("maximum_value")
    details["Accepted range"] = (
        "no additional bounds"
        if lower is None and upper is None
        else f"{lower if lower is not None else 'no minimum'} to "
        f"{upper if upper is not None else 'no maximum'} {row.get('unit') or ''}"
    )
    return details


def _review_ambiguous_entities(
    hass: HomeAssistant, *rows: dict[str, Any] | None
) -> set[str]:
    labels: dict[str, set[str]] = {}
    for row in rows:
        if row is None:
            continue
        references = [
            row.get(field)
            for field in (
                CONF_HOMEKIT_ENTITY,
                CONF_ECOBEE_ENTITY,
                *OPTIONAL_SOURCE_KEYS,
            )
        ]
        references.extend(source.get("entity") for source in row.get("sources", []))
        for reference in references:
            if reference:
                labels.setdefault(_review_entity(hass, reference), set()).add(reference)
    return {label for label, references in labels.items() if len(references) > 1}


def _reconfigure_summary(
    hass: HomeAssistant, original: dict[str, Any], pending: dict[str, Any]
) -> str:
    """Describe changed units without exposing storage identities or config JSON."""
    sections = []
    for collection, identity, label in (
        (CONF_MAPPINGS, CONF_MAPPING_ID, "Thermostat"),
        ("datapoints", "datapoint_id", "Datapoint"),
    ):
        before = {row[identity]: row for row in original.get(collection, [])}
        after = {row[identity]: row for row in pending.get(collection, [])}
        for key in dict.fromkeys((*before, *after)):
            previous, current = before.get(key), after.get(key)
            if previous == current:
                continue
            row = current if current is not None else previous
            assert row is not None
            action = (
                "Added"
                if previous is None
                else "Removed"
                if current is None
                else "Changed"
            )
            ambiguous = _review_ambiguous_entities(hass, previous, current)
            old = (
                _review_details(hass, collection, previous, ambiguous)
                if previous
                else {}
            )
            new = (
                _review_details(hass, collection, current, ambiguous) if current else {}
            )
            lines = [
                f"**{action} {label}: {_review_text(row.get('name', 'Unnamed'))}**"
            ]
            for field in dict.fromkeys((*old, *new)):
                if previous is not None and current is not None:
                    if old.get(field) == new.get(field):
                        continue
                    value = f"{old.get(field, 'Not selected')} → {new.get(field, 'Not selected')}"
                else:
                    if field == "Name":
                        continue
                    value = new.get(field) or old[field]
                    if value == "Not selected":
                        continue
                lines.append(f"- {field}: {_review_text(value)}")
            if len(lines) == 1:
                lines.append("- Saved source details updated.")
            sections.append("\n".join(lines))
    return "\n\n".join(sections)
