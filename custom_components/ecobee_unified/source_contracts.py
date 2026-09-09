"""Supported registry contracts for explicitly mapped source entities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfDensity,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .models import finite_number


class PhysicalIdentityStatus(StrEnum):
    """Proof state for one HomeKit and Ecobee thermostat pairing."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNPROVEN = "unproven"


@dataclass(frozen=True, slots=True)
class SensorContract:
    """One bounded sensor semantic accepted from the Ecobee integration."""

    device_class: SensorDeviceClass
    unit: str | None


AIR_QUALITY_SENSOR_CONTRACTS = {
    "aqi": SensorContract(SensorDeviceClass.AQI, None),
    "co2": SensorContract(SensorDeviceClass.CO2, UnitOfRatio.PARTS_PER_MILLION),
    "voc": SensorContract(
        SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
        UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
    ),
}
HOMEKIT_PRESET_OPTIONS = frozenset({"home", "sleep", "away"})


def physical_identity_status(
    hass: HomeAssistant, homekit_reference: str, ecobee_reference: str
) -> PhysicalIdentityStatus:
    """Compare the stable identities exposed by installed Core integrations."""

    homekit_device = _device_for_reference(hass, homekit_reference)
    ecobee_device = _device_for_reference(hass, ecobee_reference)
    if homekit_device is None or ecobee_device is None:
        return PhysicalIdentityStatus.UNPROVEN
    homekit_serial = _normalized_identity(homekit_device.serial_number)
    ecobee_identifiers = {
        normalized
        for domain, value in ecobee_device.identifiers
        if domain == "ecobee" and (normalized := _normalized_identity(value))
    }
    if homekit_serial is None or len(ecobee_identifiers) != 1:
        return PhysicalIdentityStatus.UNPROVEN
    return (
        PhysicalIdentityStatus.MATCH
        if homekit_serial in ecobee_identifiers
        else PhysicalIdentityStatus.MISMATCH
    )


def sensor_contract_valid(
    hass: HomeAssistant, entity_reference: str, contract: SensorContract
) -> bool:
    """Validate one sensor's physical quantity, unit, and bounded state shape."""

    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, entity_reference)
    entry = registry.async_get(entity_id) if entity_id else None
    state = hass.states.get(entity_id) if entity_id else None
    if entry is None:
        return False
    # A sensor's live value may use a user-selected unit or device class. Native
    # registry metadata must never relabel an explicitly different live value.
    attributes = state.attributes if state else {}
    device_class = attributes.get(ATTR_DEVICE_CLASS, entry.original_device_class)
    unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT, entry.unit_of_measurement)
    normalized_unit = str(unit) if unit is not None and unit != "" else None
    if device_class != contract.device_class or normalized_unit != contract.unit:
        return False
    if state is None or state.state in {"unknown", "unavailable"}:
        return True
    value = finite_number(state.state, allow_text=True)
    return value is not None and value >= 0


def temperature_source_unit(
    hass: HomeAssistant, entity_reference: str
) -> UnitOfTemperature | None:
    """Validate temperature metadata and present numeric state consistently.

    Explicit live class/unit attributes belong to the current value, including
    invalid null or empty attributes. Registry metadata fills only absent keys.
    Missing, unknown, or unavailable state retains a valid metadata contract;
    availability, association, conversion, and physical limits have other owners.
    """

    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, entity_reference)
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None:
        return None
    state = hass.states.get(entry.entity_id)
    attributes = state.attributes if state else {}
    device_class = attributes.get(ATTR_DEVICE_CLASS, entry.original_device_class)
    if device_class != SensorDeviceClass.TEMPERATURE:
        return None
    unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT, entry.unit_of_measurement)
    try:
        temperature_unit = UnitOfTemperature(str(unit))
    except ValueError:
        return None
    if (
        state is not None
        and state.state not in {"unknown", "unavailable"}
        and finite_number(state.state, allow_text=True) is None
    ):
        return None
    return temperature_unit


def homekit_action_contract_valid(
    hass: HomeAssistant,
    entity_reference: str,
    role: Literal["preset", "clear_hold"],
) -> bool:
    """Reject action roles contradicted by supported public entity metadata.

    Current Mode has a supported option contract. Clear Hold has no positive
    public role marker: an explicitly selected uncategorized, classless button
    can only be checked for known incompatibility, not proven to clear a hold.
    Association and current writer availability are separate caller checks.
    """

    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, entity_reference)
    entry = registry.async_get(entity_id) if entity_id else None
    expected_domain = "select" if role == "preset" else "button"
    if (
        entry is None
        or entry.platform != "homekit_controller"
        or entry.domain != expected_domain
        or entry.entity_category is not None
    ):
        return False
    state = hass.states.get(entry.entity_id)
    attributes = state.attributes if state else {}
    if any(
        device_class is not None and device_class != ""
        for device_class in (
            entry.original_device_class,
            entry.device_class,
            attributes.get(ATTR_DEVICE_CLASS),
        )
    ):
        return False
    if role == "clear_hold":
        return entry.translation_key is None
    if entry.translation_key not in {None, "ecobee_mode"}:
        return False
    options = attributes.get("options", (entry.capabilities or {}).get("options"))
    if not isinstance(options, list | tuple) or not 1 <= len(options) <= 3:
        return False
    if not all(isinstance(option, str) for option in options):
        return False
    normalized = {option.casefold() for option in options}
    return len(normalized) == len(options) and normalized <= HOMEKIT_PRESET_OPTIONS


def _device_for_reference(
    hass: HomeAssistant, entity_reference: str
) -> dr.DeviceEntry | None:
    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, entity_reference)
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None or entry.device_id is None:
        return None
    device = dr.async_get(hass).async_get(entry.device_id)
    # Child devices cannot prove the physical thermostat's serial identity.
    return device if isinstance(device, dr.DeviceEntry) else None


def _normalized_identity(value: str | None) -> str | None:
    if value is None or not (normalized := value.strip().casefold()):
        return None
    return normalized
