"""Mapped Ecobee thermostat-display notifications."""

from __future__ import annotations

from typing import override

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_SNAPSHOT_UPDATED, SUFFIX_NOTIFICATION
from .manager import MappingManager
from .models import MappingConfig
from .runtime import EcobeeUnifiedConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcobeeUnifiedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up explicitly mapped thermostat notification entities."""

    manager = entry.runtime_data.manager
    async_add_entities(
        EcobeeUnifiedNotify(manager, mapping)
        for mapping in manager.mappings
        if mapping.ecobee_notify_entity
    )


class EcobeeUnifiedNotify(NotifyEntity):
    """Forward a display message through one mapped Ecobee notify writer."""

    _attr_has_entity_name = True
    _attr_translation_key = "notification"

    def __init__(self, manager: MappingManager, mapping: MappingConfig) -> None:
        self._manager = manager
        self._mapping = mapping
        self._attr_unique_id = f"{mapping.mapping_id}_{SUFFIX_NOTIFICATION}"
        source_entity_id = manager.resolve_entity_id(mapping.homekit_entity)
        self.device_entry = (
            async_entity_id_to_device(manager.hass, source_entity_id)
            if source_entity_id
            else None
        )

    @property
    @override
    def available(self) -> bool:
        return self._manager.snapshot(self._mapping.mapping_id).ecobee_notify_writable

    async def async_added_to_hass(self) -> None:
        """Subscribe to the mapping's normalized capability snapshot."""

        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_SNAPSHOT_UPDATED}_{self._mapping.mapping_id}",
                self.async_write_ha_state,
            )
        )

    @override
    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send one message; the Ecobee backend does not support a title."""

        await self._manager.async_send_notification(
            self._mapping.mapping_id, message, self._context
        )
