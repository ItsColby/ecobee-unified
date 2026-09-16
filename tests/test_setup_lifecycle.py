"""Native config-entry cancellation releases a partially started manager."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import Mock, patch

from homeassistant import setup as ha_setup
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, EntityStateAttribute
from homeassistant.core import ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform
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
        """Entry cancellation releases ownership and retries after global setup."""
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
        selected = set()
        native_forward = self.hass.config_entries.async_forward_entry_setups

        async def forward(config_entry, platforms) -> None:
            selected.update(platforms)
            await native_forward(
                config_entry,
                [platform for platform in platforms if platform != "sensor"],
            )
            await native_forward(config_entry, ["sensor"])

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
                self.assertIn("sensor", selected)
                self.assertTrue(selected <= self.hass.config.components)
                old_manager = entry.runtime_data.manager
                self.manager = old_manager
                old_platforms = {
                    domain: component._platforms[entry.entry_id]
                    for domain, component in self.hass.data["entity_components"].items()
                    if entry.entry_id in component._platforms
                }
                old_entities = {
                    entity_id: entity
                    for platform in old_platforms.values()
                    for entity_id, entity in platform.entities.items()
                }
                self.assertIn("climate", old_platforms)
                self.assertTrue(old_entities)
                self.assertTrue(all(self.hass.states.get(key) for key in old_entities))
                setup.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(setup, 10)
                await self._async_drain_entry_tasks(entry)
                self.assertEqual(ConfigEntryState.SETUP_ERROR, entry.state)
                self.assertIsNotNone(sensor_task)
                self.assertTrue(sensor_task.cancelled())
                self._assert_entry_cleanup(
                    entry, old_manager, old_platforms, old_entities
                )
            finally:
                setup.cancel()
                release.set()
                await asyncio.wait_for(
                    asyncio.gather(setup, return_exceptions=True), 10
                )
                await self._async_drain_entry_tasks(entry)

        self.assertTrue(
            await asyncio.wait_for(
                self.hass.config_entries.async_reload(entry.entry_id), 10
            )
        )
        await self._async_drain_entry_tasks(entry)
        self.assertEqual(ConfigEntryState.LOADED, entry.state)
        self.assertFalse(entry.setup_lock.locked())
        new_manager = entry.runtime_data.manager
        self.manager = new_manager
        self.assertIsNot(new_manager, old_manager)
        self.assertFalse(new_manager._stopped.is_set())
        new_platforms = {
            domain: component._platforms[entry.entry_id]
            for domain, component in self.hass.data["entity_components"].items()
            if entry.entry_id in component._platforms
        }
        new_entities = {
            entity_id: entity
            for platform in new_platforms.values()
            for entity_id, entity in platform.entities.items()
        }
        for entity_id, old_entity in old_entities.items():
            entity = new_entities.get(entity_id)
            self.assertIsNotNone(entity)
            self.assertIsNot(entity, old_entity)
            self.assertIs(entity._manager, new_manager)
            self.assertIsNotNone(self.hass.states.get(entity_id))
        registry = er.async_get(self.hass)
        self.assertTrue(all(registry.async_get(key) for key in old_entities))
        self.assertTrue(
            await asyncio.wait_for(
                self.hass.config_entries.async_unload(entry.entry_id), 10
            )
        )
        await self._async_drain_entry_tasks(entry)
        self._assert_entry_cleanup(entry, new_manager, new_platforms, new_entities)

    async def test_cancel_cold_global_setup_releases_entry_ownership(self) -> None:
        """Pinned Core retains a cancelled global future; entry cleanup still holds."""
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
        selected = set()
        native_forward = self.hass.config_entries.async_forward_entry_setups
        native_deps_reqs = ha_setup.async_process_deps_reqs
        sensor_task = None

        async def forward(config_entry, platforms) -> None:
            selected.update(platforms)
            await native_forward(
                config_entry,
                [platform for platform in platforms if platform != "sensor"],
            )
            await native_forward(config_entry, ["sensor"])

        async def deps_reqs(hass, config, integration) -> None:
            nonlocal sensor_task
            if integration.domain == "sensor":
                sensor_task = asyncio.current_task()
                pending.set()
                await release.wait()
            await native_deps_reqs(hass, config, integration)

        with (
            patch.object(
                self.hass.config_entries, "async_forward_entry_setups", forward
            ),
            patch("homeassistant.setup.async_process_deps_reqs", deps_reqs),
        ):
            setup = asyncio.create_task(
                self.hass.config_entries.async_setup(entry.entry_id)
            )
            try:
                await asyncio.wait_for(pending.wait(), 10)
                self.assertIn("sensor", selected)
                self.assertNotIn("sensor", self.hass.config.components)
                self.assertTrue(selected - {"sensor"} <= self.hass.config.components)
                old_manager = entry.runtime_data.manager
                self.manager = old_manager
                old_platforms = {
                    domain: component._platforms[entry.entry_id]
                    for domain, component in self.hass.data["entity_components"].items()
                    if entry.entry_id in component._platforms
                }
                old_entities = {
                    entity_id: entity
                    for platform in old_platforms.values()
                    for entity_id, entity in platform.entities.items()
                }
                self.assertIn("climate", old_platforms)
                self.assertTrue(old_entities)
                self.assertTrue(all(self.hass.states.get(key) for key in old_entities))
                setup.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(setup, 10)
                await self._async_drain_entry_tasks(entry)
                self.assertEqual(ConfigEntryState.SETUP_ERROR, entry.state)
                self.assertIsNotNone(sensor_task)
                self.assertTrue(sensor_task.cancelled())
                self._assert_entry_cleanup(
                    entry, old_manager, old_platforms, old_entities
                )
            finally:
                setup.cancel()
                release.set()
                await asyncio.wait_for(
                    asyncio.gather(setup, return_exceptions=True), 10
                )
                await self._async_drain_entry_tasks(entry)

        # Both patches are removed: exercise Core's cached failure and the
        # integration's ordinary concurrent forwarding on the failed retry.
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(async_setup_component(self.hass, "sensor", {}), 10)
        self.assertNotIn("sensor", self.hass.config.components)
        self.assertFalse(
            await asyncio.wait_for(
                self.hass.config_entries.async_reload(entry.entry_id), 10
            )
        )
        await self._async_drain_entry_tasks(entry)
        self.assertEqual(ConfigEntryState.SETUP_ERROR, entry.state)
        new_manager = entry.runtime_data.manager
        self.manager = new_manager
        self.assertIsNot(new_manager, old_manager)
        self._assert_entry_cleanup(entry, new_manager, old_platforms, old_entities)

    async def _async_drain_entry_tasks(self, entry: MockConfigEntry) -> None:
        """Drain native forward tasks as well as HA-tracked work without retries."""
        # Core creates eager forwarding tasks outside HA's tracked task sets.
        prefixes = tuple(
            f"config entry forward {action} {entry.title} {entry.domain} {entry.entry_id} "
            for action in ("setup", "unload")
        )
        failures = []
        async with asyncio.timeout(10):
            while True:
                # Keep references before yielding: an untracked forward can
                # finish during HA's drain and disappear from all_tasks().
                pending = {
                    task
                    for task in asyncio.all_tasks()
                    if task.get_name().startswith(prefixes)
                }
                await self.hass.async_block_till_done()
                pending.update(
                    task
                    for task in asyncio.all_tasks()
                    if task.get_name().startswith(prefixes)
                )
                if not pending:
                    self.assertFalse(failures)
                    return
                outcomes = await asyncio.gather(*pending, return_exceptions=True)
                failures.extend(
                    outcome
                    for outcome in outcomes
                    if isinstance(outcome, BaseException)
                )

    def _assert_entry_cleanup(
        self,
        entry: MockConfigEntry,
        manager: MappingManager,
        platforms: dict[str, EntityPlatform],
        entities: dict[str, Entity],
    ) -> None:
        """Check complete entry ownership before teardown or any manual unload."""
        self.assertFalse(entry.setup_lock.locked())
        self.assertTrue(manager._stopped.is_set())
        self._assert_stopped(manager)
        components = self.hass.data["entity_components"].values()
        for component in components:
            self.assertNotIn(entry.entry_id, component._platforms)
        for platform in platforms.values():
            self.assertFalse(platform.entities)
        registry = er.async_get(self.hass)
        for entity_id, entity in entities.items():
            row = registry.async_get(entity_id)
            self.assertIsNotNone(row, entity_id)
            self.assertIsNotNone(entity.registry_entry, entity_id)
            self.assertEqual(entity.registry_entry.id, row.id, entity_id)
            self.assertEqual(entry.entry_id, row.config_entry_id, entity_id)
        entity_ids = set(entities) | {
            row.entity_id
            for row in er.async_entries_for_config_entry(registry, entry.entry_id)
        }
        for entity_id in entity_ids:
            for component in components:
                self.assertIsNone(component.get_entity(entity_id), entity_id)
            if (state := self.hass.states.get(entity_id)) is not None:
                # Core preserves an unavailable placeholder for registry entries.
                self.assertEqual(STATE_UNAVAILABLE, state.state, entity_id)
                self.assertIs(
                    state.attributes[EntityStateAttribute.RESTORED], True, entity_id
                )
                row = registry.async_get(entity_id)
                self.assertIsNotNone(row, entity_id)
                self.assertFalse(row.disabled, entity_id)
                self.assertEqual(entry.entry_id, row.config_entry_id, entity_id)

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
