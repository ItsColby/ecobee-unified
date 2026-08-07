"""Shared no-I/O entity projection for Ecobee Unified."""

from __future__ import annotations

from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import SIGNAL_SNAPSHOT_UPDATED
from .manager import MappingManager
from .models import MappingConfig, NormalizedSnapshot


class EcobeeUnifiedEntity(Entity):
    """Base entity linked to the mapped HomeKit-owned thermostat device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        manager: MappingManager,
        mapping: MappingConfig,
        suffix: str,
        translation_key: str,
    ) -> None:
        self._manager = manager
        self._mapping = mapping
        self._attr_unique_id = f"{mapping.mapping_id}_{suffix}"
        self._attr_translation_key = translation_key
        source_entity_id = manager.resolve_entity_id(mapping.homekit_entity)
        self.device_entry = (
            async_entity_id_to_device(manager.hass, source_entity_id)
            if source_entity_id
            else None
        )

    @property
    def _snapshot(self) -> NormalizedSnapshot:
        return self._manager.snapshot(self._mapping.mapping_id)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the mapping's single normalized snapshot."""

        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_SNAPSHOT_UPDATED}_{self._mapping.mapping_id}",
                self.async_write_ha_state,
            )
        )
