"""Native administrator-only access to configured daily historical families."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN
from .historical import HistoricalManager
from .runtime import EcobeeUnifiedRuntime

SERVICE_GET_DAILY_HISTORY = "get_daily_history"


def async_register_history_service(hass: HomeAssistant) -> None:
    """Register once; preserve the initiating context through the native reader."""

    def get_manager(call: ServiceCall) -> HistoricalManager:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        runtime = getattr(entry, "runtime_data", None)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or not isinstance(runtime, EcobeeUnifiedRuntime)
            or runtime.history is None
        ):
            raise ServiceValidationError(
                "No loaded historical configuration for this entry"
            )
        return runtime.history

    async def async_get_historical_configuration(call: ServiceCall) -> ServiceResponse:
        manager = get_manager(call)
        return {
            "families": [
                {
                    "family_id": family.family_id,
                    "name": family.name,
                    "quantity": family.quantity,
                    "unit": family.unit,
                    "timezone": family.timezone,
                    "policy": family.policy,
                    "source_count": len(family.sources),
                    "equivalent_selection": (
                        "blocked_voc_units"
                        if family.quantity == "voc"
                        else "configured"
                    ),
                }
                for family in manager.families
            ]
        }

    async def async_get_daily_history(call: ServiceCall) -> ServiceResponse:
        manager = get_manager(call)
        family_ids = call.data.get("family_ids")
        if family_ids is None:
            family_ids = [family.family_id for family in manager.families]
        return await manager.async_read(
            family_ids=family_ids,
            start_date=call.data["start_date"],
            end_date=call.data["end_date"],
            include_provisional=call.data["include_provisional"],
            context=call.context,
        )

    async_register_admin_service(
        hass,
        DOMAIN,
        "get_historical_configuration",
        async_get_historical_configuration,
        schema=vol.Schema({vol.Required("config_entry_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_GET_DAILY_HISTORY,
        async_get_daily_history,
        schema=vol.Schema(
            {
                vol.Required("config_entry_id"): cv.string,
                vol.Optional("family_ids"): vol.All(
                    [cv.string], vol.Length(min=1, max=16)
                ),
                vol.Required("start_date"): cv.string,
                vol.Required("end_date"): cv.string,
                vol.Optional("include_provisional", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
