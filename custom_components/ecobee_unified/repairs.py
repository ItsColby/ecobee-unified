"""Fix flows for Repairs raised by Ecobee Unified."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant

from .const import HOMEKIT_SOURCE_DOMAIN


class ReloadSilentTemperatureSourceFlow(RepairsFlow):
    """Reload the HomeKit entry whose precise temperature stopped updating."""

    def __init__(self, source_entry_id: str) -> None:
        self._source_entry_id = source_entry_id

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
        if not await self.hass.config_entries.async_reload(source_entry.entry_id):
            return self.async_abort(reason="source_reload_failed")
        # The manager removes the Repair once the reloaded sensor reports a new value.
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the only fix flow this integration registers."""

    source_entry_id = data.get("source_entry_id") if data else None
    if not issue_id.startswith("homekit_temperature_silent_") or not isinstance(
        source_entry_id, str
    ):
        raise ValueError(f"Unsupported Ecobee Unified repair: {issue_id}")
    return ReloadSilentTemperatureSourceFlow(source_entry_id)
