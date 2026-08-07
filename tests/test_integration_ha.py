"""Exact Home Assistant Core 2026.8 integration-contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.const import (
    CONF_ADD_ANOTHER,
    CONF_ECOBEE_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_MAPPINGS,
    CONF_NAME,
    DOMAIN,
)
from custom_components.ecobee_unified.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import MappingConfig

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_config_flow_creates_two_explicit_mappings(
    hass: HomeAssistant,
) -> None:
    hk_a = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec_a = _register_source(hass, "ecobee", "ec_a")
    hk_b = _register_source(hass, "homekit_controller", "hk_b", with_device=True)
    ec_b = _register_source(hass, "ecobee", "ec_b")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "mapping"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Zone A",
            CONF_HOMEKIT_ENTITY: hk_a.entity_id,
            CONF_ECOBEE_ENTITY: ec_a.entity_id,
            CONF_ADD_ANOTHER: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "mapping"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Zone B",
            CONF_HOMEKIT_ENTITY: hk_b.entity_id,
            CONF_ECOBEE_ENTITY: ec_b.entity_id,
            CONF_ADD_ANOTHER: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(result["data"][CONF_MAPPINGS]) == 2
    assert result["data"][CONF_MAPPINGS][0][CONF_HOMEKIT_ENTITY] == hk_a.id
    assert result["data"][CONF_MAPPINGS][1][CONF_ECOBEE_ENTITY] == ec_b.id


async def test_config_flow_rejects_wrong_platform_and_duplicates(
    hass: HomeAssistant,
) -> None:
    wrong = _register_source(hass, "demo", "wrong")
    ec_a = _register_source(hass, "ecobee", "ec_a")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Zone A",
            CONF_HOMEKIT_ENTITY: wrong.entity_id,
            CONF_ECOBEE_ENTITY: ec_a.entity_id,
            CONF_ADD_ANOTHER: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_homekit_controller_source"


async def test_load_links_entities_to_source_devices_and_unloads_cleanly(
    hass: HomeAssistant,
) -> None:
    hk_a = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec_a = _register_source(hass, "ecobee", "ec_a")
    hk_b = _register_source(hass, "homekit_controller", "hk_b", with_device=True)
    ec_b = _register_source(hass, "ecobee", "ec_b")
    entry = _entry(
        hass,
        (
            _mapping("mapping_a", "Zone A", hk_a, ec_a),
            _mapping("mapping_b", "Zone B", hk_b, ec_b),
        ),
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    unified_a = registry.async_get_entity_id("climate", DOMAIN, "mapping_a")
    unified_b = registry.async_get_entity_id("climate", DOMAIN, "mapping_b")
    assert unified_a is not None
    assert unified_b is not None
    assert registry.async_get(unified_a).device_id == hk_a.device_id
    assert registry.async_get(unified_b).device_id == hk_b.device_id

    source_device = dr.async_get(hass).async_get(hk_a.device_id)
    assert source_device is not None
    assert source_device.config_entry_id != entry.entry_id
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_rename_loss_fallback_recovery_and_removal_repair(
    hass: HomeAssistant,
) -> None:
    hk = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec = _register_source(hass, "ecobee", "ec_a")
    entry = _entry(hass, (_mapping("mapping_a", "Zone A", hk, ec),))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    manager = entry.runtime_data.manager
    assert manager.snapshot("mapping_a").current_temperature == 20.0

    registry = er.async_get(hass)
    registry.async_update_entity(hk.entity_id, new_entity_id="climate.zone_a_renamed")
    hass.states.async_set("climate.zone_a_renamed", "heat", _climate_attributes(20.5))
    await hass.async_block_till_done()
    assert manager.resolve_entity_id(hk.id) == "climate.zone_a_renamed"
    assert manager.snapshot("mapping_a").current_temperature == 20.5

    hass.states.async_set("climate.zone_a_renamed", "unavailable", {})
    await hass.async_block_till_done()
    fallback = manager.snapshot("mapping_a")
    assert fallback.available
    assert not fallback.homekit_writable
    assert fallback.provenance["current_temperature"] == "ecobee"

    hass.states.async_set("climate.zone_a_renamed", "heat", _climate_attributes(19.5))
    await hass.async_block_till_done()
    recovered = manager.snapshot("mapping_a")
    assert recovered.homekit_writable
    assert recovered.current_temperature == 19.5

    registry.async_remove("climate.zone_a_renamed")
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "mapping_mapping_a")
    assert issue is not None


async def test_standard_and_vendor_commands_have_exactly_one_writer(
    hass: HomeAssistant,
) -> None:
    hk = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec = _register_source(hass, "ecobee", "ec_a")
    preset = _register_sibling(hass, hk, "select", "hk_a_current_mode")
    clear_hold = _register_sibling(hass, hk, "button", "hk_a_clear_hold")
    hass.states.async_set(preset.entity_id, "Home", {"options": ["Home", "Away"]})
    hass.states.async_set(clear_hold.entity_id, "unknown")
    mapping = MappingConfig(
        "mapping_a",
        "Zone A",
        hk.id,
        ec.id,
        homekit_preset_entity=preset.id,
        homekit_clear_hold_entity=clear_hold.id,
    )
    manager = MappingManager(
        hass,
        "entry_a",
        (mapping,),
        {},
    )
    await manager.async_start()
    service_call = AsyncMock()
    with patch.object(hass.services, "async_call", service_call):
        await manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 22.0},
            {"target_temperature": 22.0},
            None,
        )
        service_call.assert_awaited_once_with(
            "climate",
            "set_temperature",
            {"entity_id": hk.entity_id, "temperature": 22.0},
            blocking=True,
            context=None,
        )

        service_call.reset_mock()
        await manager.async_set_preset_mode("mapping_a", "Away", None)
        service_call.assert_awaited_once_with(
            "select",
            "select_option",
            {"entity_id": preset.entity_id, "option": "Away"},
            blocking=True,
            context=None,
        )

        service_call.reset_mock()
        await manager.async_resume_program("mapping_a", None)
        service_call.assert_awaited_once_with(
            "button",
            "press",
            {"entity_id": clear_hold.entity_id},
            blocking=True,
            context=None,
        )
    await manager.async_stop()


async def test_vendor_action_services_target_unified_climate_once(
    hass: HomeAssistant,
) -> None:
    hk = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec = _register_source(hass, "ecobee", "ec_a")
    entry = _entry(hass, (_mapping("mapping_a", "Zone A", hk, ec),))
    calls = []

    async def capture(call) -> None:
        calls.append(call)

    hass.services.async_register("ecobee", "set_occupancy_modes", capture)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    unified = registry.async_get_entity_id("climate", DOMAIN, "mapping_a")
    assert unified is not None
    for service in (
        "create_vacation",
        "delete_vacation",
        "set_occupancy_modes",
        "set_sensors_used_in_climate",
    ):
        assert hass.services.has_service(DOMAIN, service)

    await hass.services.async_call(
        DOMAIN,
        "set_occupancy_modes",
        {"entity_id": unified, "auto_away": True},
        blocking=True,
    )
    assert len(calls) == 1
    assert calls[0].domain == "ecobee"
    assert calls[0].service == "set_occupancy_modes"
    assert dict(calls[0].data) == {
        "entity_id": ec.entity_id,
        "auto_away": True,
    }


async def test_diagnostics_are_bounded_and_identifier_free(
    hass: HomeAssistant,
) -> None:
    hk = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec = _register_source(hass, "ecobee", "ec_a")
    entry = _entry(hass, (_mapping("mapping_a", "Zone A", hk, ec),))
    assert await hass.config_entries.async_setup(entry.entry_id)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    rendered = repr(diagnostics)
    assert diagnostics["entry"]["mapping_count"] == 1
    assert "mapping_1" in rendered
    assert "Zone A" not in rendered
    assert hk.id not in rendered
    assert hk.entity_id not in rendered
    assert ec.id not in rendered
    assert ec.entity_id not in rendered


def _register_source(
    hass: HomeAssistant,
    platform: str,
    unique_id: str,
    *,
    with_device: bool = False,
) -> er.RegistryEntry:
    source_entry = MockConfigEntry(domain=platform)
    source_entry.add_to_hass(hass)
    device_id: str | None = None
    if with_device:
        device_id = (
            dr.async_get(hass)
            .async_get_or_create(
                config_entry_id=source_entry.entry_id,
                identifiers={(platform, f"device_{unique_id}")},
            )
            .id
        )
    entity = er.async_get(hass).async_get_or_create(
        "climate",
        platform,
        unique_id,
        config_entry=source_entry,
        device_id=device_id,
        suggested_object_id=unique_id,
    )
    hass.states.async_set(entity.entity_id, "heat", _climate_attributes(20.0))
    return entity


def _climate_attributes(temperature: float) -> dict[str, object]:
    return {
        "current_temperature": temperature,
        "temperature": 21.0,
        "hvac_action": "heating",
        "hvac_modes": ["off", "heat", "cool", "heat_cool"],
        "fan_mode": "auto",
        "fan_modes": ["auto", "on"],
        "supported_features": 385,
        "min_temp": 7.0,
        "max_temp": 35.0,
        "target_temp_step": 0.5,
        "unit_of_measurement": "°C",
    }


def _register_sibling(
    hass: HomeAssistant,
    source: er.RegistryEntry,
    domain: str,
    unique_id: str,
) -> er.RegistryEntry:
    source_entry = hass.config_entries.async_get_entry(source.config_entry_id)
    assert source_entry is not None
    return er.async_get(hass).async_get_or_create(
        domain,
        source.platform,
        unique_id,
        config_entry=source_entry,
        device_id=source.device_id,
        suggested_object_id=unique_id,
    )


def _mapping(
    mapping_id: str,
    name: str,
    homekit: er.RegistryEntry,
    ecobee: er.RegistryEntry,
) -> MappingConfig:
    return MappingConfig(mapping_id, name, homekit.id, ecobee.id)


def _entry(hass: HomeAssistant, mappings: tuple[MappingConfig, ...]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ecobee Unified",
        unique_id=DOMAIN,
        data={CONF_MAPPINGS: [mapping.as_dict() for mapping in mappings]},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    return entry
