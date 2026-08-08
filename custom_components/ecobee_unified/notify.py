"""Mapped Ecobee thermostat-display notifications."""

from __future__ import annotations

from typing import override

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUFFIX_NOTIFICATION
from .entity import EcobeeUnifiedEntity
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


class EcobeeUnifiedNotify(EcobeeUnifiedEntity, NotifyEntity):
    """Forward a display message through one mapped Ecobee notify writer."""

    _attr_has_entity_name = True
    _attr_translation_key = "notification"

    def __init__(self, manager: MappingManager, mapping: MappingConfig) -> None:
        super().__init__(
            manager,
            mapping,
            SUFFIX_NOTIFICATION,
            SUFFIX_NOTIFICATION,
        )

    @property
    @override
    def available(self) -> bool:
        return self._snapshot.ecobee_notify_writable

    @override
    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send one message; the Ecobee backend does not support a title."""

        await self._manager.async_send_notification(
            self._mapping.mapping_id, message, self._context
        )
