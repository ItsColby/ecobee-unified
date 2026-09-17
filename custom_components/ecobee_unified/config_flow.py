"""Native configuration and reconfiguration flows for Ecobee Unified."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from math import isfinite
from typing import Any, Literal
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    StatisticSelector,
    StatisticSelectorConfig,
    TextSelector,
)
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_ADD_ANOTHER,
    CONF_CONFIRM_CHANGE,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_ECOBEE_VOC_ENTITY,
    CONF_HISTORICAL_FAMILIES,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_HOMEKIT_TEMPERATURE_ENTITY,
    CONF_MAPPING_ID,
    CONF_MAPPINGS,
    CONF_NAME,
    DEFAULT_CONFIRMATION_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DOMAIN,
    NAME,
    RECONFIGURE_MENU_OPTIONS,
)
from .datapoints import (
    TEMPERATURE_ABSOLUTE_ZERO,
    DatapointConfig,
    SourceBinding,
    validate_datapoint,
    validate_datapoint_edit_sources,
)
from .historical import HistoricalFamily
from .history_source import (
    SOURCE_CONTRACT_FIELDS,
    SOURCE_TIMING_FIELDS,
    async_capture_source,
)
from .models import READ_POLICIES, READ_POLICY_FIELDS, MappingConfig, merge_mapping_data
from .source_contracts import (
    AIR_QUALITY_SENSOR_CONTRACTS,
    PhysicalIdentityStatus,
    homekit_action_contract_valid,
    physical_identity_status,
    sensor_contract_valid,
    temperature_source_unit,
)
from .weather_source import validate_weather_feed

HOMEKIT_CLIMATE_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="climate", integration="homekit_controller")
)
ECOBEE_CLIMATE_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="climate", integration="ecobee")
)
HOMEKIT_SENSOR_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", integration="homekit_controller")
)
ECOBEE_SENSOR_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="sensor", integration="ecobee")
)
HOMEKIT_SELECT_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="select", integration="homekit_controller")
)
HOMEKIT_BUTTON_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="button", integration="homekit_controller")
)
ECOBEE_NOTIFY_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain="notify", integration="ecobee")
)
BOOLEAN_SELECTOR = BooleanSelector()
OPTIONAL_SOURCE_KEYS = (
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_TEMPERATURE_ENTITY,
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_VOC_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
)
DATAPOINT_DOMAINS = [
    "sensor",
    "binary_sensor",
    "climate",
    "select",
    "number",
    "weather",
]
DATAPOINT_SOURCE_SELECTOR = EntitySelector(
    EntitySelectorConfig(domain=DATAPOINT_DOMAINS)
)
DATAPOINT_KINDS = (
    "temperature",
    "humidity",
    "occupancy",
    "motion",
    "battery",
    "profile",
    "configured_membership",
    "weather",
    "duration",
    "number",
    "text",
)
READ_POLICY_OPTIONS = ("homekit_first", "ecobee_first", "homekit_only", "ecobee_only")
SOURCE_SLOTS = ("primary", "secondary", "tertiary")
HISTORICAL_STATISTIC_SELECTOR = StatisticSelector(
    StatisticSelectorConfig(multiple=False)
)
HISTORICAL_ANCHOR_SELECTOR = EntitySelector(
    EntitySelectorConfig(
        filter=[
            {"domain": "sensor", "integration": "ecobee"},
            {"domain": "sensor", "integration": "homekit_controller"},
            {"domain": "sensor", "integration": DOMAIN},
            {"domain": "sensor", "integration": "battery_notes"},
            {"domain": "weather", "integration": "ecobee"},
        ]
    )
)


class EcobeeUnifiedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single multi-thermostat Ecobee Unified entry."""

    VERSION = 1
    MINOR_VERSION = 5

    def __init__(self) -> None:
        self._original_data: dict[str, Any] | None = None
        self._pending_mappings: list[dict[str, str]] = []
        self._selected_mapping_id: str | None = None
        self._original_options: dict[str, Any] | None = None
        self._pending_datapoints: list[dict[str, Any]] = []
        self._selected_datapoint_id: str | None = None
        self._pending_historical: list[dict[str, Any]] = []
        self._selected_family_id: str | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return changeable timing options."""

        return EcobeeUnifiedOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Begin one entry containing one or more explicit mappings."""

        await self.async_set_unique_id(DOMAIN)
        self._pending_mappings = []
        return await self.async_step_mapping(user_input)

    async def async_step_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect one explicit thermostat mapping."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mapping = _mapping_from_input(self.hass, user_input)
                _validate_no_duplicate_sources(self._pending_mappings, mapping)
            except vol.Invalid as err:
                errors["base"] = str(err)
            else:
                self._pending_mappings.append(mapping)
                if user_input.get(CONF_ADD_ANOTHER, False):
                    return self.async_show_form(
                        step_id="mapping",
                        data_schema=_mapping_schema({}, include_add=True),
                    )
                return self.async_create_entry(
                    title=NAME,
                    data={CONF_MAPPINGS: self._pending_mappings},
                )
        return self.async_show_form(
            step_id="mapping",
            data_schema=_mapping_schema(user_input or {}, include_add=True),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open explicit add, edit, remove, or finish mapping operations."""

        if self._original_data is None:
            entry = self._get_reconfigure_entry()
            self._original_data = deepcopy(dict(entry.data))
            self._original_options = deepcopy(dict(entry.options))
            self._pending_mappings = deepcopy(
                self._original_data.get(CONF_MAPPINGS, [])
            )
            self._pending_datapoints = deepcopy(
                self._original_data.get("datapoints", [])
            )
            self._pending_historical = deepcopy(
                self._original_data.get(CONF_HISTORICAL_FAMILIES, [])
            )
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=[
                option
                for option in RECONFIGURE_MENU_OPTIONS
                if (option != "reconfigure_remove" or len(self._pending_mappings) > 1)
                and (
                    option not in {"datapoint_edit", "datapoint_remove"}
                    or self._pending_datapoints
                )
                and (
                    option not in {"historical_edit", "historical_remove"}
                    or self._pending_historical
                )
            ],
        )

    async def async_step_datapoint_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect one explicitly equivalent set of read-only sources."""
        return await self._datapoint_form("datapoint_add", user_input)

    async def async_step_datapoint_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select an existing stable datapoint identity."""
        if user_input is not None:
            self._selected_datapoint_id = str(user_input["datapoint_id"])
            return await self.async_step_datapoint_edit_confirm()
        return self.async_show_form(
            step_id="datapoint_edit",
            data_schema=_datapoint_selection_schema(self._pending_datapoints),
        )

    async def async_step_datapoint_edit_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one row without replacing its identity or future fields."""
        current = self._selected_datapoint()
        if current is None:
            return self.async_abort(reason="invalid_datapoint_identity")
        if len(current.get("sources", [])) > len(SOURCE_SLOTS):
            return self.async_abort(reason="datapoint_source_limit")
        return await self._datapoint_form("datapoint_edit_confirm", user_input, current)

    async def _datapoint_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        current: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                updated = _datapoint_from_input(self.hass, user_input, current)
                _validate_datapoint_collection(
                    [row for row in self._pending_datapoints if row is not current],
                    updated,
                )
            except (ValueError, vol.Invalid) as err:
                errors["base"] = str(err)
            else:
                if current is None:
                    self._pending_datapoints.append(updated)
                else:
                    self._pending_datapoints = [
                        updated if row is current else row
                        for row in self._pending_datapoints
                    ]
                self._selected_datapoint_id = None
                return await self.async_step_reconfigure()
        try:
            defaults = (
                user_input
                if user_input is not None
                else (_datapoint_form_defaults(self.hass, current) if current else {})
            )
        except KeyError, TypeError, ValueError:
            return self.async_abort(reason="datapoint_not_supported")
        return self.async_show_form(
            step_id=step_id,
            data_schema=_datapoint_schema(defaults),
            errors=errors,
        )

    async def async_step_datapoint_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the one datapoint to remove."""
        if not self._pending_datapoints:
            return await self.async_step_reconfigure()
        if user_input is not None:
            self._selected_datapoint_id = str(user_input["datapoint_id"])
            return await self.async_step_datapoint_remove_confirm()
        return self.async_show_form(
            step_id="datapoint_remove",
            data_schema=_datapoint_selection_schema(self._pending_datapoints),
        )

    async def async_step_datapoint_remove_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require confirmation and never remove multiple duplicate identities."""
        current = self._selected_datapoint()
        if current is None:
            return self.async_abort(reason="invalid_datapoint_identity")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_CONFIRM_CHANGE, False):
                self._pending_datapoints = [
                    row for row in self._pending_datapoints if row is not current
                ]
                self._selected_datapoint_id = None
                return await self.async_step_reconfigure()
            errors["base"] = "confirmation_required"
        return self.async_show_form(
            step_id="datapoint_remove_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM_CHANGE, default=False): BOOLEAN_SELECTOR}
            ),
            errors=errors,
            description_placeholders={"name": str(current["name"])},
        )

    def _selected_datapoint(self) -> dict[str, Any] | None:
        matches = [
            row
            for row in self._pending_datapoints
            if row.get("datapoint_id") == self._selected_datapoint_id
        ]
        return matches[0] if len(matches) == 1 else None

    async def async_step_historical_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Stage one daily history family with explicit source associations."""
        return await self._historical_form("historical_add", user_input)

    async def async_step_historical_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._selected_family_id = str(user_input["family_id"])
            return await self.async_step_historical_edit_confirm()
        return self.async_show_form(
            step_id="historical_edit",
            data_schema=_historical_selection_schema(self._pending_historical),
        )

    async def async_step_historical_edit_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current = self._selected_historical()
        if current is None:
            return self.async_abort(reason="historical_identity_invalid")
        if len(current.get("sources", [])) > len(SOURCE_SLOTS):
            return self.async_abort(reason="historical_source_limit")
        return await self._historical_form(
            "historical_edit_confirm", user_input, current
        )

    async def _historical_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        current: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                updated = await _historical_from_input(self.hass, user_input, current)
                _validate_historical_collection(
                    [row for row in self._pending_historical if row is not current],
                    updated,
                )
            except (ValueError, vol.Invalid) as err:
                errors["base"] = str(err)
            else:
                if current is None:
                    self._pending_historical.append(updated)
                else:
                    self._pending_historical = [
                        updated if row is current else row
                        for row in self._pending_historical
                    ]
                self._selected_family_id = None
                return await self.async_step_reconfigure()
        try:
            defaults = (
                user_input
                if user_input is not None
                else (_historical_form_defaults(self.hass, current) if current else {})
            )
        except KeyError, TypeError, ValueError:
            return self.async_abort(reason="historical_not_supported")
        return self.async_show_form(
            step_id=step_id,
            data_schema=_historical_schema(self.hass, defaults),
            errors=errors,
        )

    async def async_step_historical_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._pending_historical:
            return await self.async_step_reconfigure()
        if user_input is not None:
            self._selected_family_id = str(user_input["family_id"])
            return await self.async_step_historical_remove_confirm()
        return self.async_show_form(
            step_id="historical_remove",
            data_schema=_historical_selection_schema(self._pending_historical),
        )

    async def async_step_historical_remove_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current = self._selected_historical()
        if current is None:
            return self.async_abort(reason="historical_identity_invalid")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_CONFIRM_CHANGE, False):
                self._pending_historical = [
                    row for row in self._pending_historical if row is not current
                ]
                self._selected_family_id = None
                return await self.async_step_reconfigure()
            errors["base"] = "confirmation_required"
        return self.async_show_form(
            step_id="historical_remove_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM_CHANGE, default=False): BOOLEAN_SELECTOR}
            ),
            errors=errors,
            description_placeholders={"name": str(current["name"])},
        )

    def _selected_historical(self) -> dict[str, Any] | None:
        matches = [
            row
            for row in self._pending_historical
            if row.get("family_id") == self._selected_family_id
        ]
        return matches[0] if len(matches) == 1 else None

    async def async_step_reconfigure_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one mapping without replacing existing stable identities."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mapping = _mapping_from_input(self.hass, user_input)
                _validate_no_duplicate_sources(self._pending_mappings, mapping)
            except vol.Invalid as err:
                errors["base"] = str(err)
            else:
                self._pending_mappings.append(mapping)
                return await self.async_step_reconfigure()
        return self.async_show_form(
            step_id="reconfigure_add",
            data_schema=_mapping_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a mapping whose physical association may be changed."""

        if user_input is not None:
            self._selected_mapping_id = str(user_input[CONF_MAPPING_ID])
            return await self.async_step_reconfigure_edit_confirm()
        return self.async_show_form(
            step_id="reconfigure_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAPPING_ID): _mapping_selector(
                        self._pending_mappings
                    )
                }
            ),
        )

    async def async_step_reconfigure_edit_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require explicit confirmation before changing source/writer identity."""

        current = self._selected_mapping()
        defaults = _mapping_form_defaults(self.hass, current)
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                updated = merge_mapping_data(
                    current,
                    _mapping_from_input(
                        self.hass,
                        user_input,
                        mapping_id=str(current[CONF_MAPPING_ID]),
                        preserved=MappingConfig.from_dict(current),
                    ),
                )
                others = [
                    mapping
                    for mapping in self._pending_mappings
                    if mapping[CONF_MAPPING_ID] != current[CONF_MAPPING_ID]
                ]
                _validate_no_duplicate_sources(others, updated)
            except vol.Invalid as err:
                errors["base"] = str(err)
            else:
                changes_writer = any(
                    current.get(key) != updated.get(key)
                    for key in (
                        CONF_HOMEKIT_ENTITY,
                        CONF_HOMEKIT_PRESET_ENTITY,
                        CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
                        CONF_ECOBEE_ENTITY,
                        CONF_ECOBEE_NOTIFY_ENTITY,
                    )
                )
                if changes_writer and not user_input.get(CONF_CONFIRM_CHANGE, False):
                    errors["base"] = "confirmation_required"
                else:
                    self._pending_mappings = [
                        updated
                        if mapping[CONF_MAPPING_ID] == current[CONF_MAPPING_ID]
                        else mapping
                        for mapping in self._pending_mappings
                    ]
                    self._selected_mapping_id = None
                    return await self.async_step_reconfigure()
        return self.async_show_form(
            step_id="reconfigure_edit_confirm",
            data_schema=_mapping_schema(
                defaults if user_input is None else user_input,
                include_confirmation=True,
            ),
            errors=errors,
        )

    async def async_step_reconfigure_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a mapping to remove."""

        if len(self._pending_mappings) <= 1:
            return await self.async_step_reconfigure()
        if user_input is not None:
            self._selected_mapping_id = str(user_input[CONF_MAPPING_ID])
            return await self.async_step_reconfigure_remove_confirm()
        return self.async_show_form(
            step_id="reconfigure_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAPPING_ID): _mapping_selector(
                        self._pending_mappings
                    )
                }
            ),
        )

    async def async_step_reconfigure_remove_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require confirmation before removing a canonical thermostat."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_CONFIRM_CHANGE, False):
                errors["base"] = "confirmation_required"
            else:
                self._pending_mappings = [
                    mapping
                    for mapping in self._pending_mappings
                    if mapping[CONF_MAPPING_ID] != self._selected_mapping_id
                ]
                self._selected_mapping_id = None
                return await self.async_step_reconfigure()
        return self.async_show_form(
            step_id="reconfigure_remove_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONFIRM_CHANGE, default=False): BOOLEAN_SELECTOR}
            ),
            errors=errors,
            description_placeholders={"name": self._selected_mapping()[CONF_NAME]},
        )

    async def async_step_reconfigure_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Atomically save and reload the complete mapping collection."""

        entry = self._get_reconfigure_entry()
        original_data = self._original_data
        if (
            original_data is None
            or dict(entry.data) != original_data
            or dict(entry.options) != self._original_options
        ):
            return self.async_abort(reason="configuration_changed")

        accepted_data = deepcopy(original_data)
        accepted_data[CONF_MAPPINGS] = deepcopy(self._pending_mappings)
        if self._pending_datapoints or "datapoints" in original_data:
            accepted_data["datapoints"] = deepcopy(self._pending_datapoints)
        if self._pending_historical or CONF_HISTORICAL_FAMILIES in original_data:
            accepted_data[CONF_HISTORICAL_FAMILIES] = deepcopy(self._pending_historical)

        return self.async_update_reload_and_abort(
            entry,
            data=accepted_data,
            reason="reconfigure_successful",
            reload_even_if_entry_is_unchanged=False,
        )

    def _selected_mapping(self) -> dict[str, str]:
        if self._selected_mapping_id is None:
            raise RuntimeError("No mapping selected")
        return next(
            mapping
            for mapping in self._pending_mappings
            if mapping[CONF_MAPPING_ID] == self._selected_mapping_id
        )


class EcobeeUnifiedOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage cadence-backed freshness and confirmation thresholds."""

    def __init__(self) -> None:
        self._original_options: dict[str, Any] | None = None
        self._original_data: dict[str, Any] | None = None
        self._pending_options: dict[str, Any] = {}
        self._selected_mapping_id: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._original_options is None:
            self._original_options = deepcopy(dict(self.config_entry.options))
            self._original_data = deepcopy(dict(self.config_entry.data))
            self._pending_options = deepcopy(self._original_options)
        if user_input is not None:
            if self._configuration_changed():
                return self.async_abort(reason="configuration_changed")
            try:
                validated_input = _validate_timing_options(user_input)
            except vol.Invalid:
                errors["base"] = "invalid_timing"
            else:
                self._pending_options.update(validated_input)
                if user_input.get("configure_read_policy", False):
                    return await self.async_step_read_policy_mapping()
                return self._save_options()

        original_options = self._original_options
        assert original_options is not None
        defaults = {
            CONF_ECOBEE_STALE_SECONDS: original_options.get(
                CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS
            ),
            CONF_CONFIRMATION_SECONDS: original_options.get(
                CONF_CONFIRMATION_SECONDS, DEFAULT_CONFIRMATION_SECONDS
            ),
        }
        if user_input is not None:
            defaults.update(user_input)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(defaults), errors=errors
        )

    def _configuration_changed(self) -> bool:
        return (
            dict(self.config_entry.options) != self._original_options
            or dict(self.config_entry.data) != self._original_data
        )

    def _save_options(self) -> ConfigFlowResult:
        if self._configuration_changed():
            return self.async_abort(reason="configuration_changed")
        return self.async_create_entry(title="", data=deepcopy(self._pending_options))

    async def async_step_read_policy_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the thermostat whose read precedence should change."""
        if self._configuration_changed():
            return self.async_abort(reason="configuration_changed")
        mappings = (self._original_data or {}).get(CONF_MAPPINGS, [])
        if user_input is not None:
            selected = str(user_input[CONF_MAPPING_ID])
            if sum(row.get(CONF_MAPPING_ID) == selected for row in mappings) != 1:
                return self.async_abort(reason="invalid_mapping_identity")
            self._selected_mapping_id = selected
            return await self.async_step_read_policy()
        return self.async_show_form(
            step_id="read_policy_mapping",
            data_schema=vol.Schema(
                {vol.Required(CONF_MAPPING_ID): _mapping_selector(mappings)}
            ),
        )

    async def async_step_read_policy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set field-specific reads independently from command writers."""
        if self._configuration_changed():
            return self.async_abort(reason="configuration_changed")
        selected = self._selected_mapping_id
        assert selected is not None
        policies = self._pending_options.get("read_policies", {})
        current = policies.get(selected, {})
        errors: dict[str, str] = {}
        if user_input is not None:
            if any(
                user_input.get(field) not in READ_POLICIES
                for field in READ_POLICY_FIELDS
            ):
                errors["base"] = "invalid_read_policy"
            else:
                self._pending_options["read_policies"] = deepcopy(policies) | {
                    selected: deepcopy(current)
                    | {field: user_input[field] for field in READ_POLICY_FIELDS}
                }
                if user_input.get("configure_another", False):
                    return await self.async_step_read_policy_mapping()
                return self._save_options()
        defaults = current if user_input is None else user_input
        schema: dict[vol.Marker, Any] = {
            vol.Required(
                field, default=defaults.get(field, "homekit_first")
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(READ_POLICY_OPTIONS), translation_key="read_policy"
                )
            )
            for field in READ_POLICY_FIELDS
        }
        schema[vol.Optional("configure_another", default=False)] = BOOLEAN_SELECTOR
        return self.async_show_form(
            step_id="read_policy",
            data_schema=vol.Schema(schema),
            errors=errors,
        )


def _mapping_schema(
    defaults: dict[str, Any],
    *,
    include_add: bool = False,
    include_confirmation: bool = False,
) -> vol.Schema:
    schema: dict[vol.Marker, Any] = {
        vol.Required(
            CONF_NAME,
            description={"suggested_value": defaults.get(CONF_NAME, "")},
        ): TextSelector(),
        vol.Required(
            CONF_HOMEKIT_ENTITY,
            description={"suggested_value": defaults.get(CONF_HOMEKIT_ENTITY, "")},
        ): HOMEKIT_CLIMATE_SELECTOR,
        vol.Required(
            CONF_ECOBEE_ENTITY,
            description={"suggested_value": defaults.get(CONF_ECOBEE_ENTITY, "")},
        ): ECOBEE_CLIMATE_SELECTOR,
        vol.Optional(
            CONF_HOMEKIT_PRESET_ENTITY,
            description={"suggested_value": defaults.get(CONF_HOMEKIT_PRESET_ENTITY)},
        ): HOMEKIT_SELECT_SELECTOR,
        vol.Optional(
            CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
            description={
                "suggested_value": defaults.get(CONF_HOMEKIT_CLEAR_HOLD_ENTITY)
            },
        ): HOMEKIT_BUTTON_SELECTOR,
        vol.Optional(
            CONF_HOMEKIT_TEMPERATURE_ENTITY,
            description={
                "suggested_value": defaults.get(CONF_HOMEKIT_TEMPERATURE_ENTITY)
            },
        ): HOMEKIT_SENSOR_SELECTOR,
        vol.Optional(
            CONF_ECOBEE_NOTIFY_ENTITY,
            description={"suggested_value": defaults.get(CONF_ECOBEE_NOTIFY_ENTITY)},
        ): ECOBEE_NOTIFY_SELECTOR,
    }
    for key in (
        CONF_ECOBEE_AQI_ENTITY,
        CONF_ECOBEE_CO2_ENTITY,
        CONF_ECOBEE_VOC_ENTITY,
    ):
        schema[
            vol.Optional(
                key,
                description={"suggested_value": defaults.get(key)},
            )
        ] = ECOBEE_SENSOR_SELECTOR
    if include_add:
        schema[
            vol.Required(
                CONF_ADD_ANOTHER, default=defaults.get(CONF_ADD_ANOTHER, False)
            )
        ] = BOOLEAN_SELECTOR
    if include_confirmation:
        schema[vol.Required(CONF_CONFIRM_CHANGE, default=False)] = BOOLEAN_SELECTOR
    return vol.Schema(schema)


def _datapoint_selection_schema(rows: list[dict[str, Any]]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("datapoint_id"): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=str(row["datapoint_id"]), label=str(row["name"])
                        )
                        for row in rows
                    ]
                )
            )
        }
    )


def _historical_schema(hass: Any, defaults: dict[str, Any]) -> vol.Schema:
    """Use native statistic selection and an explicit physical sensor anchor."""
    schema: dict[vol.Marker, Any] = {}
    for field, selector, default in (
        ("name", TextSelector(), ""),
        (
            "quantity",
            SelectSelector(
                SelectSelectorConfig(
                    options=[
                        "temperature",
                        "humidity",
                        "co2",
                        "aqi",
                        "voc",
                        "battery",
                        "weather_temperature",
                        "weather_humidity",
                    ],
                    translation_key="historical_quantity",
                )
            ),
            "temperature",
        ),
        (
            "unit",
            SelectSelector(
                SelectSelectorConfig(
                    options=["°C", "°F", "%", "ppm", "0-100", "native"],
                )
            ),
            "°C",
        ),
        ("timezone", TextSelector(), hass.config.time_zone),
        ("anchor_ref", HISTORICAL_ANCHOR_SELECTOR, ""),
        (
            "policy",
            SelectSelector(
                SelectSelectorConfig(
                    options=["fixed_source", "ordered_daily"],
                    translation_key="historical_policy",
                )
            ),
            "fixed_source",
        ),
        ("accept_cross_method", BOOLEAN_SELECTOR, False),
    ):
        schema[vol.Required(field, default=defaults.get(field, default))] = selector
    for slot in SOURCE_SLOTS:
        key = f"{slot}_statistic"
        marker = vol.Required if slot == "primary" else vol.Optional
        schema[
            marker(key, **({"default": defaults[key]} if key in defaults else {}))
        ] = HISTORICAL_STATISTIC_SELECTOR
    schema[vol.Required("confirm_association", default=False)] = BOOLEAN_SELECTOR
    return vol.Schema(schema)


def _historical_selection_schema(rows: list[dict[str, Any]]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("family_id"): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=str(row["family_id"]), label=str(row["name"])
                        )
                        for row in rows
                    ],
                )
            )
        }
    )


def _historical_form_defaults(hass: Any, row: dict[str, Any]) -> dict[str, Any]:
    family = HistoricalFamily.from_dict(row)
    defaults = {
        key: getattr(family, key)
        for key in (
            "name",
            "quantity",
            "unit",
            "timezone",
            "policy",
            "accept_cross_method",
        )
    }
    defaults["anchor_ref"] = _resolved_or_reference(
        er.async_get(hass), family.anchor_ref
    )
    for slot, source in zip(SOURCE_SLOTS, family.sources, strict=False):
        defaults[f"{slot}_statistic"] = source["statistic_id"]
    return defaults


async def _historical_from_input(
    hass: Any, user_input: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Capture current native contracts without claiming historical continuity."""
    previous = HistoricalFamily.from_dict(current) if current is not None else None
    registry = er.async_get(hass)
    reference = str(user_input.get("anchor_ref", ""))
    entity_id = er.async_resolve_entity_id(registry, reference)
    anchor = registry.async_get(entity_id) if entity_id else None
    if anchor is None:
        raise ValueError("historical_source_identity_unproven")
    quantity = str(user_input.get("quantity", ""))
    timezone = str(user_input.get("timezone", hass.config.time_zone))
    if timezone != hass.config.time_zone:
        raise ValueError("historical_timezone_mismatch")
    statistic_ids = [
        str(user_input[f"{slot}_statistic"])
        for slot in SOURCE_SLOTS
        if user_input.get(f"{slot}_statistic")
    ]
    if not user_input.get("primary_statistic") or len(set(statistic_ids)) != len(
        statistic_ids
    ):
        raise ValueError("historical_sources_invalid")
    old_sources = (
        {source["statistic_id"]: source for source in previous.sources}
        if previous
        else {}
    )
    sources = []
    for statistic_id in statistic_ids:
        captured = await async_capture_source(hass, statistic_id, quantity, anchor.id)
        if captured.get("timezone") != timezone:
            raise ValueError("historical_timezone_mismatch")
        retained = deepcopy(old_sources.get(statistic_id, {}))
        # Replace owned optional contract fields as well as populated ones.
        for key in SOURCE_CONTRACT_FIELDS | SOURCE_TIMING_FIELDS:
            retained.pop(key, None)
        source_id = retained.get("source_id", uuid4().hex)
        sources.append(retained | captured | {"source_id": source_id})
    family = HistoricalFamily.from_dict(
        {
            "family_id": previous.family_id if previous else uuid4().hex,
            "name": str(user_input.get("name", "")).strip(),
            "quantity": quantity,
            "unit": str(user_input.get("unit", "")),
            "timezone": timezone,
            "anchor_ref": anchor.id,
            "sources": sources,
            "policy": user_input.get("policy", "fixed_source"),
            "accept_cross_method": user_input.get("accept_cross_method", False),
        }
    )
    if not user_input.get("confirm_association", False) and (
        previous is None or _historical_contract_changed(previous, family)
    ):
        raise ValueError("historical_confirmation_required")
    return deepcopy(current or {}) | family.as_dict()


def _historical_contract_changed(
    previous: HistoricalFamily, current: HistoricalFamily
) -> bool:
    def contract(family: HistoricalFamily) -> dict[str, Any]:
        result = family.as_dict()
        result.pop("name", None)
        for source in result["sources"]:
            for key in SOURCE_TIMING_FIELDS:
                source.pop(key, None)
        return result

    return contract(previous) != contract(current)


def _validate_historical_collection(
    rows: list[dict[str, Any]], candidate: dict[str, Any]
) -> None:
    if any(row.get("family_id") == candidate["family_id"] for row in rows):
        raise ValueError("historical_identity_invalid")
    if any(
        str(row.get("name", "")).strip().casefold() == candidate["name"].casefold()
        for row in rows
    ):
        raise ValueError("historical_duplicate_name")


class _TemperatureBoundSelector(NumberSelector):
    """Keep HA's native number box without coercing booleans into bounds."""

    def __call__(self, data: Any) -> float:
        if isinstance(data, bool):
            raise vol.Invalid("invalid_accepted_range")
        return super().__call__(data)


def _datapoint_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Describe quantities and each ordered source without guessing names."""
    schema: dict[vol.Marker, Any] = {}
    for field, selector, default in (
        ("name", TextSelector(), ""),
        (
            "kind",
            SelectSelector(
                SelectSelectorConfig(
                    options=list(DATAPOINT_KINDS), translation_key="datapoint_kind"
                )
            ),
            "temperature",
        ),
        (
            "semantic",
            SelectSelector(
                SelectSelectorConfig(
                    options=[
                        "physical_temperature",
                        "control_temperature",
                        "weather_temperature",
                        "elapsed_duration",
                        "minimum_fan_runtime_per_hour",
                    ],
                    translation_key="datapoint_semantic",
                )
            ),
            "physical_temperature",
        ),
        (
            "time_basis",
            SelectSelector(
                SelectSelectorConfig(
                    options=["current", "interval"], translation_key="time_basis"
                )
            ),
            "current",
        ),
        (
            "max_age_seconds",
            NumberSelector(
                NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
            ),
            0,
        ),
        ("fallback", BOOLEAN_SELECTOR, True),
    ):
        schema[vol.Required(field, default=defaults.get(field, default))] = selector
    for field, selector in (
        ("unit", TextSelector()),
        (
            "minimum_value",
            _TemperatureBoundSelector(
                NumberSelectorConfig(step="any", mode=NumberSelectorMode.BOX)
            ),
        ),
        (
            "maximum_value",
            _TemperatureBoundSelector(
                NumberSelectorConfig(step="any", mode=NumberSelectorMode.BOX)
            ),
        ),
        (
            "interval_seconds",
            NumberSelector(
                NumberSelectorConfig(min=1, step=1, mode=NumberSelectorMode.BOX)
            ),
        ),
    ):
        schema[
            vol.Optional(field, description={"suggested_value": defaults.get(field)})
        ] = selector
    for slot in SOURCE_SLOTS:
        entity_field = f"{slot}_entity"
        marker = vol.Optional if slot == "tertiary" else vol.Required
        schema[
            marker(
                entity_field,
                description={"suggested_value": defaults.get(entity_field)},
            )
        ] = DATAPOINT_SOURCE_SELECTOR
        for suffix in ("attribute", "timestamp_attribute", "unit"):
            field = f"{slot}_{suffix}"
            schema[
                vol.Optional(
                    field, description={"suggested_value": defaults.get(field)}
                )
            ] = TextSelector()
        age_field = f"{slot}_max_age_seconds"
        schema[
            vol.Optional(
                age_field, description={"suggested_value": defaults.get(age_field)}
            )
        ] = NumberSelector(
            NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
        )
    schema[vol.Required("confirm_equivalence", default=False)] = BOOLEAN_SELECTOR
    return vol.Schema(schema)


def _datapoint_from_input(
    hass: Any, user_input: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    registry = er.async_get(hass)
    name = str(user_input.get("name", "")).strip()
    if not name or len(name) > 64:
        raise vol.Invalid("invalid_name")
    sources = []
    for slot in SOURCE_SLOTS:
        selected = user_input.get(f"{slot}_entity")
        if not selected:
            if slot != "tertiary":
                raise vol.Invalid("datapoint_source_missing")
            continue
        entity_id = er.async_resolve_entity_id(registry, str(selected))
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is None or entry.domain not in DATAPOINT_DOMAINS:
            raise vol.Invalid("datapoint_source_missing")
        sources.append(
            SourceBinding(
                entry.id,
                max_age_seconds=_datapoint_seconds(
                    user_input.get(f"{slot}_max_age_seconds"), optional=True
                ),
                **{
                    suffix: str(user_input.get(f"{slot}_{suffix}", "")).strip() or None
                    for suffix in ("attribute", "timestamp_attribute", "unit")
                },
            )
        )
    kind = str(user_input.get("kind", ""))
    config = DatapointConfig.from_dict(
        {
            "datapoint_id": str(current["datapoint_id"]) if current else uuid4().hex,
            "name": name,
            "kind": kind,
            "sources": [source.as_dict() for source in sources],
            "unit": str(user_input.get("unit", "")).strip() or None,
            "semantic": str(user_input.get("semantic", ""))
            if kind in {"temperature", "duration"}
            else (current or {}).get("semantic"),
            "time_basis": str(user_input.get("time_basis", "current")),
            "interval_seconds": _datapoint_seconds(
                user_input.get("interval_seconds"), optional=True, minimum=1
            ),
            "max_age_seconds": _datapoint_seconds(user_input.get("max_age_seconds", 0)),
            "fallback": user_input.get("fallback", True),
            **_weather_datapoint_identity(hass, sources),
        }
    )
    if current is not None:
        _validate_datapoint_edit_meaning(
            hass, DatapointConfig.from_dict(current), config
        )
    config = _datapoint_with_range(config, user_input, current)
    validate_datapoint(hass, config)
    if not user_input.get("confirm_equivalence", False) and (
        current is None or _datapoint_contract_changed(current, config)
    ):
        raise vol.Invalid("datapoint_equivalence_required")
    result = deepcopy(current or {})
    canonical = config.as_dict()
    # Clear nullable owned fields before overlay; retain unknown future fields.
    for field in (
        "unit",
        "interval_seconds",
        "semantic",
        "minimum_value",
        "maximum_value",
    ):
        result.pop(field, None)
    result.update(canonical)
    old_sources = (current or {}).get("sources", [])
    result["sources"] = [
        {
            **{
                key: value
                for key, value in (
                    old_sources[index].items() if index < len(old_sources) else []
                )
                if key
                not in {
                    "entity",
                    "attribute",
                    "timestamp_attribute",
                    "unit",
                    "max_age_seconds",
                }
            },
            **source,
        }
        for index, source in enumerate(canonical["sources"])
    ]
    return result


def _datapoint_with_range(
    config: DatapointConfig,
    user_input: dict[str, Any],
    current: dict[str, Any] | None,
) -> DatapointConfig:
    """Apply bounds in output units without reinterpreting an existing range."""
    bounds = {
        field: _datapoint_bound(user_input.get(field))
        for field in ("minimum_value", "maximum_value")
    }
    if current is not None:
        previous = DatapointConfig.from_dict(current)
        if previous.unit != config.unit and any(
            getattr(previous, field) is not None for field in bounds
        ):
            if any(bounds[field] != getattr(previous, field) for field in bounds):
                raise vol.Invalid("datapoint_range_unit_change")
            assert previous.unit is not None and config.unit is not None
            bounds = {
                field: None
                if value is None
                else TEMPERATURE_ABSOLUTE_ZERO[config.unit]
                if value == TEMPERATURE_ABSOLUTE_ZERO[previous.unit]
                else TemperatureConverter.convert(value, previous.unit, config.unit)
                for field, value in bounds.items()
            }
    return replace(
        config,
        minimum_value=bounds["minimum_value"],
        maximum_value=bounds["maximum_value"],
    )


def _datapoint_bound(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise vol.Invalid("invalid_accepted_range")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("invalid_accepted_range") from err
    if not isfinite(number):
        raise vol.Invalid("invalid_accepted_range")
    return number


def _validate_datapoint_edit_meaning(
    hass: HomeAssistant, previous: DatapointConfig, current: DatapointConfig
) -> None:
    """Keep one subject, quantity, role and time meaning behind a Recorder identity."""
    if (
        previous.kind != current.kind
        or (previous.semantic or previous.kind) != (current.semantic or current.kind)
        or previous.time_basis != current.time_basis
        or previous.interval_seconds != current.interval_seconds
        or (
            previous.unit != current.unit
            and current.kind not in {"temperature", "duration"}
        )
    ):
        raise vol.Invalid("datapoint_meaning_change")
    # Generic numbers/text have no narrower native quantity contract. A different
    # binding may carry a different meaning even when its unit or value matches.
    if current.kind in {"number", "text"} and {
        (source.entity, source.attribute) for source in previous.sources
    } != {(source.entity, source.attribute) for source in current.sources}:
        raise vol.Invalid("datapoint_meaning_change")
    validate_datapoint_edit_sources(hass, previous, current)


def _weather_datapoint_identity(
    hass: Any, sources: list[SourceBinding]
) -> dict[str, str]:
    """Capture the native station feed identity for explicitly selected weather aliases."""
    registry = er.async_get(hass)
    entries = [
        registry.async_get(entity_id)
        if (entity_id := er.async_resolve_entity_id(registry, binding.entity))
        else None
        for binding in sources
    ]
    if not any(entry is not None and entry.domain == "weather" for entry in entries):
        return {}
    if any(entry is None or entry.domain != "weather" for entry in entries):
        raise ValueError("weather_sources_required")
    observations = []
    for entry in entries:
        assert entry is not None
        state = hass.states.get(entry.entity_id)
        if state is None:
            raise ValueError("source_missing")
        observations.append((entry, state))
    identity = validate_weather_feed(observations)
    return {
        "weather_station": identity.station,
        "weather_config_entry_id": identity.config_entry_id,
    }


def _datapoint_seconds(
    value: Any, *, optional: bool = False, minimum: int = 0
) -> int | None:
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise vol.Invalid("datapoint_invalid_timing")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("datapoint_invalid_timing") from err
    if not isfinite(number) or number != int(number) or number < minimum:
        raise vol.Invalid("datapoint_invalid_timing")
    return int(number)


def _datapoint_contract_changed(
    current: dict[str, Any], config: DatapointConfig
) -> bool:
    previous = DatapointConfig.from_dict(current)
    source_changes = [
        tuple(
            (source.entity, source.attribute, source.timestamp_attribute, source.unit)
            for source in item.sources
        )
        for item in (previous, config)
    ]
    return source_changes[0] != source_changes[1] or any(
        getattr(previous, field) != getattr(config, field)
        for field in (
            "kind",
            "semantic",
            "unit",
            "time_basis",
            "interval_seconds",
            "weather_station",
            "weather_config_entry_id",
        )
    )


def _validate_datapoint_collection(
    rows: list[dict[str, Any]], candidate: dict[str, Any]
) -> None:
    if any(row.get("datapoint_id") == candidate["datapoint_id"] for row in rows):
        raise vol.Invalid("invalid_datapoint_identity")
    for row in rows:
        if str(row.get("name", "")).strip().casefold() == candidate["name"].casefold():
            raise vol.Invalid("duplicate_datapoint_name")


def _datapoint_form_defaults(hass: Any, row: dict[str, Any]) -> dict[str, Any]:
    config = DatapointConfig.from_dict(row)
    defaults = {
        field: getattr(config, field)
        for field in (
            "name",
            "kind",
            "semantic",
            "unit",
            "time_basis",
            "interval_seconds",
            "max_age_seconds",
            "fallback",
            "minimum_value",
            "maximum_value",
        )
        if getattr(config, field) is not None
    }
    if config.kind not in {"temperature", "duration"}:
        defaults["semantic"] = "physical_temperature"
    registry = er.async_get(hass)
    for slot, source in zip(SOURCE_SLOTS, config.sources, strict=False):
        values = source.as_dict()
        reference = values["entity"]
        defaults[f"{slot}_entity"] = _resolved_or_reference(registry, reference)
        for field in ("attribute", "timestamp_attribute", "unit", "max_age_seconds"):
            if values.get(field) is not None:
                defaults[f"{slot}_{field}"] = values[field]
    return defaults


def _mapping_from_input(
    hass: Any,
    user_input: dict[str, Any],
    mapping_id: str | None = None,
    preserved: MappingConfig | None = None,
) -> dict[str, str]:
    name = str(user_input[CONF_NAME]).strip()
    if not name or len(name) > 64:
        raise vol.Invalid("invalid_name")
    _validate_candidate_optional_sources(user_input)
    homekit_entity = _entity_reference(
        hass,
        str(user_input[CONF_HOMEKIT_ENTITY]),
        "homekit_controller",
        "climate",
        preserved.homekit_entity if preserved else None,
    )
    ecobee_entity = _entity_reference(
        hass,
        str(user_input[CONF_ECOBEE_ENTITY]),
        "ecobee",
        "climate",
        preserved.ecobee_entity if preserved else None,
    )
    homekit_device_id = _reference_device_id(hass, homekit_entity)
    ecobee_device_id = _reference_device_id(hass, ecobee_entity)
    identity_status = physical_identity_status(hass, homekit_entity, ecobee_entity)
    if identity_status is PhysicalIdentityStatus.MISMATCH:
        raise vol.Invalid("physical_device_mismatch")
    if identity_status is PhysicalIdentityStatus.UNPROVEN and not (
        preserved is not None
        and homekit_entity == preserved.homekit_entity
        and ecobee_entity == preserved.ecobee_entity
    ):
        raise vol.Invalid("physical_device_identity_unproven")
    mapping = MappingConfig(
        mapping_id=mapping_id or uuid4().hex,
        name=name,
        homekit_entity=homekit_entity,
        ecobee_entity=ecobee_entity,
        homekit_preset_entity=_homekit_action_reference(
            hass,
            user_input.get(CONF_HOMEKIT_PRESET_ENTITY),
            preserved.homekit_preset_entity if preserved else None,
            required_device_id=homekit_device_id,
            role="preset",
        ),
        homekit_clear_hold_entity=_homekit_action_reference(
            hass,
            user_input.get(CONF_HOMEKIT_CLEAR_HOLD_ENTITY),
            preserved.homekit_clear_hold_entity if preserved else None,
            required_device_id=homekit_device_id,
            role="clear_hold",
        ),
        homekit_temperature_entity=_temperature_entity_reference(
            hass,
            user_input.get(CONF_HOMEKIT_TEMPERATURE_ENTITY),
            preserved.homekit_temperature_entity if preserved else None,
            required_device_id=homekit_device_id,
        ),
        ecobee_notify_entity=_optional_entity_reference(
            hass,
            user_input.get(CONF_ECOBEE_NOTIFY_ENTITY),
            "ecobee",
            "notify",
            preserved.ecobee_notify_entity if preserved else None,
            required_device_id=ecobee_device_id,
        ),
        **{
            field_name: _air_quality_entity_reference(
                hass,
                user_input.get(config_key),
                getattr(preserved, field_name) if preserved else None,
                contract_name,
                error_key,
                required_device_id=ecobee_device_id,
            )
            for config_key, field_name, contract_name, error_key in (
                (
                    CONF_ECOBEE_AQI_ENTITY,
                    "ecobee_aqi_entity",
                    "aqi",
                    "invalid_ecobee_aqi_source",
                ),
                (
                    CONF_ECOBEE_CO2_ENTITY,
                    "ecobee_co2_entity",
                    "co2",
                    "invalid_ecobee_co2_source",
                ),
                (
                    CONF_ECOBEE_VOC_ENTITY,
                    "ecobee_voc_entity",
                    "voc",
                    "invalid_ecobee_voc_source",
                ),
            )
        },
    )
    return mapping.as_dict()


def _entity_reference(
    hass: Any,
    entity_id: str,
    platform: str,
    domain: str,
    preserve_reference: str | None = None,
) -> str:
    registry = er.async_get(hass)
    resolved_id = er.async_resolve_entity_id(registry, entity_id)
    entry = registry.async_get(resolved_id) if resolved_id else None
    if entry is None and preserve_reference and entity_id == preserve_reference:
        return preserve_reference
    if entry is None or entry.platform != platform or entry.domain != domain:
        raise vol.Invalid(f"invalid_{platform}_source")
    if platform == "homekit_controller" and (
        entry.device_id is None or dr.async_get(hass).async_get(entry.device_id) is None
    ):
        raise vol.Invalid("homekit_device_required")
    return entry.id


def _optional_entity_reference(
    hass: Any,
    entity_id: Any,
    platform: str,
    domain: str,
    preserve_reference: str | None = None,
    *,
    required_device_id: str | None = None,
) -> str | None:
    if not entity_id:
        return None
    reference = _entity_reference(
        hass, str(entity_id), platform, domain, preserve_reference
    )
    if (
        preserve_reference == reference
        and er.async_resolve_entity_id(er.async_get(hass), reference) is None
    ):
        return reference
    if required_device_id is None:
        # An absent parent can preserve saved intent, but cannot establish the
        # physical association of a newly selected optional source.
        if reference != preserve_reference:
            raise vol.Invalid(f"invalid_{platform}_source")
    elif _reference_device_id(hass, reference) != required_device_id:
        raise vol.Invalid(f"invalid_{platform}_source")
    return reference


def _reference_device_id(hass: Any, reference: str) -> str | None:
    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, reference)
    entry = registry.async_get(entity_id) if entity_id else None
    return entry.device_id if entry is not None else None


def _homekit_action_reference(
    hass: Any,
    entity_id: Any,
    preserve_reference: str | None,
    *,
    required_device_id: str | None,
    role: Literal["preset", "clear_hold"],
) -> str | None:
    reference = _optional_entity_reference(
        hass,
        entity_id,
        "homekit_controller",
        "select" if role == "preset" else "button",
        preserve_reference,
        required_device_id=required_device_id,
    )
    if reference is None or (
        reference == preserve_reference
        and er.async_resolve_entity_id(er.async_get(hass), reference) is None
    ):
        return reference
    if not homekit_action_contract_valid(hass, reference, role):
        raise vol.Invalid(f"invalid_homekit_{role}_source")
    return reference


def _temperature_entity_reference(
    hass: Any,
    entity_id: Any,
    preserve_reference: str | None,
    *,
    required_device_id: str | None,
) -> str | None:
    reference = _optional_entity_reference(
        hass,
        entity_id,
        "homekit_controller",
        "sensor",
        preserve_reference,
        required_device_id=required_device_id,
    )
    if reference is None:
        return None
    registry = er.async_get(hass)
    resolved_id = er.async_resolve_entity_id(registry, reference)
    if resolved_id is None and preserve_reference == reference:
        return reference
    if temperature_source_unit(hass, reference) is None:
        raise vol.Invalid("invalid_homekit_temperature_source")
    return reference


def _air_quality_entity_reference(
    hass: Any,
    entity_id: Any,
    preserve_reference: str | None,
    contract_name: str,
    error_key: str,
    *,
    required_device_id: str | None,
) -> str | None:
    reference = _optional_entity_reference(
        hass,
        entity_id,
        "ecobee",
        "sensor",
        preserve_reference,
        required_device_id=required_device_id,
    )
    if reference is None:
        return None
    registry = er.async_get(hass)
    if (
        er.async_resolve_entity_id(registry, reference) is None
        and preserve_reference == reference
    ):
        return reference
    if not sensor_contract_valid(
        hass, reference, AIR_QUALITY_SENSOR_CONTRACTS[contract_name]
    ):
        raise vol.Invalid(error_key)
    return reference


def _validate_candidate_optional_sources(candidate: dict[str, Any]) -> None:
    references = [
        str(reference)
        for key in OPTIONAL_SOURCE_KEYS
        if (reference := candidate.get(key))
    ]
    if len(references) != len(set(references)):
        raise vol.Invalid("duplicate_optional_source")


def _validate_no_duplicate_sources(
    existing: list[dict[str, str]], candidate: dict[str, str]
) -> None:
    _validate_candidate_optional_sources(candidate)
    candidate_name = candidate[CONF_NAME].strip().casefold()
    candidate_optional = {
        reference for key in OPTIONAL_SOURCE_KEYS if (reference := candidate.get(key))
    }
    for mapping in existing:
        if mapping[CONF_NAME].strip().casefold() == candidate_name:
            raise vol.Invalid("duplicate_mapping_name")
        if mapping[CONF_HOMEKIT_ENTITY] == candidate[CONF_HOMEKIT_ENTITY]:
            raise vol.Invalid("duplicate_homekit_source")
        if mapping[CONF_ECOBEE_ENTITY] == candidate[CONF_ECOBEE_ENTITY]:
            raise vol.Invalid("duplicate_ecobee_source")
        existing_optional = {
            reference for key in OPTIONAL_SOURCE_KEYS if (reference := mapping.get(key))
        }
        if candidate_optional & existing_optional:
            raise vol.Invalid("duplicate_optional_source")


def _mapping_selector(mappings: list[dict[str, str]]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(
                    value=mapping[CONF_MAPPING_ID], label=mapping[CONF_NAME]
                )
                for mapping in mappings
            ]
        )
    )


def _mapping_form_defaults(hass: Any, mapping: dict[str, str]) -> dict[str, Any]:
    registry = er.async_get(hass)
    return {
        CONF_NAME: mapping[CONF_NAME],
        CONF_HOMEKIT_ENTITY: er.async_resolve_entity_id(
            registry, mapping[CONF_HOMEKIT_ENTITY]
        )
        or mapping[CONF_HOMEKIT_ENTITY],
        CONF_ECOBEE_ENTITY: er.async_resolve_entity_id(
            registry, mapping[CONF_ECOBEE_ENTITY]
        )
        or mapping[CONF_ECOBEE_ENTITY],
        **{
            key: _resolved_or_reference(registry, mapping.get(key))
            for key in OPTIONAL_SOURCE_KEYS
        },
    }


def _resolved_or_reference(
    registry: er.EntityRegistry, reference: str | None
) -> str | None:
    if reference is None:
        return None
    return er.async_resolve_entity_id(registry, reference) or reference


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    def selector(minimum: int, maximum: int, step: int) -> NumberSelector:
        return NumberSelector(
            NumberSelectorConfig(
                min=minimum,
                max=maximum,
                step=step,
                mode=NumberSelectorMode.BOX,
            )
        )

    return vol.Schema(
        {
            vol.Required(
                CONF_ECOBEE_STALE_SECONDS,
                default=defaults[CONF_ECOBEE_STALE_SECONDS],
            ): selector(300, 7200, 60),
            vol.Required(
                CONF_CONFIRMATION_SECONDS,
                default=defaults[CONF_CONFIRMATION_SECONDS],
            ): selector(300, 1800, 30),
            vol.Optional(
                "configure_read_policy",
                description={
                    "suggested_value": defaults.get("configure_read_policy", False)
                },
            ): BOOLEAN_SELECTOR,
        }
    )


def _validate_timing_options(user_input: dict[str, Any]) -> dict[str, int]:
    """Validate selector-aligned timing values without breaking form serialization."""

    def validate(value: Any, minimum: int, maximum: int, step: int) -> int:
        if isinstance(value, bool):
            raise vol.Invalid("timing value must be an integer")
        try:
            number = float(value)
        except (TypeError, ValueError) as err:
            raise vol.Invalid("timing value must be numeric") from err
        if (
            not isfinite(number)
            or number != int(number)
            or not minimum <= number <= maximum
            or (int(number) - minimum) % step != 0
        ):
            raise vol.Invalid(f"timing value must use {step}-second steps")
        return int(number)

    return {
        CONF_ECOBEE_STALE_SECONDS: validate(
            user_input[CONF_ECOBEE_STALE_SECONDS], 300, 7200, 60
        ),
        CONF_CONFIRMATION_SECONDS: validate(
            user_input[CONF_CONFIRMATION_SECONDS], 300, 1800, 30
        ),
    }
