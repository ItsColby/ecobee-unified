"""Native configuration and reconfiguration flows for Ecobee Unified."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfTemperature,
)
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
    CONF_CONFIRM_CHANGE,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_ECOBEE_VOC_ENTITY,
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
)
from .models import MappingConfig
from .source_contracts import (
    AIR_QUALITY_SENSOR_CONTRACTS,
    PhysicalIdentityStatus,
    physical_identity_status,
    sensor_contract_valid,
)

CLIMATE_SELECTOR = EntitySelector(EntitySelectorConfig(domain="climate"))
SENSOR_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor"))
SELECT_SELECTOR = EntitySelector(EntitySelectorConfig(domain="select"))
BUTTON_SELECTOR = EntitySelector(EntitySelectorConfig(domain="button"))
NOTIFY_SELECTOR = EntitySelector(EntitySelectorConfig(domain="notify"))
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


class EcobeeUnifiedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single multi-thermostat Ecobee Unified entry."""

    VERSION = 1
    MINOR_VERSION = 3

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
    """Manage cadence-backed freshness and confirmation thresholds."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {
            CONF_ECOBEE_STALE_SECONDS: self.config_entry.options.get(
                CONF_ECOBEE_STALE_SECONDS, DEFAULT_ECOBEE_STALE_SECONDS
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
            CONF_HOMEKIT_PRESET_ENTITY,
            description={"suggested_value": defaults.get(CONF_HOMEKIT_PRESET_ENTITY)},
        ): SELECT_SELECTOR,
        vol.Optional(
            CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
            description={
                "suggested_value": defaults.get(CONF_HOMEKIT_CLEAR_HOLD_ENTITY)
            },
        ): BUTTON_SELECTOR,
        vol.Optional(
            CONF_HOMEKIT_TEMPERATURE_ENTITY,
            description={
                "suggested_value": defaults.get(CONF_HOMEKIT_TEMPERATURE_ENTITY)
            },
        ): SENSOR_SELECTOR,
        vol.Optional(
            CONF_ECOBEE_NOTIFY_ENTITY,
            description={"suggested_value": defaults.get(CONF_ECOBEE_NOTIFY_ENTITY)},
        ): NOTIFY_SELECTOR,
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
        ] = SENSOR_SELECTOR
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
    if (
        any(
            user_input.get(key)
            for key in (
                CONF_ECOBEE_AQI_ENTITY,
                CONF_ECOBEE_CO2_ENTITY,
                CONF_ECOBEE_VOC_ENTITY,
                CONF_ECOBEE_NOTIFY_ENTITY,
            )
        )
        and ecobee_device_id is None
    ):
        raise vol.Invalid("invalid_ecobee_source")
    mapping = MappingConfig(
        mapping_id=mapping_id or uuid4().hex,
        name=name,
        homekit_entity=homekit_entity,
        ecobee_entity=ecobee_entity,
        homekit_preset_entity=_optional_entity_reference(
            hass,
            user_input.get(CONF_HOMEKIT_PRESET_ENTITY),
            "homekit_controller",
            "select",
            preserved.homekit_preset_entity if preserved else None,
            required_device_id=homekit_device_id,
        ),
        homekit_clear_hold_entity=_optional_entity_reference(
            hass,
            user_input.get(CONF_HOMEKIT_CLEAR_HOLD_ENTITY),
            "homekit_controller",
            "button",
            preserved.homekit_clear_hold_entity if preserved else None,
            required_device_id=homekit_device_id,
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
    if (
        required_device_id is not None
        and _reference_device_id(hass, reference) != required_device_id
    ):
        raise vol.Invalid(f"invalid_{platform}_source")
    return reference


def _reference_device_id(hass: Any, reference: str) -> str | None:
    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, reference)
    entry = registry.async_get(entity_id) if entity_id else None
    return entry.device_id if entry is not None else None


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
    entry = registry.async_get(resolved_id) if resolved_id else None
    state = hass.states.get(resolved_id) if resolved_id else None
    if entry is None:
        raise vol.Invalid("invalid_homekit_temperature_source")
    device_class = entry.original_device_class or (
        state.attributes.get(ATTR_DEVICE_CLASS) if state else None
    )
    unit = entry.unit_of_measurement or (
        state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
    )
    try:
        UnitOfTemperature(str(unit))
    except ValueError as err:
        raise vol.Invalid("invalid_homekit_temperature_source") from err
    if device_class != SensorDeviceClass.TEMPERATURE:
        raise vol.Invalid("invalid_homekit_temperature_source")
    if state is not None and state.state not in {"unknown", "unavailable"}:
        try:
            if not isfinite(float(state.state)):
                raise ValueError
        except ValueError as err:
            raise vol.Invalid("invalid_homekit_temperature_source") from err
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
    candidate_optional = {
        reference for key in OPTIONAL_SOURCE_KEYS if (reference := candidate.get(key))
    }
    for mapping in existing:
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
            for key in (
                CONF_HOMEKIT_PRESET_ENTITY,
                CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
                CONF_HOMEKIT_TEMPERATURE_ENTITY,
                CONF_ECOBEE_AQI_ENTITY,
                CONF_ECOBEE_CO2_ENTITY,
                CONF_ECOBEE_VOC_ENTITY,
                CONF_ECOBEE_NOTIFY_ENTITY,
            )
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
        }
    )
