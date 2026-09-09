"""Native action-role validation, wrong-effect prevention, and source recovery."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import voluptuous as vol
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.ecobee_unified.button import EcobeeUnifiedResumeProgramButton
from custom_components.ecobee_unified.climate import EcobeeUnifiedClimate
from custom_components.ecobee_unified.config_flow import _mapping_from_input
from custom_components.ecobee_unified.const import (
    CONF_ECOBEE_ENTITY,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_NAME,
    DOMAIN,
)
from custom_components.ecobee_unified.models import SourceHealth

from . import test_runtime_core_api as runtime_tests


class HomeKitActionRoleTests(unittest.IsolatedAsyncioTestCase):
    """Reuse Core's registry/state fixture without collecting its tests twice."""

    async def asyncSetUp(self) -> None:
        self.runtime = runtime_tests.RuntimeCoreApiTests()
        self.runtime.setUp()
        await self.runtime.asyncSetUp()
        self.hass = self.runtime.hass
        self.manager = self.runtime.manager
        self.mapping = self.runtime.mapping
        self.registry = er.async_get(self.hass)
        self.preset = self.runtime.homekit_preset
        self.clear_hold = self.runtime.homekit_clear_hold
        self.calls: list[ServiceCall] = []
        self.hass.services.async_register("select", "select_option", self._capture)
        self.hass.services.async_register("button", "press", self._capture)

    async def asyncTearDown(self) -> None:
        await self.runtime.asyncTearDown()
        self.runtime.tearDown()

    async def _capture(self, call: ServiceCall) -> None:
        self.calls.append(call)

    def _mapping_input(self) -> dict[str, str]:
        return {
            CONF_NAME: "Zone A",
            CONF_HOMEKIT_ENTITY: self.runtime.homekit.entity_id,
            CONF_ECOBEE_ENTITY: self.runtime.ecobee.entity_id,
            CONF_HOMEKIT_PRESET_ENTITY: self.preset.entity_id,
            CONF_HOMEKIT_CLEAR_HOLD_ENTITY: self.clear_hold.entity_id,
        }

    async def test_configuration_rejects_display_units_and_identify(self) -> None:
        """Native metadata distinguishes these same-device incompatible roles."""

        for source, metadata, error in (
            (
                self.preset,
                {"entity_category": EntityCategory.CONFIG},
                "invalid_homekit_preset_source",
            ),
            (
                self.preset,
                {"translation_key": "temperature_display_units"},
                "invalid_homekit_preset_source",
            ),
            (
                self.clear_hold,
                {"entity_category": EntityCategory.DIAGNOSTIC},
                "invalid_homekit_clear_hold_source",
            ),
            (
                self.clear_hold,
                {"original_device_class": "identify"},
                "invalid_homekit_clear_hold_source",
            ),
            (
                self.clear_hold,
                {"device_class": "restart"},
                "invalid_homekit_clear_hold_source",
            ),
            (
                self.clear_hold,
                {"entity_category": EntityCategory.CONFIG},
                "invalid_homekit_clear_hold_source",
            ),
        ):
            with self.subTest(metadata=metadata):
                self.registry.async_update_entity(source.entity_id, **metadata)
                with self.assertRaisesRegex(vol.Invalid, error):
                    _mapping_from_input(self.hass, self._mapping_input())
                self.registry.async_update_entity(
                    source.entity_id, **dict.fromkeys(metadata)
                )

    async def test_configuration_validates_preset_options_not_current_value(
        self,
    ) -> None:
        self.registry.async_update_entity(
            self.preset.entity_id, translation_key="ecobee_mode"
        )
        for options in (
            ["celsius", "fahrenheit"],
            ["home", "vacation"],
            ["Home", "home"],
            [],
            None,
        ):
            with self.subTest(options=options):
                self.hass.states.async_set(
                    self.preset.entity_id, "unknown", {"options": options}
                )
                with self.assertRaisesRegex(
                    vol.Invalid, "invalid_homekit_preset_source"
                ):
                    _mapping_from_input(self.hass, self._mapping_input())
        self.hass.states.async_set(
            self.preset.entity_id, "unknown", {"options": ["home", "sleep", "away"]}
        )
        mapping = _mapping_from_input(self.hass, self._mapping_input())
        self.assertEqual(self.preset.id, mapping[CONF_HOMEKIT_PRESET_ENTITY])
        self.assertEqual(self.clear_hold.id, mapping[CONF_HOMEKIT_CLEAR_HOLD_ENTITY])
        await self.hass.async_block_till_done()
        await self.manager.async_set_preset_mode("mapping_a", "away", None)
        self.assertEqual("away", self.calls[0].data["option"])
        self.assertEqual(
            SourceHealth.UNKNOWN,
            self.manager.snapshot("mapping_a").source_health["homekit_preset"],
        )

    async def test_runtime_rejects_wrong_options_and_recovers_from_source_event(
        self,
    ) -> None:
        self.hass.states.async_set(
            self.preset.entity_id,
            "celsius",
            {"options": ["celsius", "fahrenheit"]},
        )
        await self.hass.async_block_till_done()
        climate = EcobeeUnifiedClimate(self.manager, self.mapping)
        self.assertEqual([], climate.preset_modes)
        self.assertIsNone(climate.preset_mode)
        self.assertFalse(climate.supported_features & ClimateEntityFeature.PRESET_MODE)
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_set_preset_mode("mapping_a", "fahrenheit", None)
        self.assertFalse(self.calls)
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        self.assertIsNotNone(issue)
        self.assertIn("HomeKit preset", issue.translation_placeholders["source"])

        self.hass.states.async_set(
            self.preset.entity_id, "Home", {"options": ["Home", "Away"]}
        )
        await self.hass.async_block_till_done()
        self.assertTrue(climate.supported_features & ClimateEntityFeature.PRESET_MODE)
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )
        await self.manager.async_set_preset_mode("mapping_a", "Away", None)
        self.assertEqual("select", self.calls[0].domain)

    async def test_runtime_rejects_identify_and_recovers_from_registry_event(
        self,
    ) -> None:
        self.registry.async_update_entity(
            self.clear_hold.entity_id,
            entity_category=EntityCategory.DIAGNOSTIC,
            original_device_class="identify",
        )
        await self.hass.async_block_till_done()
        button = EcobeeUnifiedResumeProgramButton(self.manager, self.mapping)
        self.assertFalse(button.available)
        with self.assertRaises(ServiceValidationError):
            await self.manager.async_resume_program("mapping_a", None)
        self.assertFalse(self.calls)
        self.assertIsNotNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )

        self.registry.async_update_entity(
            self.clear_hold.entity_id, entity_category=None, original_device_class=None
        )
        await self.hass.async_block_till_done()
        self.assertTrue(button.available)
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )
        await self.manager.async_resume_program("mapping_a", None)
        self.assertEqual("button", self.calls[0].domain)

    async def test_dispatch_rechecks_registry_role_even_with_stale_snapshot(
        self,
    ) -> None:
        """A queue delay cannot authorize an action from a previously valid role."""

        snapshot = self.manager.snapshot("mapping_a")
        lock = self.manager._command_locks["mapping_a"]
        await lock.acquire()
        preset_command = asyncio.create_task(
            self.manager.async_set_preset_mode("mapping_a", "Away", None)
        )
        resume_command = asyncio.create_task(
            self.manager.async_resume_program("mapping_a", None)
        )
        await asyncio.sleep(0)
        self.registry.async_update_entity(
            self.preset.entity_id, translation_key="temperature_display_units"
        )
        self.registry.async_update_entity(
            self.clear_hold.entity_id, original_device_class="identify"
        )
        with patch.object(self.manager, "snapshot", return_value=snapshot):
            lock.release()
            outcomes = await asyncio.gather(
                preset_command, resume_command, return_exceptions=True
            )
        self.assertTrue(
            all(isinstance(result, ServiceValidationError) for result in outcomes)
        )
        self.assertFalse(self.calls)

    async def test_renamed_sources_preserve_identity_and_native_dispatch(self) -> None:
        renamed_preset = self.registry.async_update_entity(
            self.preset.entity_id, new_entity_id="select.renamed_mode"
        )
        renamed_clear_hold = self.registry.async_update_entity(
            self.clear_hold.entity_id, new_entity_id="button.renamed_action"
        )
        self.hass.states.async_remove(self.preset.entity_id)
        self.hass.states.async_remove(self.clear_hold.entity_id)
        self.hass.states.async_set(
            renamed_preset.entity_id, "Home", {"options": ["Home", "Away"]}
        )
        self.hass.states.async_set(renamed_clear_hold.entity_id, "unknown")
        await self.hass.async_block_till_done()
        mapping = _mapping_from_input(
            self.hass,
            {
                **self._mapping_input(),
                CONF_HOMEKIT_PRESET_ENTITY: renamed_preset.entity_id,
                CONF_HOMEKIT_CLEAR_HOLD_ENTITY: renamed_clear_hold.entity_id,
            },
            mapping_id=self.mapping.mapping_id,
            preserved=self.mapping,
        )
        self.assertEqual(self.preset.id, mapping[CONF_HOMEKIT_PRESET_ENTITY])
        self.assertEqual(self.clear_hold.id, mapping[CONF_HOMEKIT_CLEAR_HOLD_ENTITY])
        await self.manager.async_set_preset_mode("mapping_a", "Away", None)
        await self.manager.async_resume_program("mapping_a", None)
        self.assertEqual(
            [renamed_preset.entity_id, renamed_clear_hold.entity_id],
            [call.data["entity_id"] for call in self.calls],
        )

    async def test_unavailable_with_registry_options_and_missing_saved_references(
        self,
    ) -> None:
        self.registry.async_update_entity(
            self.preset.entity_id, capabilities={"options": ["Home", "Away"]}
        )
        self.hass.states.async_set(self.preset.entity_id, "unavailable")
        self.hass.states.async_set(self.clear_hold.entity_id, "unavailable")
        _mapping_from_input(self.hass, self._mapping_input())
        await self.hass.async_block_till_done()
        self.assertFalse(self.manager.snapshot("mapping_a").homekit_preset_writable)
        self.assertFalse(self.manager.snapshot("mapping_a").homekit_clear_hold_writable)
        self.assertIsNone(
            ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_mapping_a")
        )

        self.registry.async_remove(self.preset.entity_id)
        self.registry.async_remove(self.clear_hold.entity_id)
        mapping = _mapping_from_input(
            self.hass,
            {
                **self._mapping_input(),
                CONF_HOMEKIT_PRESET_ENTITY: self.preset.id,
                CONF_HOMEKIT_CLEAR_HOLD_ENTITY: self.clear_hold.id,
            },
            mapping_id=self.mapping.mapping_id,
            preserved=self.mapping,
        )
        self.assertEqual(self.preset.id, mapping[CONF_HOMEKIT_PRESET_ENTITY])
        self.assertEqual(self.clear_hold.id, mapping[CONF_HOMEKIT_CLEAR_HOLD_ENTITY])
        await self.hass.async_block_till_done()
        self.assertEqual(
            SourceHealth.MISSING,
            self.manager.snapshot("mapping_a").source_health["homekit_preset"],
        )
