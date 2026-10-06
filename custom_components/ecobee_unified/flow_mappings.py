"""Thermostat mapping forms and source-reference validation."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .const import (
    CONF_ADD_ANOTHER,
    CONF_CONFIRM_CHANGE,
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
    CONF_ECOBEE_VOC_ENTITY,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_HOMEKIT_TEMPERATURE_ENTITY,
    CONF_MAPPING_ID,
    CONF_NAME,
)
from .flow_common import (
    BOOLEAN_SELECTOR,
    ECOBEE_CLIMATE_SELECTOR,
    ECOBEE_NOTIFY_SELECTOR,
    ECOBEE_SENSOR_SELECTOR,
    HOMEKIT_BUTTON_SELECTOR,
    HOMEKIT_CLIMATE_SELECTOR,
    HOMEKIT_SELECT_SELECTOR,
    HOMEKIT_SENSOR_SELECTOR,
    _resolved_or_reference,
)
from .models import MappingConfig
from .source_contracts import (
    AIR_QUALITY_SENSOR_CONTRACTS,
    PhysicalIdentityStatus,
    homekit_action_contract_valid,
    physical_identity_status,
    sensor_contract_valid,
    temperature_source_unit,
)

OPTIONAL_SOURCE_KEYS = (
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_TEMPERATURE_ENTITY,
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_VOC_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
)
READ_POLICY_OPTIONS = ("homekit_first", "ecobee_first", "homekit_only", "ecobee_only")


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
    preserve_reference: str | None,
    *,
    required_device_id: str | None,
) -> str | None:
    if not entity_id:
        return None
    reference = _entity_reference(
        hass, str(entity_id), platform, domain, preserve_reference
    )
    if _preserved_unresolved(hass, reference, preserve_reference):
        return reference
    if required_device_id is None:
        # An absent parent can preserve saved intent, but cannot establish the
        # physical association of a newly selected optional source.
        if reference != preserve_reference:
            raise vol.Invalid(f"invalid_{platform}_source")
    elif _reference_device_id(hass, reference) != required_device_id:
        raise vol.Invalid(f"invalid_{platform}_source")
    return reference


def _preserved_unresolved(
    hass: Any, reference: str, preserve_reference: str | None
) -> bool:
    """Return whether a saved reference is kept although it no longer resolves."""
    return (
        preserve_reference == reference
        and er.async_resolve_entity_id(er.async_get(hass), reference) is None
    )


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
    if reference is None or _preserved_unresolved(hass, reference, preserve_reference):
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
    if _preserved_unresolved(hass, reference, preserve_reference):
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
    if _preserved_unresolved(hass, reference, preserve_reference):
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
