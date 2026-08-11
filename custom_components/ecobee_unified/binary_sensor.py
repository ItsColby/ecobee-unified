"""Ecobee Unified mapping-health diagnostics."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUFFIX_SOURCE_DEGRADED
from .entity import EcobeeUnifiedEntity
from .manager import MappingManager
from .models import MappingConfig, SourceHealth
from .runtime import EcobeeUnifiedConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcobeeUnifiedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one mapping-health problem entity per thermostat."""

    manager = entry.runtime_data.manager
    async_add_entities(
        EcobeeSourceDegradedBinarySensor(manager, mapping)
        for mapping in manager.mappings
    )


class EcobeeSourceDegradedBinarySensor(EcobeeUnifiedEntity, BinarySensorEntity):
    """Report whether a mapped source or normalized semantic is degraded."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"reasons", "source_health"})

    def __init__(self, manager: MappingManager, mapping: MappingConfig) -> None:
        super().__init__(
            manager,
            mapping,
            SUFFIX_SOURCE_DEGRADED,
            "source_degraded",
        )

    @property
    @override
    def is_on(self) -> bool:
        snapshot = self._snapshot
        return bool(snapshot.degradation) or any(
            health is not SourceHealth.HEALTHY
            for health in snapshot.source_health.values()
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, object]:
        snapshot = self._snapshot
        return {
            "reasons": list(snapshot.degradation),
            "source_health": {
                key: health.value for key, health in snapshot.source_health.items()
            },
        }
