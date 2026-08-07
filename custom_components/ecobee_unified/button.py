"""Native Unified controls backed by one explicitly mapped writer."""

from __future__ import annotations

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUFFIX_RESUME_PROGRAM
from .entity import EcobeeUnifiedEntity
from .manager import MappingManager
from .models import MappingConfig
from .runtime import EcobeeUnifiedConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcobeeUnifiedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up mapped local resume controls."""

    manager = entry.runtime_data.manager
    async_add_entities(
        EcobeeUnifiedResumeProgramButton(manager, mapping)
        for mapping in manager.mappings
        if mapping.homekit_clear_hold_entity
    )


class EcobeeUnifiedResumeProgramButton(EcobeeUnifiedEntity, ButtonEntity):
    """Resume the thermostat schedule through one HomeKit Clear Hold writer."""

    _attr_has_entity_name = True
    _attr_translation_key = SUFFIX_RESUME_PROGRAM

    def __init__(self, manager: MappingManager, mapping: MappingConfig) -> None:
        super().__init__(
            manager,
            mapping,
            SUFFIX_RESUME_PROGRAM,
            SUFFIX_RESUME_PROGRAM,
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the explicit local writer is usable."""

        return self._manager.snapshot(
            self._mapping.mapping_id
        ).homekit_clear_hold_writable

    async def async_press(self) -> None:
        """Press the mapped writer exactly once through the manager."""

        await self._manager.async_resume_program(
            self._mapping.mapping_id,
            self._context,
        )
