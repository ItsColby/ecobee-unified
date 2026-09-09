"""Native config-entry cancellation releases a partially started manager."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.helpers.event import async_call_later
from homeassistant.setup import async_setup_component

from custom_components.ecobee_unified.const import CONF_MAPPINGS, DOMAIN
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import CommandStatus

from . import test_runtime_core_api as runtime_tests


class SetupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Compose the native fixture without collecting its existing tests again."""

    async def asyncSetUp(self) -> None:
        self.runtime = runtime_tests.RuntimeCoreApiTests()
        self.runtime.setUp()
        await self.runtime.asyncSetUp()
        self.hass = self.runtime.hass
        await self.runtime.manager.async_stop()
        self.assertTrue(await async_setup_component(self.hass, DOMAIN, {}))

    async def asyncTearDown(self) -> None:
        await self.runtime.asyncTearDown()
        self.runtime.tearDown()

    async def test_cancel_during_manager_start(self) -> None:
        await self._cancel_setup("start", "mismatch")

    async def test_cancel_during_forward_with_mismatch_deadline(self) -> None:
        await self._cancel_setup("forward", "mismatch")

    async def test_cancel_during_forward_with_settle_deadline(self) -> None:
        await self._cancel_setup("forward", "settle")

    async def _cancel_setup(self, phase: str, deadline: str) -> None:
        mapping = replace(
            self.runtime.mapping,
            homekit_temperature_entity=self.runtime.homekit_temperature.id,
        )
        if deadline == "mismatch":
            self.hass.states.async_set(
                self.runtime.homekit.entity_id,
                "heat",
                self.runtime._attributes(21.0),
            )
        entry = runtime_tests.MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [mapping.as_dict()]},
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(self.hass)
        native_cleanup = Mock(return_value=None)
        entry.async_on_unload(native_cleanup)
        reached = asyncio.Event()
        blocked = asyncio.Event()
        original_start = MappingManager.async_start

        async def start(manager: MappingManager) -> None:
            await original_start(manager)
            if phase == "start":
                reached.set()
                await blocked.wait()

        async def forward(*_args: object) -> None:
            reached.set()
            await blocked.wait()

        async def write(_call: ServiceCall) -> None:
            """A local fixture writer accepts without generating confirmation."""

        self.hass.services.async_register("climate", "set_temperature", write)
        with (
            patch.object(MappingManager, "async_start", start),
            patch.object(
                self.hass.config_entries, "async_forward_entry_setups", forward
            ),
            patch(
                "custom_components.ecobee_unified.manager.async_call_later",
                side_effect=lambda *args: Mock(wraps=async_call_later(*args)),
            ),
        ):
            setup = asyncio.create_task(
                self.hass.config_entries.async_setup(entry.entry_id)
            )
            manager = None
            try:
                await asyncio.wait_for(reached.wait(), 5)
                manager = entry.runtime_data.manager
                self.runtime.manager = manager
                self.assertEqual(ConfigEntryState.SETUP_IN_PROGRESS, entry.state)
                await manager.async_standard_command(
                    mapping.mapping_id,
                    "set_temperature",
                    {"temperature": 22.0},
                    {"target_temperature": 22.0},
                    None,
                )
                if deadline == "settle":
                    source = self.hass.states.get(
                        self.runtime.homekit_temperature.entity_id
                    )
                    self.assertIsNotNone(source)
                    self.hass.states.async_set(
                        source.entity_id, "21.2", source.attributes
                    )
                    await self.hass.async_block_till_done()
                    self.assertTrue(manager._homekit_settle_report_times)
                    self.assertTrue(manager._unsub_homekit_settles)
                else:
                    self.assertTrue(manager._temperature_mismatch_candidates)
                    self.assertTrue(manager._unsub_temperature_mismatches)
                self.assertTrue(manager._temperature_recovery)
                for name in (
                    "_unsub_state",
                    "_unsub_state_report",
                    "_unsub_registry",
                    "_unsub_device_registry",
                ):
                    unsubscribe = getattr(manager, name)
                    self.assertIsNotNone(unsubscribe, name)
                    setattr(manager, name, Mock(wraps=unsubscribe))
                subscriptions = [
                    manager._unsub_state,
                    manager._unsub_state_report,
                    manager._unsub_registry,
                    manager._unsub_device_registry,
                ]
                self.assertTrue(manager._unsub_timeouts)
                self.assertTrue(manager._unsub_stale_refreshes)
                deadlines = [
                    unsubscribe
                    for handles in (
                        manager._unsub_timeouts,
                        manager._unsub_stale_refreshes,
                        manager._unsub_homekit_settles,
                        manager._unsub_temperature_mismatches,
                    )
                    for unsubscribe in handles.values()
                ]
                setup.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await setup
                self.assertEqual(ConfigEntryState.SETUP_ERROR, entry.state)
                self.assertFalse(entry.setup_lock.locked())
                native_cleanup.assert_called_once_with()
                self.assertTrue(manager._stopped.is_set())
                for unsubscribe in (*subscriptions, *deadlines):
                    unsubscribe.assert_called_once_with()
                self.assertEqual(
                    CommandStatus.UNCONFIRMED,
                    manager.diagnostic_command_summary(mapping.mapping_id).status,
                )
                snapshot = manager.snapshot(mapping.mapping_id)
                self.hass.states.async_set(
                    self.runtime.ecobee.entity_id,
                    "heat",
                    self.runtime._attributes(23.0),
                )
                await self.hass.async_block_till_done()
                self.assertIs(snapshot, manager.snapshot(mapping.mapping_id))
                self._assert_stopped(manager)
            finally:
                setup.cancel()
                await asyncio.gather(setup, return_exceptions=True)
                if manager is not None:
                    await manager.async_stop()

    def _assert_stopped(self, manager: MappingManager) -> None:
        for name in (
            "_unsub_state",
            "_unsub_state_report",
            "_unsub_registry",
            "_unsub_device_registry",
        ):
            self.assertIsNone(getattr(manager, name), name)
        for name in (
            "_unsub_timeouts",
            "_unsub_stale_refreshes",
            "_unsub_homekit_settles",
            "_unsub_temperature_mismatches",
            "_homekit_settle_report_times",
            "_temperature_mismatch_candidates",
            "_temperature_recovery",
            "_watched_entity_ids",
            "_watched_device_ids",
        ):
            self.assertFalse(getattr(manager, name), name)
