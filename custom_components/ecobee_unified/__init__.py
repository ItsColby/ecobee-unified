"""Ecobee Unified integration lifecycle."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_MAPPINGS,
    DOMAIN,
    PLATFORMS,
)
from .manager import MappingManager
from .models import MappingConfig
from .runtime import EcobeeUnifiedConfigEntry, EcobeeUnifiedRuntime


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration package without external I/O."""

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: EcobeeUnifiedConfigEntry
) -> bool:
    """Set up one typed runtime and its climate entities."""

    mappings = tuple(
        MappingConfig.from_dict(item) for item in entry.data[CONF_MAPPINGS]
    )
    manager = MappingManager(hass, entry.entry_id, mappings, entry.options)
    entry.runtime_data = EcobeeUnifiedRuntime(manager)
    await manager.async_start()
    platforms = _platforms_for_mappings(mappings)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
    except Exception:
        await manager.async_stop()
        raise
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EcobeeUnifiedConfigEntry
) -> bool:
    """Unload entities before releasing manager subscriptions."""

    if not await hass.config_entries.async_unload_platforms(
        entry, _platforms_for_mappings(entry.runtime_data.manager.mappings)
    ):
        return False
    await entry.runtime_data.manager.async_stop()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Normalize supported schema revisions without guessing source identity."""

    if entry.version != 1 or entry.minor_version > 3:
        return False
    normalized = [
        MappingConfig.from_dict(item).as_dict()
        for item in entry.data.get(CONF_MAPPINGS, [])
    ]
    if not normalized:
        return False
    hass.config_entries.async_update_entry(
        entry,
        data={CONF_MAPPINGS: normalized},
        options={
            key: value
            for key, value in entry.options.items()
            if key
            in {
                CONF_ECOBEE_STALE_SECONDS,
                CONF_CONFIRMATION_SECONDS,
            }
        },
        version=1,
        minor_version=3,
    )
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove any Repair still owned by a deleted config entry."""

    for item in entry.data.get(CONF_MAPPINGS, []):
        mapping_id = item.get("mapping_id")
        if mapping_id:
            ir.async_delete_issue(hass, DOMAIN, f"mapping_{mapping_id}")


def _platforms_for_mappings(mappings: tuple[MappingConfig, ...]) -> list[str]:
    """Load the notify component only when a mapping exposes that capability."""

    return [
        platform
        for platform in PLATFORMS
        if platform != "notify"
        or any(mapping.ecobee_notify_entity for mapping in mappings)
    ]
