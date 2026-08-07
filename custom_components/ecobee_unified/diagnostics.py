"""Allow-listed diagnostics for Ecobee Unified."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .runtime import EcobeeUnifiedConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EcobeeUnifiedConfigEntry
) -> dict[str, Any]:
    """Return useful diagnostics without source or household identifiers."""

    manager = entry.runtime_data.manager
    mappings: list[dict[str, Any]] = []
    for index, mapping in enumerate(manager.mappings, start=1):
        snapshot = manager.snapshot(mapping.mapping_id)
        mappings.append(
            {
                "mapping": f"mapping_{index}",
                "available": snapshot.available,
                "homekit_writable": snapshot.homekit_writable,
                "source_health": {
                    key: value.value for key, value in snapshot.source_health.items()
                },
                "source_age_seconds": dict(snapshot.source_ages),
                "field_sources": dict(snapshot.provenance),
                "degradation": list(snapshot.degradation),
                "capabilities": {
                    "hvac_mode": snapshot.hvac_mode is not None,
                    "target_temperature": snapshot.target_temperature is not None,
                    "target_range": snapshot.target_temperature_low is not None
                    and snapshot.target_temperature_high is not None,
                    "fan_mode": snapshot.fan_mode is not None,
                    "vendor_context": snapshot.preset_mode is not None
                    or snapshot.climate_mode is not None,
                    "schedule_context": snapshot.scheduled_profile is not None
                    or snapshot.next_transition is not None,
                },
                "command": {
                    "revision": snapshot.command.revision,
                    "operation": snapshot.command.operation,
                    "status": snapshot.command.status.value,
                    "age_seconds": snapshot.command.age_seconds,
                },
            }
        )
    return {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "mapping_count": len(mappings),
        },
        "mappings": mappings,
    }
