"""Ecobee cloud-only writable projections."""

from __future__ import annotations

from typing import override

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUFFIX_MINIMUM_FAN_RUNTIME
from .entity import EcobeeUnifiedEntity
from .manager import MappingManager
from .models import MappingConfig
from .runtime import EcobeeUnifiedConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcobeeUnifiedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one minimum-fan-runtime control per mapping."""

    manager = entry.runtime_data.manager
    async_add_entities(
        EcobeeMinimumFanRuntimeNumber(manager, mapping) for mapping in manager.mappings
    )


class EcobeeMinimumFanRuntimeNumber(EcobeeUnifiedEntity, NumberEntity):
    """Canonical Ecobee cloud writer for minimum fan runtime."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_min_value = 0
    _attr_native_max_value = 60
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, manager: MappingManager, mapping: MappingConfig) -> None:
        super().__init__(
            manager,
            mapping,
            SUFFIX_MINIMUM_FAN_RUNTIME,
            "minimum_fan_runtime",
        )

    @property
    @override
    def available(self) -> bool:
        return (
            self._snapshot.ecobee_writable
            and self._snapshot.minimum_fan_runtime is not None
        )

    @property
    @override
    def native_value(self) -> float | None:
        return self._snapshot.minimum_fan_runtime

    async def async_set_native_value(self, value: float) -> None:
        await self._manager.async_set_minimum_fan_runtime(
            self._mapping.mapping_id, value, self._context
        )
