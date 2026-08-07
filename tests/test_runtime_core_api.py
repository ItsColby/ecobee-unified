"""Core 2026.8 API checks that do not require the Linux pytest plugin."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import voluptuous as vol
from homeassistant import loader
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import SOURCE_USER, ConfigEntries
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified import (
    async_migrate_entry,
    async_remove_entry,
    async_setup_entry,
)
from custom_components.ecobee_unified.climate import (
    EcobeeUnifiedClimate,
)
from custom_components.ecobee_unified.climate import (
    async_setup_entry as async_setup_climate_entry,
)
from custom_components.ecobee_unified.config_flow import (
    _mapping_form_defaults,
    _mapping_from_input,
    _validate_no_duplicate_sources,
)
from custom_components.ecobee_unified.const import (
    CONF_ADD_ANOTHER,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_MAPPINGS,
    CONF_NAME,
    DOMAIN,
    SUFFIX_AIR_QUALITY_INDEX,
)
from custom_components.ecobee_unified.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import CommandStatus, MappingConfig
from custom_components.ecobee_unified.number import EcobeeMinimumFanRuntimeNumber
from custom_components.ecobee_unified.runtime import EcobeeUnifiedRuntime
from custom_components.ecobee_unified.sensor import PROJECTIONS, EcobeeCloudSensor


class RuntimeCoreApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "custom_components",
            Path(self._temp.name) / "custom_components",
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    async def asyncSetUp(self) -> None:
        self.hass = HomeAssistant(self._temp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        loader.async_setup(self.hass)
        dr.async_setup(self.hass)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.homekit = self._source("homekit_controller", "hk_a", device=True)
        self.ecobee = self._source("ecobee", "ec_a", device=True)
        homekit_entry = self.hass.config_entries.async_get_entry(
            self.homekit.config_entry_id
        )
        assert homekit_entry is not None
        registry = er.async_get(self.hass)
        self.homekit_preset = registry.async_get_or_create(
            "select",
            "homekit_controller",
            "hk_a_current_mode",
            config_entry=homekit_entry,
            device_id=self.homekit.device_id,
            suggested_object_id="hk_a_current_mode",
        )
        self.homekit_clear_hold = registry.async_get_or_create(
            "button",
            "homekit_controller",
            "hk_a_clear_hold",
            config_entry=homekit_entry,
            device_id=self.homekit.device_id,
            suggested_object_id="hk_a_clear_hold",
        )
        self.hass.states.async_set(self.homekit_clear_hold.entity_id, "unknown")
        self.hass.states.async_set(
            self.homekit_preset.entity_id, "Home", {"options": ["Home", "Away"]}
        )
        ecobee_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert ecobee_entry is not None
        self.ecobee_aqi = registry.async_get_or_create(
            "sensor",
            "ecobee",
            "ec_a_air_quality_index",
            config_entry=ecobee_entry,
            device_id=self.ecobee.device_id,
            suggested_object_id="ec_a_air_quality_index",
        )
        self.hass.states.async_set(self.ecobee_aqi.entity_id, "42")
        self.mapping = MappingConfig(
            "mapping_a",
            "Zone A",
            self.homekit.id,
            self.ecobee.id,
            self.homekit_preset.id,
            self.homekit_clear_hold.id,
            self.ecobee_aqi.id,
        )
        self.manager = MappingManager(self.hass, "entry_a", (self.mapping,), {})
        await self.manager.async_start()

    async def asyncTearDown(self) -> None:
        await self.manager.async_stop()
        await self.hass.async_stop(force=True)

    async def test_current_helper_device_linking_api(self) -> None:
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)
        self.assertIsNotNone(entity.device_entry)
        self.assertEqual(self.homekit.device_id, entity.device_entry.id)
        self.assertIsNone(entity.device_info)
        self.assertEqual(
            frozenset(
                {
                    "active_comfort_sensors",
                    "command_confirmation",
                    "source_age_seconds",
                }
            ),
            entity._unrecorded_attributes,
        )

    async def test_climate_platform_registers_bounded_unified_actions(self) -> None:
        registrations: list[tuple[str, object, str]] = []
        platform = Mock()
        platform.async_register_entity_service.side_effect = (
            lambda service, schema, method: registrations.append(
                (service, schema, method)
            )
        )
        entities: list[EcobeeUnifiedClimate] = []
        entry = SimpleNamespace(runtime_data=EcobeeUnifiedRuntime(manager=self.manager))

        with patch(
            "custom_components.ecobee_unified.climate.entity_platform.async_get_current_platform",
            return_value=platform,
        ):
            await async_setup_climate_entry(self.hass, entry, entities.extend)

        self.assertEqual(1, len(entities))
        self.assertEqual(
            [
                ("resume_program", "async_resume_program"),
                ("create_vacation", "async_create_vacation"),
                ("delete_vacation", "async_delete_vacation"),
                ("set_occupancy_modes", "async_set_occupancy_modes"),
                (
                    "set_sensors_used_in_climate",
                    "async_set_sensors_used_in_climate",
                ),
            ],
            [(service, method) for service, _schema, method in registrations],
        )

    async def test_entity_properties_only_project_the_normalized_snapshot(self) -> None:
        attributes = self._attributes(20.0) | {
            "current_humidity": 42,
            "humidity": 36,
            "min_humidity": 20,
            "max_humidity": 50,
            "target_temp_low": 18.0,
            "target_temp_high": 24.0,
            "supported_features": 399,
        }
        attributes.pop("target_temp_step")
        self.hass.states.async_set(self.homekit.entity_id, "heat_cool", attributes)
        await self.hass.async_block_till_done()
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)
        with patch.object(
            self.manager,
            "_raw_source",
            side_effect=AssertionError("property performed source I/O"),
        ):
            self.assertTrue(entity.available)
            self.assertIs(HVACMode.HEAT_COOL, entity.hvac_mode)
            self.assertIn(HVACMode.HEAT_COOL, entity.hvac_modes)
            self.assertEqual(20.0, entity.current_temperature)
            self.assertEqual(42.0, entity.current_humidity)
            self.assertEqual(36.0, entity.target_humidity)
            self.assertEqual(20.0, entity.min_humidity)
            self.assertEqual(50.0, entity.max_humidity)
            self.assertEqual(21.0, entity.target_temperature)
            self.assertEqual(18.0, entity.target_temperature_low)
            self.assertEqual(24.0, entity.target_temperature_high)
            self.assertEqual("auto", entity.fan_mode)
            self.assertEqual(["auto", "on"], entity.fan_modes)
            self.assertEqual(7.0, entity.min_temp)
            self.assertEqual(35.0, entity.max_temp)
            self.assertEqual(0.5, entity.target_temperature_step)
            self.assertEqual(
                "ecobee_same_device_fusion",
                self.manager.snapshot("mapping_a").provenance[
                    "target_temperature_step"
                ],
            )
            self.assertEqual("°C", entity.temperature_unit)
            self.assertEqual(415, int(entity.supported_features))
            self.assertIn("source_health", entity.extra_state_attributes)
            for first_class_key in (
                "scheduled_profile",
                "next_transition",
                "equipment_running",
                "minimum_fan_runtime",
            ):
                self.assertNotIn(first_class_key, entity.extra_state_attributes)
            aqi = EcobeeCloudSensor(
                self.manager,
                self.mapping,
                PROJECTIONS[SUFFIX_AIR_QUALITY_INDEX],
            )
            fan_runtime = EcobeeMinimumFanRuntimeNumber(self.manager, self.mapping)
            self.assertTrue(aqi.available)
            self.assertEqual(42.0, aqi.native_value)
            self.assertTrue(fan_runtime.available)
            self.assertEqual(15, fan_runtime.native_value)

        self.hass.states.async_set(self.ecobee.entity_id, "unavailable")
        await self.hass.async_block_till_done()
        self.assertTrue(aqi.available)
        self.assertEqual(42.0, aqi.native_value)
        self.assertFalse(fan_runtime.available)

    async def test_full_config_entry_setup_and_unload(self) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "climate", DOMAIN, self.mapping.mapping_id
        )
        self.assertIsNotNone(entity_id)
        self.assertEqual(
            self.homekit.device_id,
            er.async_get(self.hass).async_get(entity_id).device_id,
        )
        self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(
            entity_id,
            er.async_get(self.hass).async_get_entity_id(
                "climate", DOMAIN, self.mapping.mapping_id
            ),
        )

    async def test_setup_failure_and_entry_removal_release_owned_state(self) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        with (
            patch.object(
                self.hass.config_entries,
                "async_forward_entry_setups",
                side_effect=RuntimeError("platform setup failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "platform setup failed"),
        ):
            await async_setup_entry(self.hass, entry)
        failed_manager = entry.runtime_data.manager
        self.assertIsNone(failed_manager._unsub_state)
        self.assertIsNone(failed_manager._unsub_state_report)
        self.assertIsNone(failed_manager._unsub_registry)
        self.assertIsNone(failed_manager._unsub_device_registry)
        self.assertEqual({}, failed_manager._unsub_stale_refreshes)

        er.async_get(self.hass).async_remove(self.homekit.entity_id)
        await self.hass.async_block_till_done()
        self.assertIsNotNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )
        await async_remove_entry(self.hass, entry)
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )

    async def test_native_config_flow_creates_multiple_mappings(self) -> None:
        homekit_b = self._source("homekit_controller", "hk_b", device=True)
        ecobee_b = self._source("ecobee", "ec_b")
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        self.assertIs(FlowResultType.FORM, result["type"])
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Zone A",
                CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
                CONF_ADD_ANOTHER: True,
            },
        )
        self.assertIs(FlowResultType.FORM, result["type"])
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Zone B",
                CONF_HOMEKIT_ENTITY: homekit_b.entity_id,
                CONF_ECOBEE_ENTITY: ecobee_b.entity_id,
                CONF_ADD_ANOTHER: False,
            },
        )
        self.assertIs(FlowResultType.CREATE_ENTRY, result["type"])
        self.assertEqual(2, len(result["data"][CONF_MAPPINGS]))
        self.assertEqual(
            self.homekit.id,
            result["data"][CONF_MAPPINGS][0][CONF_HOMEKIT_ENTITY],
        )

    async def test_config_flow_rejects_duplicate_mapping_and_second_entry(self) -> None:
        ecobee_b = self._source("ecobee", "ec_b")
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Zone A",
                CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
                CONF_ADD_ANOTHER: True,
            },
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Zone B",
                CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                CONF_ECOBEE_ENTITY: ecobee_b.entity_id,
                CONF_ADD_ANOTHER: False,
            },
        )
        self.assertIs(FlowResultType.FORM, result["type"])
        self.assertEqual("duplicate_homekit_source", result["errors"]["base"])
        self.hass.config_entries.flow.async_abort(result["flow_id"])

        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        duplicate_entry = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        self.assertIs(FlowResultType.ABORT, duplicate_entry["type"])
        self.assertEqual("already_configured", duplicate_entry["reason"])

    async def test_mapping_validation_rejects_wrong_domains_and_reused_sources(
        self,
    ) -> None:
        ecobee_sensor = self._source("ecobee", "ec_sensor", domain="sensor")
        with self.assertRaisesRegex(vol.Invalid, "invalid_ecobee_source"):
            _mapping_from_input(
                self.hass,
                {
                    CONF_NAME: "Zone A",
                    CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                    CONF_ECOBEE_ENTITY: ecobee_sensor.entity_id,
                },
            )

        wrong_device_sensor = self._source(
            "ecobee", "other_aqi", domain="sensor", device=True
        )
        with self.assertRaisesRegex(vol.Invalid, "invalid_ecobee_source"):
            _mapping_from_input(
                self.hass,
                {
                    CONF_NAME: "Zone A",
                    CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                    CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
                    CONF_ECOBEE_AQI_ENTITY: wrong_device_sensor.entity_id,
                },
            )

        candidate = self.mapping.as_dict()
        homekit_b = self._source("homekit_controller", "hk_b", device=True)
        ecobee_b = self._source("ecobee", "ec_b")
        duplicate_context = MappingConfig(
            "mapping_b",
            "Zone B",
            homekit_b.id,
            ecobee_b.id,
            homekit_preset_entity=self.homekit_preset.id,
        ).as_dict()
        with self.assertRaisesRegex(vol.Invalid, "duplicate_optional_source"):
            _validate_no_duplicate_sources([candidate], duplicate_context)

    async def test_reconfigure_edits_and_removes_with_confirmation(self) -> None:
        homekit_b = self._source("homekit_controller", "hk_b", device=True)
        ecobee_b = self._source("ecobee", "ec_b")
        mapping_b = MappingConfig("mapping_b", "Zone B", homekit_b.id, ecobee_b.id)
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict(), mapping_b.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        self.assertIs(FlowResultType.MENU, result["type"])
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_edit"}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"mapping_id": self.mapping.mapping_id}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Zone A updated",
                CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
                CONF_ECOBEE_AQI_ENTITY: self.ecobee_aqi.entity_id,
                CONF_HOMEKIT_PRESET_ENTITY: self.homekit_preset.entity_id,
                CONF_HOMEKIT_CLEAR_HOLD_ENTITY: self.homekit_clear_hold.entity_id,
                "confirm_change": False,
            },
        )
        self.assertIs(FlowResultType.MENU, result["type"])
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_remove"}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"mapping_id": mapping_b.mapping_id}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"confirm_change": True}
        )
        self.assertIs(FlowResultType.MENU, result["type"])
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_finish"}
        )
        self.assertIs(FlowResultType.ABORT, result["type"])
        self.assertEqual("reconfigure_successful", result["reason"])
        self.assertEqual(1, len(entry.data[CONF_MAPPINGS]))
        self.assertEqual("Zone A updated", entry.data[CONF_MAPPINGS][0][CONF_NAME])

    async def test_reconfigure_requires_confirmation_only_for_writer_change(
        self,
    ) -> None:
        replacement_homekit = self._source(
            "homekit_controller", "hk_replacement", device=True
        )
        replacement_ecobee = self._source("ecobee", "ec_replacement")
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_edit"}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"mapping_id": self.mapping.mapping_id}
        )
        replacement = {
            CONF_NAME: "Zone A",
            CONF_HOMEKIT_ENTITY: replacement_homekit.entity_id,
            CONF_ECOBEE_ENTITY: replacement_ecobee.entity_id,
            "confirm_change": False,
        }
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], replacement
        )
        self.assertIs(FlowResultType.FORM, result["type"])
        self.assertEqual("confirmation_required", result["errors"]["base"])

        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], replacement | {"confirm_change": True}
        )
        self.assertIs(FlowResultType.MENU, result["type"])
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_finish"}
        )
        self.assertIs(FlowResultType.ABORT, result["type"])
        updated = entry.data[CONF_MAPPINGS][0]
        self.assertEqual(replacement_homekit.id, updated[CONF_HOMEKIT_ENTITY])
        self.assertEqual(replacement_ecobee.id, updated[CONF_ECOBEE_ENTITY])

    async def test_options_flow_updates_only_timing_policy(self) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        result = await self.hass.config_entries.options.async_init(entry.entry_id)
        self.assertIs(FlowResultType.FORM, result["type"])
        options = {
            CONF_ECOBEE_STALE_SECONDS: 1200,
            CONF_CONFIRMATION_SECONDS: 720,
        }
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"], options
        )
        self.assertIs(FlowResultType.CREATE_ENTRY, result["type"])
        self.assertEqual(options, entry.options)
        self.assertEqual([self.mapping.as_dict()], entry.data[CONF_MAPPINGS])

    async def test_minor_schema_migration_normalizes_mapping_data(self) -> None:
        legacy = self.mapping.as_dict() | {
            "scheduled_profile_entity": "",
            "next_transition_entity": "",
        }
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [legacy]},
            version=1,
            minor_version=0,
            options={"beestat_stale_seconds": 24_000},
        )
        entry.add_to_hass(self.hass)
        self.assertTrue(await async_migrate_entry(self.hass, entry))
        self.assertEqual(2, entry.minor_version)
        self.assertEqual([self.mapping.as_dict()], entry.data[CONF_MAPPINGS])
        self.assertEqual({}, entry.options)

    async def test_future_schema_fails_closed_without_rewriting_data(self) -> None:
        original_data = {CONF_MAPPINGS: [self.mapping.as_dict()]}
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data=original_data,
            version=2,
            minor_version=0,
        )
        entry.add_to_hass(self.hass)
        self.assertFalse(await async_migrate_entry(self.hass, entry))
        self.assertEqual(2, entry.version)
        self.assertEqual(original_data, entry.data)

        empty_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: []},
            version=1,
            minor_version=0,
        )
        empty_entry.add_to_hass(self.hass)
        self.assertFalse(await async_migrate_entry(self.hass, empty_entry))

    async def test_registry_rename_and_capability_recovery(self) -> None:
        registry = er.async_get(self.hass)
        registry.async_update_entity(
            self.homekit.entity_id, new_entity_id="climate.zone_a_renamed"
        )
        self.hass.states.async_set(
            "climate.zone_a_renamed", "heat", self._attributes(20.5)
        )
        await self.hass.async_block_till_done()
        self.assertEqual(
            "climate.zone_a_renamed",
            self.manager.resolve_entity_id(self.homekit.id),
        )
        self.assertEqual(20.5, self.manager.snapshot("mapping_a").current_temperature)

        self.hass.states.async_set("climate.zone_a_renamed", "unavailable", {})
        await self.hass.async_block_till_done()
        fallback = self.manager.snapshot("mapping_a")
        self.assertTrue(fallback.available)
        self.assertFalse(fallback.homekit_writable)
        self.assertEqual("ecobee", fallback.provenance["current_temperature"])

        self.hass.states.async_set(
            "climate.zone_a_renamed", "heat", self._attributes(19.5)
        )
        await self.hass.async_block_till_done()
        recovered = self.manager.snapshot("mapping_a")
        self.assertTrue(recovered.homekit_writable)
        self.assertEqual(19.5, recovered.current_temperature)

    async def test_registry_disable_and_late_source_state_recover_without_reload(
        self,
    ) -> None:
        registry = er.async_get(self.hass)
        registry.async_update_entity(
            self.homekit.entity_id,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        await self.hass.async_block_till_done()
        disabled = self.manager.snapshot("mapping_a")
        self.assertFalse(disabled.homekit_writable)
        self.assertEqual("unavailable", disabled.source_health["homekit"].value)
        self.assertEqual("ecobee", disabled.provenance["current_temperature"])

        registry.async_update_entity(self.homekit.entity_id, disabled_by=None)
        await self.hass.async_block_till_done()
        self.assertTrue(self.manager.snapshot("mapping_a").homekit_writable)

        self.hass.states.async_remove(self.homekit.entity_id)
        await self.hass.async_block_till_done()
        missing_state = self.manager.snapshot("mapping_a")
        self.assertFalse(missing_state.homekit_writable)
        self.assertEqual("ecobee", missing_state.provenance["hvac_mode"])

        self.hass.states.async_set(
            self.homekit.entity_id, "heat", self._attributes(18.5)
        )
        await self.hass.async_block_till_done()
        recovered = self.manager.snapshot("mapping_a")
        self.assertTrue(recovered.homekit_writable)
        self.assertEqual(18.5, recovered.current_temperature)

    async def test_target_humidity_degrades_on_actual_unavailable_and_recovers(
        self,
    ) -> None:
        attributes = self._attributes(20.0) | {
            "supported_features": 389,
            "humidity": 36,
            "min_humidity": 20,
            "max_humidity": 50,
        }
        attributes.pop("target_temp_step")
        self.hass.states.async_set(self.homekit.entity_id, "heat", attributes)
        await self.hass.async_block_till_done()
        self.assertTrue(
            self.manager.snapshot("mapping_a").supported_features
            & int(ClimateEntityFeature.TARGET_HUMIDITY)
        )

        self.hass.states.async_set(self.homekit.entity_id, "unavailable", {})
        await self.hass.async_block_till_done()
        degraded = self.manager.snapshot("mapping_a")
        self.assertFalse(degraded.homekit_writable)
        self.assertFalse(
            degraded.supported_features & int(ClimateEntityFeature.TARGET_HUMIDITY)
        )

        self.hass.states.async_set(self.homekit.entity_id, "heat", attributes)
        await self.hass.async_block_till_done()
        recovered = self.manager.snapshot("mapping_a")
        self.assertTrue(recovered.homekit_writable)
        self.assertTrue(
            recovered.supported_features & int(ClimateEntityFeature.TARGET_HUMIDITY)
        )

    async def test_preset_source_renames_degrades_and_recovers_independently(
        self,
    ) -> None:
        snapshot = self.manager.snapshot(self.mapping.mapping_id)
        self.assertEqual("Home", snapshot.preset_mode)
        self.assertEqual(("Home", "Away"), snapshot.preset_modes)

        registry = er.async_get(self.hass)
        registry.async_update_entity(
            self.homekit_preset.entity_id,
            new_entity_id="select.hk_a_current_mode_renamed",
        )
        self.hass.states.async_set(
            "select.hk_a_current_mode_renamed",
            "Away",
            {"options": ["Home", "Away"]},
        )
        await self.hass.async_block_till_done()
        self.assertEqual("Away", self.manager.snapshot("mapping_a").preset_mode)

        self.hass.states.async_set("select.hk_a_current_mode_renamed", "unavailable")
        await self.hass.async_block_till_done()
        degraded = self.manager.snapshot("mapping_a")
        self.assertIsNone(degraded.preset_mode)
        self.assertFalse(degraded.homekit_preset_writable)
        self.assertIn("homekit_preset_unavailable", degraded.degradation)

        self.hass.states.async_set(
            "select.hk_a_current_mode_renamed",
            "Home",
            {"options": ["Home", "Away"]},
        )
        await self.hass.async_block_till_done()
        self.assertTrue(self.manager.snapshot("mapping_a").homekit_preset_writable)

    async def test_preset_control_survives_ecobee_read_fallback(self) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("select", "select_option", capture)
        self.hass.states.async_set(self.homekit.entity_id, "unavailable")
        await self.hass.async_block_till_done()

        snapshot = self.manager.snapshot("mapping_a")
        self.assertTrue(snapshot.available)
        self.assertFalse(snapshot.homekit_writable)
        self.assertTrue(snapshot.homekit_preset_writable)
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)
        self.assertEqual(
            ClimateEntityFeature.PRESET_MODE,
            entity.supported_features,
        )
        await entity.async_set_preset_mode("Away")
        self.assertEqual(1, len(calls))
        self.assertEqual("select", calls[0].domain)
        self.assertEqual(self.homekit_preset.entity_id, calls[0].data["entity_id"])

    async def test_exact_single_writer_service_calls(self) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_temperature", capture)
        self.hass.services.async_register("ecobee", "set_fan_min_on_time", capture)
        self.hass.services.async_register("button", "press", capture)
        self.hass.services.async_register("select", "select_option", capture)
        await self.manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 22.0},
            {"target_temperature": 22.0},
            None,
        )
        self.assertEqual(1, len(calls))
        self.assertEqual("climate", calls[0].domain)
        self.assertEqual("set_temperature", calls[0].service)
        self.assertEqual(
            {"entity_id": self.homekit.entity_id, "temperature": 22.0},
            dict(calls[0].data),
        )

        calls.clear()
        await self.manager.async_set_preset_mode("mapping_a", "Away", None)
        self.assertEqual(1, len(calls))
        self.assertEqual("select", calls[0].domain)
        self.assertEqual("select_option", calls[0].service)
        self.assertEqual(
            {"entity_id": self.homekit_preset.entity_id, "option": "Away"},
            dict(calls[0].data),
        )

        calls.clear()
        await self.manager.async_set_minimum_fan_runtime("mapping_a", 15, None)
        self.assertEqual(1, len(calls))
        self.assertEqual("ecobee", calls[0].domain)
        self.assertEqual("set_fan_min_on_time", calls[0].service)
        self.assertEqual(
            {"entity_id": self.ecobee.entity_id, "fan_min_on_time": 15},
            dict(calls[0].data),
        )

        calls.clear()
        number = EcobeeMinimumFanRuntimeNumber(self.manager, self.mapping)
        await number.async_set_native_value(7.5)
        self.assertEqual(1, len(calls))
        self.assertEqual(
            {"entity_id": self.ecobee.entity_id, "fan_min_on_time": 10},
            dict(calls[0].data),
        )

        calls.clear()
        await self.manager.async_resume_program("mapping_a", None)
        self.assertEqual(1, len(calls))
        self.assertEqual("button", calls[0].domain)
        self.assertEqual("press", calls[0].service)
        self.assertEqual(
            {"entity_id": self.homekit_clear_hold.entity_id},
            dict(calls[0].data),
        )

    async def test_vendor_action_facade_routes_exactly_one_mapped_call(self) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        for service in (
            "create_vacation",
            "delete_vacation",
            "set_occupancy_modes",
            "set_sensors_used_in_climate",
        ):
            self.hass.services.async_register("ecobee", service, capture)
        ecobee_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert ecobee_entry is not None
        sensor_device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=ecobee_entry.entry_id,
            identifiers={("ecobee", "sensor_a")},
        )
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)
        entity.hass = self.hass

        await entity.async_create_vacation(
            "Trip",
            82.0,
            58.0,
            "2026-09-01",
            "08:00:00",
            "2026-09-05",
            "18:00:00",
            "auto",
            5,
        )
        self.assertEqual(1, len(calls))
        self.assertEqual("create_vacation", calls[0].service)
        self.assertEqual(
            {
                "entity_id": self.ecobee.entity_id,
                "vacation_name": "Trip",
                "cool_temp": 82.0,
                "heat_temp": 58.0,
                "start_date": "2026-09-01",
                "start_time": "08:00:00",
                "end_date": "2026-09-05",
                "end_time": "18:00:00",
                "fan_mode": "auto",
                "fan_min_on_time": 5,
            },
            dict(calls[0].data),
        )
        self.assertEqual(
            CommandStatus.SUBMITTED,
            self.manager.snapshot("mapping_a").command.status,
        )

        calls.clear()
        await entity.async_delete_vacation("Trip")
        self.assertEqual(1, len(calls))
        self.assertEqual("delete_vacation", calls[0].service)
        self.assertEqual(
            {"entity_id": self.ecobee.entity_id, "vacation_name": "Trip"},
            dict(calls[0].data),
        )

        calls.clear()
        await entity.async_set_occupancy_modes(auto_away=True, follow_me=False)
        self.assertEqual(1, len(calls))
        self.assertEqual("set_occupancy_modes", calls[0].service)
        self.assertEqual(
            {
                "entity_id": self.ecobee.entity_id,
                "auto_away": True,
                "follow_me": False,
            },
            dict(calls[0].data),
        )

        calls.clear()
        await entity.async_set_sensors_used_in_climate(
            [sensor_device.id], preset_mode="Home"
        )
        self.assertEqual(1, len(calls))
        self.assertEqual("set_sensors_used_in_climate", calls[0].service)
        self.assertEqual(
            {
                "entity_id": self.ecobee.entity_id,
                "device_ids": [sensor_device.id],
                "preset_mode": "Home",
            },
            dict(calls[0].data),
        )

    async def test_vendor_action_facade_rejects_invalid_or_unprovable_inputs(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        for service in (
            "create_vacation",
            "delete_vacation",
            "set_occupancy_modes",
            "set_sensors_used_in_climate",
        ):
            self.hass.services.async_register("ecobee", service, capture)
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)
        entity.hass = self.hass
        assert self.ecobee.device_id is not None

        invalid_calls = (
            entity.async_create_vacation("", 82.0, 58.0),
            entity.async_create_vacation("Trip", float("nan"), 58.0),
            entity.async_create_vacation("Trip", 58.0, 82.0),
            entity.async_create_vacation(
                "Trip",
                82.0,
                58.0,
                "2026-09-05",
                "18:00:00",
                "2026-09-01",
                "08:00:00",
            ),
            entity.async_create_vacation("Trip", 82.0, 58.0, "2026-02-30", "08:00:00"),
            entity.async_set_occupancy_modes(),
            entity.async_set_sensors_used_in_climate(["unknown_device"]),
            entity.async_set_sensors_used_in_climate(
                [self.ecobee.device_id, self.ecobee.device_id]
            ),
            entity.async_set_sensors_used_in_climate(
                [self.ecobee.device_id], preset_mode=" "
            ),
        )
        for call in invalid_calls:
            with self.assertRaises(ServiceValidationError):
                await call
        self.assertEqual([], calls)

    async def test_vendor_action_requires_registered_service_and_healthy_source(
        self,
    ) -> None:
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_vendor_action(
                "mapping_a", "create_vacation", {"vacation_name": "Trip"}, None
            )

        self.hass.services.async_register("ecobee", "create_vacation", lambda _: None)
        self.hass.states.async_set(self.ecobee.entity_id, "unavailable", {})
        await self.hass.async_block_till_done()
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_vendor_action(
                "mapping_a", "create_vacation", {"vacation_name": "Trip"}, None
            )

    async def test_vendor_action_timeout_owns_late_completion(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        self.manager._tracker.begin(
            "mapping_a", "set_temperature", {"target_temperature": 23.0}
        )
        self.manager._subscribe_state_reports()
        self.assertIsNotNone(self.manager._unsub_state_report)

        async def delayed(_call: ServiceCall) -> None:
            started.set()
            await release.wait()

        self.hass.services.async_register("ecobee", "delete_vacation", delayed)
        task = asyncio.create_task(
            self.manager.async_vendor_action(
                "mapping_a", "delete_vacation", {"vacation_name": "Trip"}, None
            )
        )
        await started.wait()
        self.assertIsNone(self.manager._unsub_state_report)
        revision = self.manager._tracker.current_revision("mapping_a")
        assert revision is not None

        self.manager._handle_timeout("mapping_a", revision)
        self.assertEqual(
            CommandStatus.UNCONFIRMED,
            self.manager.snapshot("mapping_a").command.status,
        )

        release.set()
        await task
        self.assertEqual(
            CommandStatus.UNCONFIRMED,
            self.manager.snapshot("mapping_a").command.status,
        )

    async def test_vendor_action_failure_is_bounded(self) -> None:
        calls = 0

        async def fail(_call: ServiceCall) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("arbitrary backend body")

        self.hass.services.async_register("ecobee", "create_vacation", fail)
        with self.assertRaises(HomeAssistantError) as raised:
            await self.manager.async_vendor_action(
                "mapping_a", "create_vacation", {"vacation_name": "Trip"}, None
            )

        self.assertEqual(1, calls)
        self.assertEqual("ecobee_command_failed", raised.exception.translation_key)
        self.assertNotIn("backend", str(raised.exception))
        self.assertEqual(
            CommandStatus.FAILED,
            self.manager.snapshot("mapping_a").command.status,
        )

    async def test_unavailable_writers_and_invalid_vendor_bounds_fail_before_effect(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_temperature", capture)
        self.hass.services.async_register("button", "press", capture)
        self.hass.states.async_set(self.homekit.entity_id, "unavailable", {})
        await self.hass.async_block_till_done()
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_standard_command(
                "mapping_a",
                "set_temperature",
                {"temperature": 22.0},
                {"target_temperature": 22.0},
                None,
            )

        self.hass.states.async_set(self.homekit_preset.entity_id, "unavailable", {})
        await self.hass.async_block_till_done()
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_resume_program("mapping_a", None)
        self.hass.states.async_set(self.ecobee.entity_id, "unavailable", {})
        await self.hass.async_block_till_done()
        for invalid in (-1, 61):
            with (
                self.subTest(minutes=invalid),
                self.assertRaises(ServiceValidationError),
            ):
                await self.manager.async_set_minimum_fan_runtime(
                    "mapping_a", invalid, None
                )
        self.assertEqual([], calls)

    async def test_optional_sibling_device_drift_degrades_and_recovers(self) -> None:
        registry = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        wrong_homekit_device = devices.async_get_or_create(
            config_entry_id=self.homekit.config_entry_id,
            identifiers={("homekit_controller", "wrong_homekit_device")},
        )
        wrong_ecobee_device = devices.async_get_or_create(
            config_entry_id=self.ecobee.config_entry_id,
            identifiers={("ecobee", "wrong_ecobee_device")},
        )

        registry.async_update_entity(
            self.homekit_preset.entity_id, device_id=wrong_homekit_device.id
        )
        registry.async_update_entity(
            self.homekit_clear_hold.entity_id, device_id=wrong_homekit_device.id
        )
        registry.async_update_entity(
            self.ecobee_aqi.entity_id, device_id=wrong_ecobee_device.id
        )
        await self.hass.async_block_till_done()

        degraded = self.manager.snapshot("mapping_a")
        self.assertTrue(degraded.available)
        self.assertTrue(degraded.homekit_writable)
        self.assertFalse(degraded.homekit_preset_writable)
        self.assertFalse(degraded.homekit_clear_hold_writable)
        self.assertIsNone(degraded.air_quality_index)
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        self.assertIsNotNone(issue)

        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("select", "select_option", capture)
        self.hass.services.async_register("button", "press", capture)
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_set_preset_mode("mapping_a", "Away", None)
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_resume_program("mapping_a", None)
        self.assertEqual([], calls)

        registry.async_update_entity(
            self.homekit_preset.entity_id, device_id=self.homekit.device_id
        )
        registry.async_update_entity(
            self.homekit_clear_hold.entity_id, device_id=self.homekit.device_id
        )
        registry.async_update_entity(
            self.ecobee_aqi.entity_id, device_id=self.ecobee.device_id
        )
        await self.hass.async_block_till_done()

        recovered = self.manager.snapshot("mapping_a")
        self.assertTrue(recovered.homekit_preset_writable)
        self.assertTrue(recovered.homekit_clear_hold_writable)
        self.assertEqual(42.0, recovered.air_quality_index)
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )

    async def test_command_timeout_reports_unconfirmed_without_retry(self) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_temperature", capture)
        await self.manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 22.0},
            {"target_temperature": 22.0},
            None,
        )
        revision = self.manager.snapshot("mapping_a").command.revision
        self.manager._handle_timeout("mapping_a", revision)
        timed_out = self.manager.snapshot("mapping_a").command
        self.assertEqual("unconfirmed", timed_out.status.value)
        self.assertEqual(1, len(calls))
        self.assertNotIn("mapping_a", self.manager._unsub_timeouts)

    async def test_unchanged_report_subscription_exists_only_while_pending(
        self,
    ) -> None:
        self.assertIsNone(self.manager._unsub_state_report)

        async def capture(_call: ServiceCall) -> None:
            return None

        self.hass.services.async_register("climate", "set_temperature", capture)
        await self.manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 22.0},
            {"target_temperature": 22.0},
            None,
        )
        self.assertIsNotNone(self.manager._unsub_state_report)

        revision = self.manager.snapshot("mapping_a").command.revision
        self.manager._handle_timeout("mapping_a", revision)
        self.assertIsNone(self.manager._unsub_state_report)

    async def test_report_handler_uses_stable_event_timestamp(self) -> None:
        state = self.hass.states.get(self.ecobee.entity_id)
        self.assertIsNotNone(state)
        assert state is not None
        reported_at = state.last_reported
        state.last_reported = reported_at + timedelta(seconds=600)
        self.manager._tracker.begin(
            "mapping_a", "set_temperature", {"target_temperature": 22.0}
        )
        event = Mock(
            data={
                "entity_id": self.ecobee.entity_id,
                "last_reported": reported_at,
            }
        )

        with patch(
            "custom_components.ecobee_unified.manager.dt_util.utcnow",
            return_value=reported_at + timedelta(seconds=10),
        ):
            self.manager._handle_state_report_event(event)

        self.assertEqual(10, self.manager.snapshot("mapping_a").source_ages["ecobee"])
        self.assertEqual(
            "pending", self.manager.snapshot("mapping_a").command.status.value
        )

    async def test_matching_unchanged_ecobee_report_confirms_pending_command(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_temperature", capture)
        await self.manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 21.0},
            {"target_temperature": 21.0},
            None,
        )
        self.assertEqual(
            "pending", self.manager.snapshot("mapping_a").command.status.value
        )

        self.hass.states.async_set(
            self.ecobee.entity_id, "heat", self._attributes(20.0)
        )
        await self.hass.async_block_till_done()

        self.assertEqual(
            "confirmed", self.manager.snapshot("mapping_a").command.status.value
        )
        self.assertEqual(1, len(calls))
        self.assertNotIn("mapping_a", self.manager._unsub_timeouts)

    async def test_confirmation_ignores_reports_from_the_wrong_writer_source(
        self,
    ) -> None:
        self.manager._tracker.begin(
            "mapping_a", "set_temperature", {"target_temperature": 21.0}
        )
        self.hass.states.async_set(
            self.homekit_preset.entity_id,
            "Away",
            {"options": ["Home", "Away"]},
        )
        await self.hass.async_block_till_done()
        self.assertEqual(
            "pending", self.manager.snapshot("mapping_a").command.status.value
        )

        self.manager._tracker.begin(
            "mapping_a", "set_preset_mode", {"preset_mode": "Away"}
        )
        ecobee_state = self.hass.states.get(self.ecobee.entity_id)
        assert ecobee_state is not None
        self.manager._handle_state_report_event(
            Mock(
                data={
                    "entity_id": self.ecobee.entity_id,
                    "last_reported": ecobee_state.last_reported,
                }
            )
        )
        self.assertEqual(
            "pending", self.manager.snapshot("mapping_a").command.status.value
        )

    async def test_all_climate_methods_validate_capability_and_use_one_writer(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        for service in (
            "set_hvac_mode",
            "set_temperature",
            "set_humidity",
            "set_fan_mode",
            "turn_off",
            "turn_on",
        ):
            self.hass.services.async_register("climate", service, capture)
        attributes = self._attributes(20.0) | {
            "supported_features": 399,
            "humidity": 36,
            "min_humidity": 20,
            "max_humidity": 50,
        }
        self.hass.states.async_set(self.homekit.entity_id, "heat", attributes)
        await self.hass.async_block_till_done()
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)

        await entity.async_set_hvac_mode(HVACMode.COOL)
        await entity.async_set_temperature(temperature=22.0)
        await entity.async_set_temperature(target_temp_low=19.0, target_temp_high=24.0)
        await entity.async_set_humidity(40)
        await entity.async_set_fan_mode("auto")
        await entity.async_turn_off()
        await entity.async_turn_on()

        self.assertEqual(
            [
                "set_hvac_mode",
                "set_temperature",
                "set_temperature",
                "set_humidity",
                "set_fan_mode",
                "turn_off",
                "turn_on",
            ],
            [call.service for call in calls],
        )
        self.assertTrue(all(call.domain == "climate" for call in calls))
        self.assertTrue(
            all(call.data["entity_id"] == self.homekit.entity_id for call in calls)
        )

        call_count = len(calls)
        with self.assertRaises(ServiceValidationError):
            await entity.async_set_hvac_mode(HVACMode.DRY)
        with self.assertRaises(ServiceValidationError):
            await entity.async_set_fan_mode("unsupported")
        with self.assertRaises(ServiceValidationError):
            await entity.async_set_temperature(temperature=100.0)
        with self.assertRaises(ServiceValidationError):
            await entity.async_set_humidity(51)
        with self.assertRaises(ServiceValidationError):
            await entity.async_set_humidity(36.5)  # type: ignore[arg-type]
        with self.assertRaises(ServiceValidationError):
            await entity.async_set_temperature(
                target_temp_low=25.0, target_temp_high=20.0
            )
        self.assertEqual(call_count, len(calls))

    async def test_late_observation_cannot_confirm_newer_command(self) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_temperature", capture)
        await self.manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 22.0},
            {"target_temperature": 22.0},
            None,
        )
        await self.manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 23.0},
            {"target_temperature": 23.0},
            None,
        )
        old_attributes = self._attributes(20.0) | {"temperature": 22.0}
        self.hass.states.async_set(self.ecobee.entity_id, "heat", old_attributes)
        await self.hass.async_block_till_done()
        pending = self.manager.snapshot("mapping_a").command
        self.assertEqual(2, pending.revision)
        self.assertEqual("pending", pending.status.value)
        self.assertEqual(2, len(calls))

        current_attributes = self._attributes(20.0) | {"temperature": 23.0}
        self.hass.states.async_set(self.ecobee.entity_id, "heat", current_attributes)
        await self.hass.async_block_till_done()
        confirmed = self.manager.snapshot("mapping_a").command
        self.assertEqual(2, confirmed.revision)
        self.assertEqual("confirmed", confirmed.status.value)
        self.assertEqual(2, len(calls))
        self.assertNotIn("mapping_a", self.manager._unsub_timeouts)

    async def test_quiet_homekit_remains_healthy_across_cloud_stale_boundaries(
        self,
    ) -> None:
        await self.manager.async_stop()
        homekit_state = self.hass.states.get(self.homekit.entity_id)
        self.assertIsNotNone(homekit_state)
        assert homekit_state is not None
        base_time = homekit_state.last_reported + timedelta(seconds=4)
        unsubscribe = Mock()
        with (
            patch(
                "custom_components.ecobee_unified.manager.dt_util.utcnow",
                return_value=base_time,
            ),
            patch(
                "custom_components.ecobee_unified.manager.async_call_later",
                return_value=unsubscribe,
            ) as schedule,
        ):
            manager = MappingManager(
                self.hass,
                "stale_entry",
                (self.mapping,),
                {
                    CONF_ECOBEE_STALE_SECONDS: 100,
                },
            )
            await manager.async_start()
            self.assertEqual(
                "homekit", manager.snapshot("mapping_a").provenance["hvac_mode"]
            )
            self.assertTrue(schedule.called)
            with patch(
                "custom_components.ecobee_unified.manager.dt_util.utcnow",
                return_value=base_time + timedelta(seconds=2),
            ):
                manager._handle_stale_refresh("mapping_a")
            quiet = manager.snapshot("mapping_a")
            self.assertEqual("healthy", quiet.source_health["homekit"].value)
            self.assertEqual("homekit", quiet.provenance["hvac_mode"])
            with patch(
                "custom_components.ecobee_unified.manager.dt_util.utcnow",
                return_value=base_time + timedelta(seconds=200),
            ):
                manager._handle_stale_refresh("mapping_a")
            later = manager.snapshot("mapping_a")
            self.assertEqual("healthy", later.source_health["homekit"].value)
            self.assertEqual("stale", later.source_health["ecobee"].value)
            self.assertEqual("homekit", later.provenance["hvac_mode"])
            await manager.async_stop()

    async def test_target_humidity_uses_one_homekit_writer_and_homekit_report(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_humidity", capture)
        attributes = self._attributes(20.0) | {
            "supported_features": 389,
            "humidity": 40,
            "min_humidity": 20,
            "max_humidity": 50,
        }
        attributes.pop("target_temp_step", None)
        self.hass.states.async_set(self.homekit.entity_id, "heat", attributes)
        await self.hass.async_block_till_done()
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)

        await entity.async_set_humidity(40)
        self.assertEqual(1, len(calls))
        self.assertEqual("climate", calls[0].domain)
        self.assertEqual("set_humidity", calls[0].service)
        self.assertEqual(
            {"entity_id": self.homekit.entity_id, "humidity": 40},
            dict(calls[0].data),
        )
        self.assertEqual(
            "pending", self.manager.snapshot("mapping_a").command.status.value
        )

        state = self.hass.states.get(self.homekit.entity_id)
        assert state is not None
        self.manager._handle_state_report_event(
            Mock(
                data={
                    "entity_id": self.homekit.entity_id,
                    "last_reported": state.last_reported,
                }
            )
        )
        self.assertEqual(
            "confirmed", self.manager.snapshot("mapping_a").command.status.value
        )
        self.assertEqual(1, len(calls))

    async def test_unchanged_report_keeps_source_fresh(self) -> None:
        homekit_state = self.hass.states.get(self.homekit.entity_id)
        self.assertIsNotNone(homekit_state)
        assert homekit_state is not None
        homekit_state.last_updated = homekit_state.last_reported - timedelta(
            seconds=600
        )

        self.hass.states.async_set(
            self.homekit.entity_id,
            homekit_state.state,
            dict(homekit_state.attributes),
        )
        await self.hass.async_block_till_done()
        reported = self.hass.states.get(self.homekit.entity_id)
        self.assertIsNotNone(reported)
        assert reported is not None
        self.assertGreater(reported.last_reported, reported.last_updated)

        with patch(
            "custom_components.ecobee_unified.manager.dt_util.utcnow",
            return_value=reported.last_reported + timedelta(seconds=1),
        ):
            self.manager.refresh_mapping("mapping_a")
        snapshot = self.manager.snapshot("mapping_a")
        self.assertEqual("healthy", snapshot.source_health["homekit"].value)
        self.assertTrue(snapshot.homekit_writable)

    async def test_source_service_errors_are_safely_translated(self) -> None:
        async def fail_with_private_detail(_call: ServiceCall) -> None:
            raise RuntimeError("private backend detail")

        self.hass.services.async_register(
            "climate", "set_temperature", fail_with_private_detail
        )
        with self.assertRaises(HomeAssistantError) as raised:
            await self.manager.async_standard_command(
                "mapping_a",
                "set_temperature",
                {"temperature": 22.0},
                {"target_temperature": 22.0},
                None,
            )
        self.assertNotIn("private backend detail", str(raised.exception))
        self.assertEqual(
            "failed", self.manager.snapshot("mapping_a").command.status.value
        )
        self.assertNotIn("mapping_a", self.manager._unsub_timeouts)

        self.hass.services.async_register("button", "press", fail_with_private_detail)
        with self.assertRaises(HomeAssistantError) as vendor_raised:
            await self.manager.async_resume_program("mapping_a", None)
        self.assertNotIn("private backend detail", str(vendor_raised.exception))

    async def test_late_awaited_failure_cannot_cancel_newer_command(self) -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        call_count = 0

        async def superseded_failure(_call: ServiceCall) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                await release_first.wait()
                raise RuntimeError("old command failed")

        self.hass.services.async_register(
            "climate", "set_temperature", superseded_failure
        )
        old_task = asyncio.create_task(
            self.manager.async_standard_command(
                "mapping_a",
                "set_temperature",
                {"temperature": 22.0},
                {"target_temperature": 22.0},
                None,
            )
        )
        await first_started.wait()
        await self.manager.async_standard_command(
            "mapping_a",
            "set_temperature",
            {"temperature": 23.0},
            {"target_temperature": 23.0},
            None,
        )
        self.assertEqual(2, self.manager.snapshot("mapping_a").command.revision)
        self.assertIn("mapping_a", self.manager._unsub_timeouts)

        release_first.set()
        with self.assertRaises(HomeAssistantError):
            await old_task
        current = self.manager.snapshot("mapping_a").command
        self.assertEqual(2, current.revision)
        self.assertEqual("pending", current.status.value)
        self.assertIn("mapping_a", self.manager._unsub_timeouts)

    async def test_diagnostics_are_allow_listed_and_identifier_free(self) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        entry.runtime_data = EcobeeUnifiedRuntime(self.manager)
        diagnostics = await async_get_config_entry_diagnostics(self.hass, entry)
        rendered = repr(diagnostics)
        self.assertEqual(1, diagnostics["entry"]["mapping_count"])
        capabilities = diagnostics["mappings"][0]["capabilities"]
        self.assertFalse(capabilities["target_humidity"])
        self.assertTrue(capabilities["temperature_step"])
        self.assertIn("mapping_1", rendered)
        self.assertNotIn("Zone A", rendered)
        self.assertNotIn(self.homekit.id, rendered)
        self.assertNotIn(self.homekit.entity_id, rendered)
        self.assertNotIn(self.ecobee.id, rendered)
        self.assertNotIn(self.ecobee.entity_id, rendered)

    async def test_reconfigure_can_preserve_temporarily_missing_reference(self) -> None:
        er.async_get(self.hass).async_remove(self.homekit.entity_id)
        await self.hass.async_block_till_done()
        self.assertIsNotNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )
        updated = _mapping_from_input(
            self.hass,
            {
                CONF_NAME: "Zone A renamed",
                CONF_HOMEKIT_ENTITY: self.homekit.id,
                CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
            },
            mapping_id=self.mapping.mapping_id,
            preserved=self.mapping,
        )
        self.assertEqual(self.homekit.id, updated[CONF_HOMEKIT_ENTITY])
        self.assertEqual(self.mapping.mapping_id, updated["mapping_id"])

        source_entry = self.hass.config_entries.async_get_entry(
            self.homekit.config_entry_id
        )
        self.assertIsNotNone(source_entry)
        restored = er.async_get(self.hass).async_get_or_create(
            "climate",
            "homekit_controller",
            self.homekit.unique_id,
            config_entry=source_entry,
            device_id=self.homekit.device_id,
            suggested_object_id="hk_a",
        )
        self.hass.states.async_set(restored.entity_id, "heat", self._attributes(20.0))
        await self.hass.async_block_till_done()
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )

    async def test_reconfigure_preserves_missing_optional_source(self) -> None:
        mapping = self.mapping
        er.async_get(self.hass).async_remove(self.homekit_preset.entity_id)
        defaults = _mapping_form_defaults(self.hass, mapping.as_dict())
        self.assertEqual(self.homekit_preset.id, defaults[CONF_HOMEKIT_PRESET_ENTITY])
        updated = _mapping_from_input(
            self.hass,
            defaults,
            mapping_id=mapping.mapping_id,
            preserved=mapping,
        )
        self.assertEqual(self.homekit_preset.id, updated[CONF_HOMEKIT_PRESET_ENTITY])

    async def test_missing_homekit_device_link_creates_repair(self) -> None:
        updated = er.async_get(self.hass).async_update_entity(
            self.homekit.entity_id, device_id=None
        )
        self.assertIsNone(updated.device_id)
        await self.hass.async_block_till_done()
        self.manager.refresh_all()
        self.assertFalse(self.manager.snapshot("mapping_a").homekit_writable)
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertEqual({"source": "homekit device"}, issue.translation_placeholders)

    async def test_source_device_transitions_relink_without_recreating_entry(
        self,
    ) -> None:
        await self.manager.async_stop()
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=1,
        )
        entry.add_to_hass(self.hass)
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        registry = er.async_get(self.hass)
        unified_entity_id = registry.async_get_entity_id(
            "climate", DOMAIN, self.mapping.mapping_id
        )
        self.assertIsNotNone(unified_entity_id)
        assert unified_entity_id is not None
        unified_entity_ids = {
            item.entity_id
            for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        }
        self.assertEqual(4, len(unified_entity_ids))

        def linked_device_ids() -> set[str | None]:
            return {
                registry.async_get(entity_id).device_id
                for entity_id in unified_entity_ids
            }

        replacement = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.homekit.config_entry_id,
            identifiers={("homekit_controller", "replacement_device")},
        )
        registry.async_update_entity(self.homekit.entity_id, device_id=replacement.id)
        registry.async_update_entity(
            self.homekit_preset.entity_id, device_id=replacement.id
        )
        registry.async_update_entity(
            self.homekit_clear_hold.entity_id, device_id=replacement.id
        )
        await self.hass.async_block_till_done()
        self.assertIs(entry, self.hass.config_entries.async_get_entry(entry.entry_id))
        self.assertEqual(
            replacement.id, registry.async_get(unified_entity_id).device_id
        )
        self.assertEqual({replacement.id}, linked_device_ids())

        registry.async_update_entity(self.homekit.entity_id, device_id=None)
        await self.hass.async_block_till_done()
        self.assertIsNone(registry.async_get(unified_entity_id).device_id)
        self.assertEqual({None}, linked_device_ids())
        self.assertIsNotNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )

        registry.async_update_entity(self.homekit.entity_id, device_id=replacement.id)
        await self.hass.async_block_till_done()
        self.assertEqual(
            replacement.id, registry.async_get(unified_entity_id).device_id
        )
        self.assertEqual({replacement.id}, linked_device_ids())
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )
        self.assertTrue(
            entry.runtime_data.manager.snapshot("mapping_a").homekit_writable
        )

        registry.async_remove(self.homekit.entity_id)
        await self.hass.async_block_till_done()
        self.assertIsNone(registry.async_get(unified_entity_id).device_id)
        self.assertEqual({None}, linked_device_ids())
        self.assertIsNotNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )

        source_entry = self.hass.config_entries.async_get_entry(
            self.homekit.config_entry_id
        )
        self.assertIsNotNone(source_entry)
        restored = registry.async_get_or_create(
            "climate",
            "homekit_controller",
            self.homekit.unique_id,
            config_entry=source_entry,
            device_id=replacement.id,
            suggested_object_id="hk_a",
        )
        self.assertEqual(self.homekit.id, restored.id)
        self.hass.states.async_set(restored.entity_id, "heat", self._attributes(20.0))
        await self.hass.async_block_till_done()
        self.assertIs(entry, self.hass.config_entries.async_get_entry(entry.entry_id))
        self.assertEqual(
            replacement.id, registry.async_get(unified_entity_id).device_id
        )
        self.assertEqual({replacement.id}, linked_device_ids())
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )
        self.assertTrue(
            entry.runtime_data.manager.snapshot("mapping_a").homekit_writable
        )

    async def test_missing_ecobee_mapping_creates_repair_without_losing_local_state(
        self,
    ) -> None:
        er.async_get(self.hass).async_remove(self.ecobee.entity_id)
        await self.hass.async_block_till_done()
        snapshot = self.manager.snapshot("mapping_a")
        self.assertTrue(snapshot.available)
        self.assertTrue(snapshot.homekit_writable)
        self.assertIn("ecobee_vendor_context_unavailable", snapshot.degradation)
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertEqual({"source": "ecobee"}, issue.translation_placeholders)

    def _source(
        self,
        platform: str,
        unique_id: str,
        *,
        device: bool = False,
        domain: str = "climate",
    ) -> er.RegistryEntry:
        source_entry = MockConfigEntry(domain=platform)
        source_entry.add_to_hass(self.hass)
        device_id = None
        if device:
            device_id = (
                dr.async_get(self.hass)
                .async_get_or_create(
                    config_entry_id=source_entry.entry_id,
                    identifiers={(platform, f"device_{unique_id}")},
                )
                .id
            )
        entry = er.async_get(self.hass).async_get_or_create(
            domain,
            platform,
            unique_id,
            config_entry=source_entry,
            device_id=device_id,
            suggested_object_id=unique_id,
        )
        attributes = self._attributes(20.0)
        if platform == "homekit_controller":
            attributes.pop("target_temp_step")
        self.hass.states.async_set(entry.entity_id, "heat", attributes)
        return entry

    @staticmethod
    def _attributes(temperature: float) -> dict[str, object]:
        return {
            "current_temperature": temperature,
            "humidity": 36,
            "min_humidity": 20,
            "max_humidity": 50,
            "temperature": 21.0,
            "hvac_action": "heating",
            "hvac_modes": ["off", "heat", "cool", "heat_cool"],
            "fan_mode": "auto",
            "fan_modes": ["auto", "on"],
            "supported_features": 385,
            "fan_min_on_time": 15,
            "equipment_running": "compCool1,fan",
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temp_step": 0.5,
            "unit_of_measurement": "°C",
        }


if __name__ == "__main__":
    unittest.main()
