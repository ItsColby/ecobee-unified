"""Native configuration and reconfiguration flows for Ecobee Unified."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
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
    TextSelector,
)

from .const import (
    CONF_ADD_ANOTHER,
    CONF_BEESTAT_STALE_SECONDS,
    CONF_CONFIRM_CHANGE,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_STALE_SECONDS,
    CONF_MAPPING_ID,
    CONF_MAPPINGS,
    CONF_NAME,
    CONF_NEXT_TRANSITION_ENTITY,
    CONF_SCHEDULED_PROFILE_ENTITY,
    DEFAULT_BEESTAT_STALE_SECONDS,
    DEFAULT_CONFIRMATION_SECONDS,
    DEFAULT_ECOBEE_STALE_SECONDS,
    DEFAULT_HOMEKIT_STALE_SECONDS,
    DOMAIN,
    NAME,
)
from .models import MappingConfig

CLIMATE_SELECTOR = EntitySelector(EntitySelectorConfig(domain="climate"))
SENSOR_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor"))
BOOLEAN_SELECTOR = BooleanSelector()


class EcobeeUnifiedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single multi-thermostat Ecobee Unified entry."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._pending_mappings: list[dict[str, str]] = []
        self._selected_mapping_id: str | None = None

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
        self._abort_if_unique_id_configured()
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

        if not self._pending_mappings:
            entry = self._get_reconfigure_entry()
            self._pending_mappings = deepcopy(entry.data.get(CONF_MAPPINGS, []))
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=(
                "reconfigure_add",
                "reconfigure_edit",
                "reconfigure_remove",
                "reconfigure_finish",
            ),
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
                updated = _mapping_from_input(
                    self.hass,
                    user_input,
                    mapping_id=str(current[CONF_MAPPING_ID]),
                    preserved=MappingConfig.from_dict(current),
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
                    current[key] != updated[key]
                    for key in (CONF_HOMEKIT_ENTITY, CONF_ECOBEE_ENTITY)
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
                {**defaults, **(user_input or {})}, include_confirmation=True
            ),
            errors=errors,
        )

    async def async_step_reconfigure_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a mapping to remove."""

        if len(self._pending_mappings) <= 1:
            return self.async_abort(reason="one_mapping_required")
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
        return self.async_update_reload_and_abort(
            entry,
            data={CONF_MAPPINGS: self._pending_mappings},
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
    """Manage documented freshness and confirmation thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {
            CONF_HOMEKIT_STALE_SECONDS: self.config_entry.options.get(
                CONF_HOMEKIT_STALE_SECONDS, DEFAULT_HOMEKIT_STALE_SECONDS
            ),
            CONF_ECOBEE_STALE_SECONDS: self.config_entry.options.get(
                CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS
            ),
            CONF_BEESTAT_STALE_SECONDS: self.config_entry.options.get(
                CONF_BEESTAT_STALE_SECONDS, DEFAULT_BEESTAT_STALE_SECONDS
            ),
            CONF_CONFIRMATION_SECONDS: self.config_entry.options.get(
                CONF_CONFIRMATION_SECONDS, DEFAULT_CONFIRMATION_SECONDS
            ),
        }
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(defaults)
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
        ): CLIMATE_SELECTOR,
        vol.Required(
            CONF_ECOBEE_ENTITY,
            description={"suggested_value": defaults.get(CONF_ECOBEE_ENTITY, "")},
        ): CLIMATE_SELECTOR,
        vol.Optional(
            CONF_SCHEDULED_PROFILE_ENTITY,
            description={
                "suggested_value": defaults.get(CONF_SCHEDULED_PROFILE_ENTITY)
            },
        ): SENSOR_SELECTOR,
        vol.Optional(
            CONF_NEXT_TRANSITION_ENTITY,
            description={"suggested_value": defaults.get(CONF_NEXT_TRANSITION_ENTITY)},
        ): SENSOR_SELECTOR,
    }
    if include_add:
        schema[vol.Required(CONF_ADD_ANOTHER, default=False)] = BOOLEAN_SELECTOR
    if include_confirmation:
        schema[vol.Required(CONF_CONFIRM_CHANGE, default=False)] = BOOLEAN_SELECTOR
    return vol.Schema(schema)


def _mapping_from_input(
    hass: Any,
    user_input: dict[str, Any],
    mapping_id: str | None = None,
    preserved: MappingConfig | None = None,
) -> dict[str, str]:
    name = str(user_input[CONF_NAME]).strip()
    if not name or len(name) > 64:
        raise vol.Invalid("invalid_name")
    mapping = MappingConfig(
        mapping_id=mapping_id or uuid4().hex,
        name=name,
        homekit_entity=_entity_reference(
            hass,
            str(user_input[CONF_HOMEKIT_ENTITY]),
            "homekit_controller",
            "climate",
            preserved.homekit_entity if preserved else None,
        ),
        ecobee_entity=_entity_reference(
            hass,
            str(user_input[CONF_ECOBEE_ENTITY]),
            "ecobee",
            "climate",
            preserved.ecobee_entity if preserved else None,
        ),
        scheduled_profile_entity=_optional_entity_reference(
            hass,
            user_input.get(CONF_SCHEDULED_PROFILE_ENTITY),
            "beestat_statistics",
            "sensor",
            preserved.scheduled_profile_entity if preserved else None,
        ),
        next_transition_entity=_optional_entity_reference(
            hass,
            user_input.get(CONF_NEXT_TRANSITION_ENTITY),
            "beestat_statistics",
            "sensor",
            preserved.next_transition_entity if preserved else None,
        ),
    )
    if (
        mapping.scheduled_profile_entity
        and mapping.scheduled_profile_entity == mapping.next_transition_entity
    ):
        raise vol.Invalid("duplicate_beestat_source")
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
) -> str | None:
    if not entity_id:
        return None
    return _entity_reference(hass, str(entity_id), platform, domain, preserve_reference)


def _validate_no_duplicate_sources(
    existing: list[dict[str, str]], candidate: dict[str, str]
) -> None:
    candidate_beestat = {
        reference
        for key in (CONF_SCHEDULED_PROFILE_ENTITY, CONF_NEXT_TRANSITION_ENTITY)
        if (reference := candidate.get(key))
    }
    for mapping in existing:
        if mapping[CONF_HOMEKIT_ENTITY] == candidate[CONF_HOMEKIT_ENTITY]:
            raise vol.Invalid("duplicate_homekit_source")
        if mapping[CONF_ECOBEE_ENTITY] == candidate[CONF_ECOBEE_ENTITY]:
            raise vol.Invalid("duplicate_ecobee_source")
        existing_beestat = {
            reference
            for key in (CONF_SCHEDULED_PROFILE_ENTITY, CONF_NEXT_TRANSITION_ENTITY)
            if (reference := mapping.get(key))
        }
        if candidate_beestat & existing_beestat:
            raise vol.Invalid("duplicate_beestat_source")


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
        CONF_SCHEDULED_PROFILE_ENTITY: _resolved_or_reference(
            registry, mapping.get(CONF_SCHEDULED_PROFILE_ENTITY)
        ),
        CONF_NEXT_TRANSITION_ENTITY: _resolved_or_reference(
            registry, mapping.get(CONF_NEXT_TRANSITION_ENTITY)
        ),
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
                CONF_HOMEKIT_STALE_SECONDS,
                default=defaults[CONF_HOMEKIT_STALE_SECONDS],
            ): selector(60, 3600, 30),
            vol.Required(
                CONF_ECOBEE_STALE_SECONDS,
                default=defaults[CONF_ECOBEE_STALE_SECONDS],
            ): selector(300, 7200, 60),
            vol.Required(
                CONF_BEESTAT_STALE_SECONDS,
                default=defaults[CONF_BEESTAT_STALE_SECONDS],
            ): selector(900, 86_400, 300),
            vol.Required(
                CONF_CONFIRMATION_SECONDS,
                default=defaults[CONF_CONFIRMATION_SECONDS],
            ): selector(300, 1800, 30),
        }
    )
