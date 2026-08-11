"""Allow-listed diagnostics for Ecobee Unified."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .models import degradation_advisories, degradation_problem_reasons
from .runtime import EcobeeUnifiedConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EcobeeUnifiedConfigEntry
) -> dict[str, Any]:
    """Return useful diagnostics without source or household identifiers."""

    manager = entry.runtime_data.manager
    mappings: list[dict[str, Any]] = []
    for index, mapping in enumerate(manager.mappings, start=1):
        snapshot = manager.snapshot(mapping.mapping_id)
        command = manager.diagnostic_command_summary(mapping.mapping_id)
        mappings.append(
            {
                "mapping": f"mapping_{index}",
                "available": snapshot.available,
                "homekit_writable": snapshot.homekit_writable,
                "source_health": {
                    key: value.value for key, value in snapshot.source_health.items()
                },
                "source_age_seconds": manager.diagnostic_source_ages(
                    mapping.mapping_id
                ),
                "field_sources": dict(snapshot.provenance),
                "degradation": list(snapshot.degradation),
                "problem_reasons": list(degradation_problem_reasons(snapshot)),
                "advisories": list(degradation_advisories(snapshot)),
                "capabilities": {
                    "hvac_mode": snapshot.hvac_mode is not None,
                    "target_temperature": snapshot.target_temperature is not None,
                    "target_range": snapshot.target_temperature_low is not None
                    and snapshot.target_temperature_high is not None,
                    "target_humidity": snapshot.min_humidity is not None
                    and snapshot.max_humidity is not None,
                    "temperature_step": snapshot.target_temperature_step is not None,
                    "fan_mode": snapshot.fan_mode is not None,
                    "preset_control": snapshot.homekit_preset_writable,
                    "clear_hold": snapshot.homekit_clear_hold_writable,
                    "precise_current_temperature": snapshot.provenance.get(
                        "current_temperature"
                    )
                    == "homekit_temperature",
                    "thermostat_notification": snapshot.ecobee_notify_writable,
                    "vendor_context": snapshot.ecobee_preset_mode is not None
                    or snapshot.climate_mode is not None,
                    "equipment_stage": snapshot.equipment_running is not None,
                    "air_quality_index": snapshot.air_quality_index is not None,
                    "co2": snapshot.co2 is not None,
                    "voc": snapshot.voc is not None,
                },
                "command": {
                    "revision": command.revision,
                    "operation": command.operation,
                    "status": command.status.value,
                    "age_seconds": command.age_seconds,
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
