"""Fix flows for Repairs raised by Ecobee Unified."""

from __future__ import annotations

from typing import Any, cast

import probatio
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import DOMAIN, HOMEKIT_SOURCE_DOMAIN
from .runtime import EcobeeUnifiedConfigEntry


class ReloadSilentTemperatureSourceFlow(RepairsFlow):
    """Reload the HomeKit entry whose precise temperature stopped updating."""

    def __init__(self, source_entry_id: str, entry_id: str, mapping_id: str) -> None:
        self._source_entry_id = source_entry_id
        self._entry_id = entry_id
        self._mapping_id = mapping_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        source_entry = self.hass.config_entries.async_get_entry(self._source_entry_id)
        if source_entry is None or source_entry.domain != HOMEKIT_SOURCE_DOMAIN:
            return self.async_abort(reason="source_entry_missing")
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                data_schema=probatio.Schema({}),
                description_placeholders={"source": source_entry.title},
            )
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state != ConfigEntryState.LOADED
        ):
            return self.async_abort(reason="source_entry_missing")
        manager = cast(EcobeeUnifiedConfigEntry, entry).runtime_data.manager
        result = await manager.async_repair_temperature_source(
            self._mapping_id, source_entry.entry_id
        )
        if result != "success":
            return self.async_abort(reason=result)
        # The manager removes the Repair once the reloaded sensor reports a new value.
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the only fix flow this integration registers."""

    source_entry_id = data.get("source_entry_id") if data else None
    entry_id = data.get("entry_id") if data else None
    prefix = "homekit_temperature_silent_"
    if (
        not issue_id.startswith("homekit_temperature_silent_")
        or not isinstance(source_entry_id, str)
        or not isinstance(entry_id, str)
        or not issue_id[len(prefix) :]
    ):
        raise ValueError(f"Unsupported Ecobee Unified repair: {issue_id}")
    return ReloadSilentTemperatureSourceFlow(
        source_entry_id, entry_id, issue_id[len(prefix) :]
    )
