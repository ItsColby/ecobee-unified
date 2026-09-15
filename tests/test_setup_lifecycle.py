"""Native config-entry cancellation releases a partially started manager."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import Mock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified import async_setup_entry
from custom_components.ecobee_unified.const import CONF_MAPPINGS, DOMAIN
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import CommandStatus

from .runtime_fixture import CoreRuntimeTestCase


class SetupLifecycleTests(CoreRuntimeTestCase):
    """Exercise cancellation through native config-entry setup."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.manager.async_stop()
        self.assertTrue(await async_setup_component(self.hass, DOMAIN, {}))

    async def test_cancel_during_manager_start(self) -> None:
        await self._cancel_setup("start", "mismatch")

    async def test_cancel_during_forward_with_mismatch_deadline(self) -> None:
        await self._cancel_setup("forward", "mismatch")

    async def test_cancel_during_forward_with_settle_deadline(self) -> None:
        await self._cancel_setup("forward", "settle")

    async def test_cancel_after_platform_load_then_retry(self) -> None:
        """Release real entity platforms acquired before another setup is cancelled."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(self.hass)
        pending = asyncio.Event()
        release = asyncio.Event()
        native_forward = self.hass.config_entries.async_forward_entry_setups

        async def forward(config_entry, platforms) -> None:
            await native_forward(config_entry, ["climate"])
            await native_forward(
                config_entry,
                [platform for platform in platforms if platform != "climate"],
            )

        sensor_task = None

        async def sensor_setup(*_args) -> None:
            nonlocal sensor_task
            sensor_task = asyncio.current_task()
            pending.set()
            await release.wait()

        with (
            patch.object(
                self.hass.config_entries, "async_forward_entry_setups", forward
            ),
            patch("homeassistant.components.sensor.async_setup_entry", sensor_setup),
        ):
            setup = asyncio.create_task(
                self.hass.config_entries.async_setup(entry.entry_id)
            )
            try:
                await asyncio.wait_for(pending.wait(), 10)
                old_manager = entry.runtime_data.manager
                self.manager = old_manager
                component = self.hass.data["entity_components"]["climate"]
                old_platform = component._platforms[entry.entry_id]
                old_entities = dict(old_platform.entities)
                self.assertTrue(old_entities)
                self.assertTrue(all(self.hass.states.get(key) for key in old_entities))
                setup.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await setup
                self.assertEqual(ConfigEntryState.SETUP_ERROR, entry.state)
                self.assertTrue(old_manager._stopped.is_set())
                self._assert_stopped(old_manager)
                self.assertIsNotNone(sensor_task)
                self.assertTrue(sensor_task.cancelled())
                for entity_component in self.hass.data["entity_components"].values():
                    self.assertNotIn(entry.entry_id, entity_component._platforms)
                self.assertFalse(old_platform.entities)
                for entity_id in old_entities:
                    self.assertIsNone(component.get_entity(entity_id))
            finally:
                setup.cancel()
                await asyncio.gather(setup, return_exceptions=True)
                release.set()

        self.assertTrue(await self.hass.config_entries.async_reload(entry.entry_id))
        self.assertEqual(ConfigEntryState.LOADED, entry.state)
        new_manager = entry.runtime_data.manager
        self.manager = new_manager
        self.assertIsNot(new_manager, old_manager)
        self.assertFalse(new_manager._stopped.is_set())
        for entity_id, old_entity in old_entities.items():
            entity = component.get_entity(entity_id)
            self.assertIsNotNone(entity)
            self.assertIsNot(entity, old_entity)
            self.assertIs(entity._manager, new_manager)
            self.assertIsNotNone(self.hass.states.get(entity_id))
        registry = er.async_get(self.hass)
        self.assertTrue(all(registry.async_get(key) for key in old_entities))
        self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))

    async def test_rollback_error_preserves_setup_error_and_stops_manager(self) -> None:
        """Failed platform rollback cannot hide the setup result or retain the manager."""
        for error in (RuntimeError("forwarding failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                entry = MockConfigEntry(
                    domain=DOMAIN,
                    data={CONF_MAPPINGS: [self.mapping.as_dict()]},
                )
                entry.add_to_hass(self.hass)
                with (
                    patch.object(
                        self.hass.config_entries,
                        "async_forward_entry_setups",
                        side_effect=error,
                    ),
                    patch.object(
                        self.hass.config_entries,
                        "async_unload_platforms",
                        side_effect=RuntimeError("rollback failed"),
                    ),
                    self.assertRaises(type(error)) as raised,
                ):
                    await async_setup_entry(self.hass, entry)
                self.assertIs(raised.exception, error)
                self.assertTrue(entry.runtime_data.manager._stopped.is_set())
                self._assert_stopped(entry.runtime_data.manager)

    async def _cancel_setup(self, phase: str, deadline: str) -> None:
        mapping = replace(
            self.mapping,
            homekit_temperature_entity=self.homekit_temperature.id,
        )
        if deadline == "mismatch":
            self.hass.states.async_set(
                self.homekit.entity_id,
                "heat",
                self._attributes(21.0),
            )
        entry = MockConfigEntry(
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
                self.manager = manager
                self.assertEqual(ConfigEntryState.SETUP_IN_PROGRESS, entry.state)
                await manager.async_standard_command(
                    mapping.mapping_id,
                    "set_temperature",
                    {"temperature": 22.0},
                    {"target_temperature": 22.0},
                    None,
                )
                if deadline == "settle":
                    source = self.hass.states.get(self.homekit_temperature.entity_id)
                    self.assertIsNotNone(source)
                    self.hass.states.async_set(
                        source.entity_id, "21.2", source.attributes
                    )
                    await self.hass.async_block_till_done()
                    self.assertTrue(manager._homekit_settle_report_times)
                    self.assertTrue(manager._unsub_homekit_settles)
                else:
                    # Arm a fresh mismatch immediately before cancellation: startup
                    # and command awaits may consume the prior 0.25-second deadline.
                    source = self.hass.states.get(self.homekit_temperature.entity_id)
                    self.assertIsNotNone(source)
                    self.hass.states.async_set(
                        source.entity_id, "20.1", source.attributes
                    )
                    manager.refresh_mapping(mapping.mapping_id)
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
                    self.ecobee.entity_id,
                    "heat",
                    self._attributes(23.0),
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
