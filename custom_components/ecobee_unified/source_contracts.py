"""Supported registry contracts for explicitly mapped source entities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfDensity,
    UnitOfRatio,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


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
    device_class = entry.original_device_class or (
        state.attributes.get(ATTR_DEVICE_CLASS) if state else None
    )
    unit = entry.unit_of_measurement or (
        state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if state else None
    )
    normalized_unit = str(unit) if unit not in {None, ""} else None
    if device_class != contract.device_class or normalized_unit != contract.unit:
        return False
    if state is None or state.state in {"unknown", "unavailable"}:
        return True
    try:
        value = float(state.state)
    except ValueError:
        return False
    return isfinite(value) and value >= 0


def _device_for_reference(
    hass: HomeAssistant, entity_reference: str
) -> dr.DeviceEntry | None:
    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, entity_reference)
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None or entry.device_id is None:
        return None
    return dr.async_get(hass).async_get(entry.device_id)


def _normalized_identity(value: str | None) -> str | None:
    if value is None or not (normalized := value.strip().casefold()):
        return None
    return normalized
