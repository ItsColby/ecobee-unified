"""Ecobee Unified integration lifecycle."""

from __future__ import annotations

import logging
from asyncio import CancelledError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_MAPPINGS,
    DOMAIN,
    PLATFORMS,
    SUFFIX_AIR_QUALITY_INDEX,
    SUFFIX_CO2,
    SUFFIX_EQUIPMENT_STAGE,
    SUFFIX_MINIMUM_FAN_RUNTIME,
    SUFFIX_NOTIFICATION,
    SUFFIX_RESUME_PROGRAM,
    SUFFIX_SOURCE_DEGRADED,
    SUFFIX_VOC,
)
from .datapoints import BINARY_KINDS, DatapointConfig, DatapointManager
from .manager import MappingManager
from .models import MappingConfig
from .runtime import EcobeeUnifiedConfigEntry, EcobeeUnifiedRuntime

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(
    hass: HomeAssistant, entry: EcobeeUnifiedConfigEntry
) -> bool:
    """Set up one typed runtime and its climate entities."""

    mappings = tuple(
        MappingConfig.from_dict(item) for item in entry.data[CONF_MAPPINGS]
    )
    manager = MappingManager(hass, entry.entry_id, mappings, entry.options)
    datapoint_configs = tuple(
        DatapointConfig.from_dict(item) for item in entry.data.get("datapoints", [])
    )
    datapoints = (
        DatapointManager(hass, entry.entry_id, datapoint_configs)
        if datapoint_configs
        else None
    )
    entry.runtime_data = EcobeeUnifiedRuntime(manager, datapoints)
    _remove_orphaned_entities(hass, entry, mappings, datapoint_configs)
    platforms = _platforms_for_mappings(mappings, datapoint_configs)
    try:
        await manager.async_start()
        if datapoints is not None:
            await datapoints.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
    except Exception, CancelledError:
        try:
            await hass.config_entries.async_unload_platforms(entry, platforms)
        except Exception, CancelledError:
            _LOGGER.exception("Error unloading platforms after setup failure")
        finally:
            await manager.async_stop()
            if datapoints is not None:
                await datapoints.async_stop()
        raise
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EcobeeUnifiedConfigEntry
) -> bool:
    """Unload entities before releasing manager subscriptions."""

    if not await hass.config_entries.async_unload_platforms(
        entry,
        _platforms_for_mappings(
            entry.runtime_data.manager.mappings,
            entry.runtime_data.datapoints.configs
            if entry.runtime_data.datapoints is not None
            else (),
        ),
    ):
        return False
    await entry.runtime_data.manager.async_stop()
    if entry.runtime_data.datapoints is not None:
        await entry.runtime_data.datapoints.async_stop()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Reject entries outside the supported schema; Core skips this at the current one."""

    _LOGGER.error(
        "Unsupported config entry version %s.%s; remove and re-add the integration",
        entry.version,
        entry.minor_version,
    )
    return False


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove any Repair still owned by a deleted config entry."""

    for item in entry.data.get(CONF_MAPPINGS, []):
        mapping_id = item.get("mapping_id")
        if mapping_id:
            ir.async_delete_issue(hass, DOMAIN, f"mapping_{mapping_id}")
            ir.async_delete_issue(
                hass, DOMAIN, f"homekit_temperature_silent_{mapping_id}"
            )


def _platforms_for_mappings(
    mappings: tuple[MappingConfig, ...], datapoints: tuple[DatapointConfig, ...] = ()
) -> list[str]:
    """Load optional platforms only when a mapping exposes their capability."""

    enabled_optional_platforms = {
        "button": any(mapping.homekit_clear_hold_entity for mapping in mappings),
        "notify": any(mapping.ecobee_notify_entity for mapping in mappings),
    }
    return [
        platform
        for platform in PLATFORMS
        if enabled_optional_platforms.get(platform, True)
    ]


def _remove_orphaned_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    mappings: tuple[MappingConfig, ...],
    datapoints: tuple[DatapointConfig, ...] = (),
) -> None:
    """Remove only entities this entry no longer declares after reconfigure."""

    expected: set[tuple[str, str]] = set()
    expected.update(
        (
            "binary_sensor" if item.kind in BINARY_KINDS else "sensor",
            f"{entry.entry_id}_datapoint_{item.datapoint_id}",
        )
        for item in datapoints
    )
    for mapping in mappings:
        expected.update(
            {
                ("climate", mapping.mapping_id),
                ("number", f"{mapping.mapping_id}_{SUFFIX_MINIMUM_FAN_RUNTIME}"),
                ("sensor", f"{mapping.mapping_id}_{SUFFIX_EQUIPMENT_STAGE}"),
                ("binary_sensor", f"{mapping.mapping_id}_{SUFFIX_SOURCE_DEGRADED}"),
            }
        )
        optional = (
            (
                mapping.homekit_clear_hold_entity,
                "button",
                SUFFIX_RESUME_PROGRAM,
            ),
            (mapping.ecobee_notify_entity, "notify", SUFFIX_NOTIFICATION),
            (mapping.ecobee_aqi_entity, "sensor", SUFFIX_AIR_QUALITY_INDEX),
            (mapping.ecobee_co2_entity, "sensor", SUFFIX_CO2),
            (mapping.ecobee_voc_entity, "sensor", SUFFIX_VOC),
        )
        expected.update(
            (domain, f"{mapping.mapping_id}_{suffix}")
            for reference, domain, suffix in optional
            if reference
        )

    registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registry_entry.platform == DOMAIN
            and (registry_entry.domain, registry_entry.unique_id) not in expected
        ):
            registry.async_remove(registry_entry.entity_id)
