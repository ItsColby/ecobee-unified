"""Shared real-Core registry, state, and manager fixture without test methods."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from homeassistant import loader
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntries
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.const import DOMAIN
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import MappingConfig


class CoreRuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    """Provide native fixtures and stop the manager before Core and storage."""

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
        self.homekit = self._source(
            "homekit_controller",
            "hk_a",
            device=True,
            physical_identity="thermostat_a",
        )
        self.ecobee = self._source(
            "ecobee", "ec_a", device=True, physical_identity="thermostat_a"
        )
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
        self.hass.states.async_set(
            self.ecobee_aqi.entity_id,
            "42",
            {ATTR_DEVICE_CLASS: SensorDeviceClass.AQI},
        )
        self.homekit_temperature = registry.async_get_or_create(
            "sensor",
            "homekit_controller",
            "hk_a_current_temperature",
            config_entry=homekit_entry,
            device_id=self.homekit.device_id,
            suggested_object_id="hk_a_current_temperature",
            original_device_class=SensorDeviceClass.TEMPERATURE,
            unit_of_measurement="°C",
        )
        self.hass.states.async_set(
            self.homekit_temperature.entity_id,
            "20.04",
            {
                ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                ATTR_UNIT_OF_MEASUREMENT: "°C",
            },
        )
        self.ecobee_notify = registry.async_get_or_create(
            "notify",
            "ecobee",
            "ec_a_notify",
            config_entry=ecobee_entry,
            device_id=self.ecobee.device_id,
            suggested_object_id="ec_a_notify",
        )
        self.hass.states.async_set(self.ecobee_notify.entity_id, "unknown")
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

    async def _wait_for_fixture_event(
        self, task: asyncio.Task[Any], started: asyncio.Event
    ) -> None:
        """Report an early task result instead of waiting on its absent event."""
        waiter = asyncio.create_task(started.wait())
        try:
            done, _ = await asyncio.wait(
                (task, waiter), timeout=1, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                await task
                self.fail("Task completed before reaching the fixture event")
            self.assertIn(waiter, done, "Task did not reach the fixture event")
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def _settle_fixture_tasks(self, *tasks: asyncio.Task[Any] | None) -> None:
        """Cancel and retrieve only the caller tasks created by a gated fixture."""
        owned = [task for task in tasks if task is not None]
        for task in owned:
            if not task.done():
                task.cancel()
        done, pending = await asyncio.wait(owned, timeout=1)
        for task in done:
            if not task.cancelled():
                task.exception()
        self.assertFalse(pending, "Fixture tasks did not settle")

    def _source(
        self,
        platform: str,
        unique_id: str,
        *,
        device: bool = False,
        domain: str = "climate",
        physical_identity: str | None = None,
    ) -> er.RegistryEntry:
        source_entry = MockConfigEntry(domain=platform)
        source_entry.add_to_hass(self.hass)
        device_id = None
        if device:
            identifiers = {(platform, f"device_{unique_id}")}
            serial_number = None
            if platform == "homekit_controller":
                serial_number = physical_identity
            elif platform == "ecobee" and physical_identity:
                identifiers = {(platform, physical_identity)}
            device_id = (
                dr.async_get(self.hass)
                .async_get_or_create(
                    config_entry_id=source_entry.entry_id,
                    identifiers=identifiers,
                    serial_number=serial_number,
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
            "equipment_running": "auxHeat1,fan",
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temp_step": 0.5,
            "unit_of_measurement": "°C",
        }


class ReconfigureFlowTestCase(CoreRuntimeTestCase):
    """Drive the integration's native reconfigure flow through Core."""

    async def _open(self, entry: MockConfigEntry) -> dict[str, Any]:
        return await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )

    async def _next(self, result: dict[str, Any], step: str) -> dict[str, Any]:
        return await self._submit(result, {"next_step_id": step})

    async def _submit(
        self, result: dict[str, Any], values: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.hass.config_entries.flow.async_configure(
            result["flow_id"], values
        )

    async def _save(self, result: dict[str, Any]) -> dict[str, Any]:
        with patch.object(self.hass.config_entries, "async_reload", return_value=True):
            result = await self._next(result, "reconfigure_finish")
            if result["type"] == FlowResultType.FORM:
                self.assertEqual("reconfigure_finish", result["step_id"])
                result = await self._submit(result, {})
            await self.hass.async_block_till_done()
        self.assertEqual("reconfigure_successful", result["reason"])
        return result
