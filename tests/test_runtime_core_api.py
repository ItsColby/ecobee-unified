"""Core 2026.8 API checks that do not require the Linux pytest plugin."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from homeassistant import loader
from homeassistant.config_entries import SOURCE_USER, ConfigEntries
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified import async_migrate_entry
from custom_components.ecobee_unified.climate import EcobeeUnifiedClimate
from custom_components.ecobee_unified.config_flow import _mapping_from_input
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
                "confirm_change": True,
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

    def _source(
        self, platform: str, unique_id: str, *, device: bool = False
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
            "climate",
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
