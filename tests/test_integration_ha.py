"""Exact Home Assistant Core integration-contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.climate import ClimateEntity
from homeassistant.core import HomeAssistant, callback
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed_exact,
)

from custom_components.ecobee_unified.climate import EcobeeUnifiedClimate
from custom_components.ecobee_unified.const import (
    CONF_ADD_ANOTHER,
    CONF_ECOBEE_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_MAPPING_ID,
    CONF_MAPPINGS,
    CONF_NAME,
    DOMAIN,
    SIGNAL_SNAPSHOT_UPDATED,
)
from custom_components.ecobee_unified.models import MappingConfig


async def test_config_flow_creates_two_explicit_mappings(
    hass: HomeAssistant,
) -> None:
    hk_a = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec_a = _register_source(hass, "ecobee", "ec_a", with_device=True)
    hk_b = _register_source(hass, "homekit_controller", "hk_b", with_device=True)
    ec_b = _register_source(hass, "ecobee", "ec_b", with_device=True)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "mapping"

    wrong = _register_source(hass, "demo", "wrong")
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
    assert (
        len({mapping[CONF_MAPPING_ID] for mapping in result["data"][CONF_MAPPINGS]})
        == 2
    )
    assert result["data"][CONF_MAPPINGS][0][CONF_HOMEKIT_ENTITY] == hk_a.id
    assert result["data"][CONF_MAPPINGS][1][CONF_ECOBEE_ENTITY] == ec_b.id


async def test_load_links_entities_to_source_devices_and_unloads_cleanly(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    freezer.move_to(now)
    hk_a = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec_a = _register_source(hass, "ecobee", "ec_a", with_device=True)
    hk_b = _register_source(hass, "homekit_controller", "hk_b", with_device=True)
    ec_b = _register_source(hass, "ecobee", "ec_b", with_device=True)
    entry = _entry(
        hass,
        (
            _mapping("mapping_a", "Zone A", hk_a, ec_a),
            _mapping("mapping_b", "Zone B", hk_b, ec_b),
        ),
    )

    added: list[str] = []
    writes: list[str] = []
    native_added = ClimateEntity.async_added_to_hass
    native_write = EcobeeUnifiedClimate.async_write_ha_state

    async def track_added(entity: ClimateEntity) -> None:
        added.append(entity.entity_id)
        await native_added(entity)

    @callback
    def track_write(entity: EcobeeUnifiedClimate) -> None:
        writes.append(entity.entity_id)
        native_write(entity)

    with (
        patch.object(ClimateEntity, "async_added_to_hass", track_added),
        patch.object(EcobeeUnifiedClimate, "async_write_ha_state", track_write),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        unified_a = registry.async_get_entity_id("climate", DOMAIN, "mapping_a")
        unified_b = registry.async_get_entity_id("climate", DOMAIN, "mapping_b")
        assert unified_a is not None
        assert unified_b is not None
        assert registry.async_get(unified_a).device_id == hk_a.device_id
        assert registry.async_get(unified_b).device_id == hk_b.device_id
        assert sorted(added) == sorted([unified_a, unified_b])

        last_reported = {
            entity_id: hass.states.get(entity_id).last_reported
            for entity_id in (unified_a, unified_b)
        }
        quiet_time = now + timedelta(seconds=61)
        freezer.move_to(quiet_time)
        async_fire_time_changed_exact(hass, quiet_time)
        await hass.async_block_till_done()
        assert {
            entity_id: hass.states.get(entity_id).last_reported
            for entity_id in (unified_a, unified_b)
        } == last_reported

        writes.clear()
        async_dispatcher_send(hass, f"{SIGNAL_SNAPSHOT_UPDATED}_mapping_a")
        await hass.async_block_till_done()
        assert writes == [unified_a]
        writes.clear()
        async_dispatcher_send(hass, f"{SIGNAL_SNAPSHOT_UPDATED}_mapping_b")
        await hass.async_block_till_done()
        assert writes == [unified_b]

        source_device = dr.async_get(hass).async_get(hk_a.device_id)
        assert source_device is not None
        assert source_device.config_entry_id != entry.entry_id
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        writes.clear()
        async_dispatcher_send(hass, f"{SIGNAL_SNAPSHOT_UPDATED}_mapping_a")
        async_dispatcher_send(hass, f"{SIGNAL_SNAPSHOT_UPDATED}_mapping_b")
        await hass.async_block_till_done()
        assert writes == []


async def test_rename_loss_fallback_recovery_and_removal_repair(
    hass: HomeAssistant,
) -> None:
    hk = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec = _register_source(hass, "ecobee", "ec_a", with_device=True)
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


@pytest.mark.parametrize("selection", ["explicit_split", "partial_fallback"])
async def test_serialized_climate_withholds_conflicting_range_and_recovers(
    hass: HomeAssistant, selection: str
) -> None:
    sources = {
        zone: (
            _register_source(
                hass, "homekit_controller", f"hk_{zone}", with_device=True
            ),
            _register_source(hass, "ecobee", f"ec_{zone}", with_device=True),
        )
        for zone in ("a", "b")
    }
    local_attributes = _climate_attributes(20.0) | {
        "target_temp_low": 24.0,
        "target_temp_high": 26.0,
        "supported_features": 387,
    }
    if selection == "partial_fallback":
        local_attributes.pop("target_temp_high")
    cloud_attributes = _climate_attributes(20.5) | {
        "target_temp_low": 18.0,
        "target_temp_high": 27.0,
    }
    for hk, ec in sources.values():
        hass.states.async_set(hk.entity_id, "heat_cool", local_attributes)
        hass.states.async_set(ec.entity_id, "heat_cool", cloud_attributes)
    entry = _entry(
        hass,
        tuple(
            _mapping(f"mapping_{zone}", f"Zone {zone.upper()}", hk, ec)
            for zone, (hk, ec) in sources.items()
        ),
    )
    if selection == "explicit_split":
        hass.config_entries.async_update_entry(
            entry,
            options={
                "read_policies": {
                    f"mapping_{zone}": {"target_temperature_high": "ecobee_only"}
                    for zone in sources
                }
            },
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for zone, (_, ec) in sources.items():
        unified = registry.async_get_entity_id("climate", DOMAIN, f"mapping_{zone}")
        assert unified is not None
        initial = hass.states.get(unified)
        assert initial is not None
        assert initial.attributes["target_temp_low"] == 24.0
        assert initial.attributes["target_temp_high"] == 27.0

        hass.states.async_set(
            ec.entity_id,
            "heat_cool",
            cloud_attributes | {"target_temp_high": 20.0},
        )
        await hass.async_block_till_done()
        conflict = hass.states.get(unified)
        assert conflict is not None
        assert conflict.state == "heat_cool"
        assert conflict.attributes["target_temp_low"] is None
        assert conflict.attributes["target_temp_high"] is None
        assert conflict.attributes["current_temperature"] == 20.0
        assert conflict.attributes["temperature"] == 21.0
        assert (
            conflict.attributes["supported_features"]
            == initial.attributes["supported_features"]
        )
        assert (
            "target_temperature_range_conflict"
            in conflict.attributes["problem_reasons"]
        )
        assert (
            conflict.attributes["selected_sources"]["target_temperature_low"]
            == "homekit"
        )
        assert (
            conflict.attributes["selected_sources"]["target_temperature_high"]
            == "ecobee"
        )
        snapshot = entry.runtime_data.manager.snapshot(f"mapping_{zone}")
        assert snapshot.homekit_writable
        assert snapshot.confirmation_values["target_temperature_low"] == 18.0
        assert snapshot.confirmation_values["target_temperature_high"] == 20.0
        other_zone = "b" if zone == "a" else "a"
        other_unified = registry.async_get_entity_id(
            "climate", DOMAIN, f"mapping_{other_zone}"
        )
        assert other_unified is not None
        other_state = hass.states.get(other_unified)
        assert other_state is not None
        assert other_state.attributes["target_temp_low"] == 24.0
        assert other_state.attributes["target_temp_high"] == 27.0

        hass.states.async_set(ec.entity_id, "heat_cool", cloud_attributes)
        await hass.async_block_till_done()
        recovered = hass.states.get(unified)
        assert recovered is not None
        assert recovered.attributes["target_temp_low"] == 24.0
        assert recovered.attributes["target_temp_high"] == 27.0
        assert (
            "target_temperature_range_conflict"
            not in recovered.attributes["problem_reasons"]
        )


async def test_vendor_action_services_target_unified_climate_once(
    hass: HomeAssistant,
) -> None:
    hk = _register_source(hass, "homekit_controller", "hk_a", with_device=True)
    ec = _register_source(hass, "ecobee", "ec_a", with_device=True)
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
        physical_identity = f"thermostat_{unique_id.rsplit('_', 1)[-1]}"
        identifiers = {(platform, f"device_{unique_id}")}
        serial_number = None
        if platform == "homekit_controller":
            serial_number = physical_identity
        elif platform == "ecobee":
            identifiers = {(platform, physical_identity)}
        device_id = (
            dr.async_get(hass)
            .async_get_or_create(
                config_entry_id=source_entry.entry_id,
                identifiers=identifiers,
                serial_number=serial_number,
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
