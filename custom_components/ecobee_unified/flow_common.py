"""Selectors and row helpers shared by the Ecobee Unified flows."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

HOMEKIT_CLIMATE_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="climate", integration="homekit_controller")
)
ECOBEE_CLIMATE_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="climate", integration="ecobee")
)
HOMEKIT_SENSOR_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", integration="homekit_controller")
)
ECOBEE_SENSOR_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", integration="ecobee")
)
HOMEKIT_SELECT_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="select", integration="homekit_controller")
)
HOMEKIT_BUTTON_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="button", integration="homekit_controller")
)
ECOBEE_NOTIFY_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="notify", integration="ecobee")
)
BOOLEAN_SELECTOR = BooleanSelector()
SOURCE_SLOTS = ("primary", "secondary", "tertiary")


def _single_row(
    rows: list[dict[str, Any]], key: str, value: Any
) -> dict[str, Any] | None:
    """Return the only row whose key matches, or None when absent or ambiguous."""
    matches = [row for row in rows if row.get(key) == value]
    return matches[0] if len(matches) == 1 else None


def _row_selection_schema(rows: list[dict[str, Any]], key: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(key): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=str(row[key]), label=str(row["name"]))
                        for row in rows
                    ]
                )
            )
        }
    )


def _resolved_or_reference(
    registry: er.EntityRegistry, reference: str | None
) -> str | None:
    if reference is None:
        return None
    return er.async_resolve_entity_id(registry, reference) or reference
