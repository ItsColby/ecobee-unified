"""Native configuration and reconfiguration flows for Ecobee Unified."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any

import probatio
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import (
    CONF_ADD_ANOTHER,
    CONF_CONFIRM_CHANGE,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_MAPPING_ID,
    CONF_MAPPINGS,
    CONF_NAME,
    CONF_RELOAD_SILENT_TEMPERATURE_SOURCE,
    DEFAULT_CONFIRMATION_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DEFAULT_RELOAD_SILENT_TEMPERATURE_SOURCE,
    DOMAIN,
    NAME,
    RECONFIGURE_MENU_OPTIONS,
)
from .flow_common import (
    BOOLEAN_SELECTOR,
    SOURCE_SLOTS,
    _row_selection_schema,
    _single_row,
)
from .flow_datapoints import (
    _datapoint_form_defaults,
    _datapoint_from_input,
    _datapoint_schema,
    _validate_datapoint_collection,
)
from .flow_mappings import (
    OPTIONAL_SOURCE_KEYS,  # noqa: F401  (re-exported for the flow tests)
    READ_POLICY_OPTIONS,
    _mapping_form_defaults,
    _mapping_from_input,
    _mapping_schema,
    _mapping_selector,
    _validate_no_duplicate_sources,
)
from .flow_review import _reconfigure_summary
from .models import READ_POLICIES, READ_POLICY_FIELDS, MappingConfig, merge_mapping_data


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
            except probatio.Invalid as err:
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
            data_schema=_row_selection_schema(self._pending_datapoints, "datapoint_id"),
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
            except (ValueError, probatio.Invalid) as err:
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
            data_schema=_row_selection_schema(self._pending_datapoints, "datapoint_id"),
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
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_CONFIRM_CHANGE, default=False
                    ): BOOLEAN_SELECTOR
                }
            ),
            errors=errors,
            description_placeholders={"name": str(current["name"])},
        )

    def _selected_datapoint(self) -> dict[str, Any] | None:
        return _single_row(
            self._pending_datapoints, "datapoint_id", self._selected_datapoint_id
        )

    async def async_step_reconfigure_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one mapping without replacing existing stable identities."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mapping = _mapping_from_input(self.hass, user_input)
                _validate_no_duplicate_sources(self._pending_mappings, mapping)
            except probatio.Invalid as err:
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
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_MAPPING_ID): _mapping_selector(
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
            except probatio.Invalid as err:
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
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_MAPPING_ID): _mapping_selector(
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
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_CONFIRM_CHANGE, default=False
                    ): BOOLEAN_SELECTOR
                }
            ),
            errors=errors,
            description_placeholders={"name": self._selected_mapping()[CONF_NAME]},
        )

    async def async_step_reconfigure_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Review staged changes, then atomically save the complete collection."""

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

        if user_input is None and accepted_data != original_data:
            return self.async_show_form(
                step_id="reconfigure_finish",
                data_schema=probatio.Schema({}),
                last_step=True,
                description_placeholders={
                    "changes": _reconfigure_summary(
                        self.hass, original_data, accepted_data
                    )
                },
            )

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
            except probatio.Invalid:
                errors["base"] = "invalid_timing"
            else:
                self._pending_options.update(validated_input)
                self._pending_options[CONF_RELOAD_SILENT_TEMPERATURE_SOURCE] = (
                    user_input.get(CONF_RELOAD_SILENT_TEMPERATURE_SOURCE) is True
                )
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
            CONF_RELOAD_SILENT_TEMPERATURE_SOURCE: original_options.get(
                CONF_RELOAD_SILENT_TEMPERATURE_SOURCE,
                DEFAULT_RELOAD_SILENT_TEMPERATURE_SOURCE,
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
            data_schema=probatio.Schema(
                {probatio.Required(CONF_MAPPING_ID): _mapping_selector(mappings)}
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
        schema: dict[probatio.Marker, Any] = {
            probatio.Required(
                field, default=defaults.get(field, "homekit_first")
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(READ_POLICY_OPTIONS), translation_key="read_policy"
                )
            )
            for field in READ_POLICY_FIELDS
        }
        schema[probatio.Optional("configure_another", default=False)] = BOOLEAN_SELECTOR
        return self.async_show_form(
            step_id="read_policy",
            data_schema=probatio.Schema(schema),
            errors=errors,
        )


def _options_schema(defaults: dict[str, Any]) -> probatio.Schema:
    def selector(minimum: int, maximum: int, step: int) -> NumberSelector:
        return NumberSelector(
            NumberSelectorConfig(
                min=minimum,
                max=maximum,
                step=step,
                mode=NumberSelectorMode.BOX,
            )
        )

    return probatio.Schema(
        {
            probatio.Required(
                CONF_ECOBEE_STALE_SECONDS,
                default=defaults[CONF_ECOBEE_STALE_SECONDS],
            ): selector(300, 7200, 60),
            probatio.Required(
                CONF_CONFIRMATION_SECONDS,
                default=defaults[CONF_CONFIRMATION_SECONDS],
            ): selector(300, 1800, 30),
            probatio.Required(
                CONF_RELOAD_SILENT_TEMPERATURE_SOURCE,
                default=defaults[CONF_RELOAD_SILENT_TEMPERATURE_SOURCE],
            ): BOOLEAN_SELECTOR,
            probatio.Optional(
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
            raise probatio.Invalid("timing value must be an integer")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise probatio.Invalid("timing value must be numeric") from err
        if (
            not isfinite(number)
            or number != int(number)
            or not minimum <= number <= maximum
            or (int(number) - minimum) % step != 0
        ):
            raise probatio.Invalid(f"timing value must use {step}-second steps")
        return int(number)

    return {
        CONF_ECOBEE_STALE_SECONDS: validate(
            user_input[CONF_ECOBEE_STALE_SECONDS], 300, 7200, 60
        ),
        CONF_CONFIRMATION_SECONDS: validate(
            user_input[CONF_CONFIRMATION_SECONDS], 300, 1800, 30
        ),
    }
