"""Core 2026.8 API checks that do not require the Linux pytest plugin."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import voluptuous as vol
from homeassistant import loader
from homeassistant.components.climate.const import HVACMode
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
from custom_components.ecobee_unified.climate import EcobeeUnifiedClimate
from custom_components.ecobee_unified.config_flow import (
    _mapping_form_defaults,
    _mapping_from_input,
    _validate_no_duplicate_sources,
)
from custom_components.ecobee_unified.const import (
    CONF_ADD_ANOTHER,
    CONF_BEESTAT_STALE_SECONDS,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_STALE_SECONDS,
    CONF_MAPPINGS,
    CONF_NAME,
    CONF_NEXT_TRANSITION_ENTITY,
    CONF_SCHEDULED_PROFILE_ENTITY,
    DOMAIN,
)
from custom_components.ecobee_unified.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import MappingConfig
from custom_components.ecobee_unified.runtime import EcobeeUnifiedRuntime


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
        self.ecobee = self._source("ecobee", "ec_a")
        self.mapping = MappingConfig(
            "mapping_a", "Zone A", self.homekit.id, self.ecobee.id
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
            frozenset({"active_comfort_sensors", "source_age_seconds"}),
            entity._unrecorded_attributes,
        )

    async def test_entity_properties_only_project_the_normalized_snapshot(self) -> None:
        attributes = self._attributes(20.0) | {
            "current_humidity": 42,
            "target_temp_low": 18.0,
            "target_temp_high": 24.0,
            "supported_features": 395,
        }
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
            self.assertEqual(21.0, entity.target_temperature)
            self.assertEqual(18.0, entity.target_temperature_low)
            self.assertEqual(24.0, entity.target_temperature_high)
            self.assertEqual("auto", entity.fan_mode)
            self.assertEqual(["auto", "on"], entity.fan_modes)
            self.assertEqual(7.0, entity.min_temp)
            self.assertEqual(35.0, entity.max_temp)
            self.assertEqual(0.5, entity.target_temperature_step)
            self.assertEqual("°C", entity.temperature_unit)
            self.assertEqual(395, int(entity.supported_features))
            self.assertIn("source_health", entity.extra_state_attributes)

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

    async def test_mapping_validation_rejects_wrong_domains_and_reused_context(
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

        context = self._source("beestat_statistics", "scheduled_a", domain="sensor")
        with self.assertRaisesRegex(vol.Invalid, "duplicate_beestat_source"):
            _mapping_from_input(
                self.hass,
                {
                    CONF_NAME: "Zone A",
                    CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                    CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
                    CONF_SCHEDULED_PROFILE_ENTITY: context.entity_id,
                    CONF_NEXT_TRANSITION_ENTITY: context.entity_id,
                },
            )

        candidate = _mapping_from_input(
            self.hass,
            {
                CONF_NAME: "Zone A",
                CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
                CONF_SCHEDULED_PROFILE_ENTITY: context.entity_id,
            },
        )
        homekit_b = self._source("homekit_controller", "hk_b", device=True)
        ecobee_b = self._source("ecobee", "ec_b")
        duplicate_context = _mapping_from_input(
            self.hass,
            {
                CONF_NAME: "Zone B",
                CONF_HOMEKIT_ENTITY: homekit_b.entity_id,
                CONF_ECOBEE_ENTITY: ecobee_b.entity_id,
                CONF_NEXT_TRANSITION_ENTITY: context.entity_id,
            },
        )
        with self.assertRaisesRegex(vol.Invalid, "duplicate_beestat_source"):
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
            CONF_HOMEKIT_STALE_SECONDS: 360,
            CONF_ECOBEE_STALE_SECONDS: 1200,
            CONF_BEESTAT_STALE_SECONDS: 24_000,
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
        )
        entry.add_to_hass(self.hass)
        self.assertTrue(await async_migrate_entry(self.hass, entry))
        self.assertEqual(1, entry.minor_version)
        self.assertEqual([self.mapping.as_dict()], entry.data[CONF_MAPPINGS])

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

    async def test_optional_context_renames_degrades_and_recovers_independently(
        self,
    ) -> None:
        scheduled = self._source("beestat_statistics", "scheduled_a", domain="sensor")
        transition = self._source("beestat_statistics", "transition_a", domain="sensor")
        self.hass.states.async_set(scheduled.entity_id, "Home")
        self.hass.states.async_set(transition.entity_id, "2026-08-08T01:00:00+00:00")
        mapping = MappingConfig(
            "mapping_optional",
            "Zone A",
            self.homekit.id,
            self.ecobee.id,
            scheduled.id,
            transition.id,
        )
        manager = MappingManager(self.hass, "optional_entry", (mapping,), {})
        await manager.async_start()
        snapshot = manager.snapshot(mapping.mapping_id)
        self.assertEqual("Home", snapshot.scheduled_profile)
        self.assertEqual("2026-08-08T01:00:00+00:00", snapshot.next_transition)

        registry = er.async_get(self.hass)
        registry.async_update_entity(
            scheduled.entity_id, new_entity_id="sensor.scheduled_a_renamed"
        )
        self.hass.states.async_set("sensor.scheduled_a_renamed", "Away")
        await self.hass.async_block_till_done()
        self.assertEqual("Away", manager.snapshot(mapping.mapping_id).scheduled_profile)

        self.hass.states.async_set("sensor.scheduled_a_renamed", "unavailable")
        await self.hass.async_block_till_done()
        degraded = manager.snapshot(mapping.mapping_id)
        self.assertIsNone(degraded.scheduled_profile)
        self.assertIsNotNone(degraded.next_transition)
        self.assertEqual(
            "unavailable", degraded.source_health["scheduled_profile"].value
        )
        self.assertEqual("healthy", degraded.source_health["next_transition"].value)
        self.assertIn("scheduled_profile_unavailable", degraded.degradation)
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(
                DOMAIN, f"mapping_{mapping.mapping_id}"
            )
        )

        self.hass.states.async_set("sensor.scheduled_a_renamed", "Sleep")
        await self.hass.async_block_till_done()
        self.assertEqual(
            "Sleep", manager.snapshot(mapping.mapping_id).scheduled_profile
        )
        await manager.async_stop()

    async def test_exact_single_writer_service_calls(self) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_temperature", capture)
        self.hass.services.async_register("ecobee", "set_fan_min_on_time", capture)
        self.hass.services.async_register("ecobee", "resume_program", capture)
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
        await self.manager.async_set_minimum_fan_runtime("mapping_a", 15, None)
        self.assertEqual(1, len(calls))
        self.assertEqual("ecobee", calls[0].domain)
        self.assertEqual("set_fan_min_on_time", calls[0].service)
        self.assertEqual(
            {"entity_id": self.ecobee.entity_id, "fan_min_on_time": 15},
            dict(calls[0].data),
        )

        calls.clear()
        await self.manager.async_resume_program("mapping_a", False, None)
        self.assertEqual(1, len(calls))
        self.assertEqual("ecobee", calls[0].domain)
        self.assertEqual("resume_program", calls[0].service)
        self.assertEqual(
            {"entity_id": self.ecobee.entity_id, "resume_all": False},
            dict(calls[0].data),
        )

    async def test_unavailable_writers_and_invalid_vendor_bounds_fail_before_effect(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("climate", "set_temperature", capture)
        self.hass.services.async_register("ecobee", "resume_program", capture)
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

        self.hass.states.async_set(self.ecobee.entity_id, "unavailable", {})
        await self.hass.async_block_till_done()
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_resume_program("mapping_a", False, None)
        for invalid in (-1, 61):
            with (
                self.subTest(minutes=invalid),
                self.assertRaises(ServiceValidationError),
            ):
                await self.manager.async_set_minimum_fan_runtime(
                    "mapping_a", invalid, None
                )
        self.assertEqual([], calls)

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

    async def test_all_climate_methods_validate_capability_and_use_one_writer(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        for service in (
            "set_hvac_mode",
            "set_temperature",
            "set_fan_mode",
            "turn_off",
            "turn_on",
        ):
            self.hass.services.async_register("climate", service, capture)
        attributes = self._attributes(20.0) | {"supported_features": 395}
        self.hass.states.async_set(self.homekit.entity_id, "heat", attributes)
        await self.hass.async_block_till_done()
        entity = EcobeeUnifiedClimate(self.manager, self.mapping)

        await entity.async_set_hvac_mode(HVACMode.COOL)
        await entity.async_set_temperature(temperature=22.0)
        await entity.async_set_temperature(target_temp_low=19.0, target_temp_high=24.0)
        await entity.async_set_fan_mode("auto")
        await entity.async_turn_off()
        await entity.async_turn_on()

        self.assertEqual(
            [
                "set_hvac_mode",
                "set_temperature",
                "set_temperature",
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

    async def test_source_crosses_stale_boundary_without_another_event(self) -> None:
        await self.manager.async_stop()
        homekit_state = self.hass.states.get(self.homekit.entity_id)
        self.assertIsNotNone(homekit_state)
        assert homekit_state is not None
        base_time = homekit_state.last_updated + timedelta(seconds=4)
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
                    CONF_HOMEKIT_STALE_SECONDS: 5,
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
            stale = manager.snapshot("mapping_a")
            self.assertEqual("stale", stale.source_health["homekit"].value)
            self.assertEqual("ecobee", stale.provenance["hvac_mode"])
            await manager.async_stop()

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

        self.hass.services.async_register(
            "ecobee", "resume_program", fail_with_private_detail
        )
        with self.assertRaises(HomeAssistantError) as vendor_raised:
            await self.manager.async_resume_program("mapping_a", False, None)
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

    async def test_reconfigure_preserves_missing_optional_context(self) -> None:
        context = self._source("beestat_statistics", "scheduled_a", domain="sensor")
        mapping = MappingConfig(
            self.mapping.mapping_id,
            self.mapping.name,
            self.mapping.homekit_entity,
            self.mapping.ecobee_entity,
            scheduled_profile_entity=context.id,
        )
        er.async_get(self.hass).async_remove(context.entity_id)
        defaults = _mapping_form_defaults(self.hass, mapping.as_dict())
        self.assertEqual(context.id, defaults[CONF_SCHEDULED_PROFILE_ENTITY])
        updated = _mapping_from_input(
            self.hass,
            defaults,
            mapping_id=mapping.mapping_id,
            preserved=mapping,
        )
        self.assertEqual(context.id, updated[CONF_SCHEDULED_PROFILE_ENTITY])

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
        self.hass.states.async_set(entry.entity_id, "heat", self._attributes(20.0))
        return entry

    @staticmethod
    def _attributes(temperature: float) -> dict[str, object]:
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


if __name__ == "__main__":
    unittest.main()
