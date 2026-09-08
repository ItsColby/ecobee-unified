"""Physical source identity across device-registry API versions."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.source_contracts import _device_for_reference


async def test_physical_source_device_is_preserved(hass: HomeAssistant) -> None:
    """Real device entries remain the identity owner on every supported Core."""
    config = MockConfigEntry(domain="homekit_controller")
    config.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id,
        identifiers={("homekit_controller", "physical_thermostat_a")},
        serial_number="THERMOSTAT-A",
    )
    entity = er.async_get(hass).async_get_or_create(
        "climate",
        "homekit_controller",
        "physical_source_a",
        config_entry=config,
        device_id=device.id,
    )
    assert _device_for_reference(hass, entity.id) is device


@pytest.mark.skipif(
    not hasattr(dr.DeviceRegistry, "async_get_or_create_child"),
    reason="This Core version predates native child devices",
)
async def test_child_source_cannot_supply_physical_identity(
    hass: HomeAssistant,
) -> None:
    """Do not read parent-only serial metadata through a child compatibility shim."""
    config = MockConfigEntry(domain="homekit_controller")
    config.add_to_hass(hass)
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=config.entry_id,
        identifiers={("homekit_controller", "physical_thermostat_a")},
        serial_number="THERMOSTAT-A",
    )
    child = registry.async_get_or_create_child(
        config_entry_id=config.entry_id,
        parent_device_id=parent.id,
        identifiers={("homekit_controller", "child_source_a")},
    )
    entity = er.async_get(hass).async_get_or_create(
        "climate",
        "homekit_controller",
        "child_climate_a",
        config_entry=config,
        device_id=child.id,
    )
    assert _device_for_reference(hass, entity.id) is None
