"""Command admission, unload, cancellation and native confirmation events."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from inspect import iscoroutine
from unittest.mock import Mock, patch

from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.ecobee_unified.climate import EcobeeUnifiedClimate
from custom_components.ecobee_unified.const import (
    CONF_MAPPINGS,
    CONF_RELOAD_SILENT_TEMPERATURE_SOURCE,
    DOMAIN,
    HOMEKIT_SOURCE_DOMAIN,
)
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import CommandStatus
from custom_components.ecobee_unified.temperature_quality import TemperatureSilence

from .runtime_fixture import CoreRuntimeTestCase, unified_entry


class CommandLifecycleTests(CoreRuntimeTestCase):
    """Exercise command lifecycle through real Core registries and states."""

    async def _enable_temperature_recovery(self) -> None:
        await self.manager.async_stop()
        self.mapping = replace(
            self.mapping, homekit_temperature_entity=self.homekit_temperature.id
        )
        self.hass.states.async_remove(self.homekit_temperature.entity_id)
        self.hass.states.async_set(
            self.homekit_temperature.entity_id,
            "20.04",
            {"device_class": "temperature", "unit_of_measurement": "°C"},
            timestamp=(dt_util.utcnow() - timedelta(minutes=10)).timestamp(),
        )
        sibling = replace(
            self.mapping, mapping_id="mapping_b", homekit_temperature_entity=None
        )
        self.manager = MappingManager(
            self.hass,
            "entry_a",
            (self.mapping, sibling),
            {CONF_RELOAD_SILENT_TEMPERATURE_SOURCE: True},
        )
        await self.manager.async_start()
        await self.hass.async_block_till_done()
        silence = TemperatureSilence()
        for seconds in (120, 100):
            silence.climate_changed(
                self.manager._temperature_source_identity(self.mapping),
                dt_util.utcnow() - timedelta(seconds=seconds),
            )
        self.manager._temperature_silence["mapping_a"] = silence

    async def test_recovery_waits_for_admission_dispatch_queue_and_confirmation(
        self,
    ) -> None:
        for kind in ("notification", "standard"):
            with self.subTest(kind=kind):
                await self._enable_temperature_recovery()
                started, release = asyncio.Event(), asyncio.Event()

                async def writer(
                    call: ServiceCall, *, started=started, release=release
                ) -> None:
                    started.set()
                    self.manager.refresh_mapping("mapping_a")
                    await release.wait()

                self._register_writer(kind, writer)
                active = queued = None
                with patch.object(
                    self.hass.config_entries, "async_reload", return_value=True
                ) as reload:
                    try:
                        active = asyncio.create_task(self._command(kind))
                        await self._wait_for_fixture_event(active, started)
                        reload.assert_not_awaited()
                        queued = asyncio.create_task(self._command(kind))
                        await asyncio.sleep(0)
                        self.assertEqual(
                            2, self.manager._admitted_commands["mapping_a"]
                        )
                        self.manager.refresh_mapping("mapping_a")
                        reload.assert_not_awaited()
                        release.set()
                        await asyncio.gather(active, queued)
                        if kind == "standard":
                            reload.assert_not_awaited()
                            revision = self.manager._tracker.pending_revision(
                                "mapping_a"
                            )
                            assert revision is not None
                            self.manager._handle_timeout("mapping_a", revision)
                        await self.hass.async_block_till_done()
                        reload.assert_awaited_once_with(self.homekit.config_entry_id)
                        self.assertFalse(self.manager._temperature_reload_tasks)
                        self.assertFalse(self.manager._reloading_sources)
                    finally:
                        release.set()
                        await self._settle_fixture_tasks(active, queued)

    async def test_sibling_without_precise_sensor_blocks_recovery(self) -> None:
        await self._enable_temperature_recovery()
        with patch.object(
            self.hass.config_entries, "async_reload", return_value=True
        ) as reload:
            async with self.manager._command_slot("mapping_b"):
                self.manager.refresh_mapping("mapping_a")
                await asyncio.sleep(0)
                reload.assert_not_awaited()
            await self.hass.async_block_till_done()
            reload.assert_awaited_once_with(self.homekit.config_entry_id)

    async def test_deferred_recovery_rechecks_command_admission(self) -> None:
        await self._enable_temperature_recovery()
        with patch.object(
            self.hass.config_entries, "async_reload", return_value=True
        ) as reload:
            self.manager.refresh_mapping("mapping_a")
            self.assertTrue(self.manager._temperature_reload_tasks)
            # Enter synchronously through admission before recovery gets its turn.
            async with self.manager._command_slot("mapping_b"):
                reload.assert_not_awaited()
            await self.hass.async_block_till_done()
            reload.assert_awaited_once_with(self.homekit.config_entry_id)

    async def test_sibling_confirmation_blocks_recovery_until_observed(self) -> None:
        await self._enable_temperature_recovery()
        revision = self.manager._tracker.begin(
            "mapping_b", "set_temperature", {"target_temperature": 22.0}
        )
        self.manager._tracker.accept_write("mapping_b", revision)
        with patch.object(
            self.hass.config_entries, "async_reload", return_value=True
        ) as reload:
            self.manager.refresh_mapping("mapping_a")
            await self.hass.async_block_till_done()
            reload.assert_not_awaited()
            self.hass.states.async_set(
                self.ecobee.entity_id,
                "heat",
                self._attributes(20.0) | {"temperature": 22.0},
            )
            await self.hass.async_block_till_done()
            self.assertIs(
                CommandStatus.CONFIRMED,
                self.manager.snapshot("mapping_b").command.status,
            )
            reload.assert_awaited_once_with(self.homekit.config_entry_id)

    async def test_stop_cancels_deferred_recovery_before_dispatch(self) -> None:
        await self._enable_temperature_recovery()
        with patch.object(self.hass.config_entries, "async_reload") as reload:
            self.manager.refresh_mapping("mapping_a")
            self.assertTrue(self.manager._temperature_reload_tasks)
            await self.manager.async_stop()
            await self.hass.async_block_till_done()
            reload.assert_not_awaited()
            self.assertFalse(self.manager._temperature_reload_tasks)
            self.assertFalse(self.manager._reloading_sources)

    async def test_running_recovery_rejects_writers_and_cleans_up(self) -> None:
        for result in (True, False):
            with self.subTest(result=result):
                await self._enable_temperature_recovery()
                started, release = asyncio.Event(), asyncio.Event()

                async def recover(
                    entry_id: str, *, started=started, release=release, result=result
                ) -> bool:
                    started.set()
                    await release.wait()
                    return result is True

                with patch.object(
                    self.hass.config_entries, "async_reload", side_effect=recover
                ) as reload:
                    self.manager.refresh_mapping("mapping_a")
                    task = self.manager._temperature_reload_tasks[
                        self.homekit.config_entry_id
                    ]
                    await self._wait_for_fixture_event(task, started)
                    for kind in (
                        "standard",
                        "vendor",
                        "action",
                        "resume",
                        "preset",
                        "notification",
                    ):
                        with (
                            self.subTest(kind=kind),
                            self.assertRaises(ServiceValidationError) as error,
                        ):
                            await self._command(kind)
                        self.assertEqual(
                            "source_recovery_in_progress",
                            error.exception.translation_key,
                        )
                    release.set()
                    await task
                    # A sibling refresh must not resurrect pre-reload evidence.
                    self.manager.refresh_mapping("mapping_b")
                    issue = ir.async_get(self.hass).async_get_issue(
                        DOMAIN, "homekit_temperature_silent_mapping_a"
                    )
                    self.assertEqual(result is False, issue is not None)
                    self.manager.refresh_mapping("mapping_a")
                    issue = ir.async_get(self.hass).async_get_issue(
                        DOMAIN, "homekit_temperature_silent_mapping_a"
                    )
                    self.assertEqual(result is False, issue is not None)
                    self.assertFalse(self.manager._temperature_reload_tasks)
                    self.assertFalse(self.manager._reloading_sources)
                    reload.assert_awaited_once()

    async def test_native_source_reload_finishes_during_unified_unload(self) -> None:
        """Never strand Core's source unload or setup by cancelling its reload."""

        for phase in ("unload", "setup"):
            with self.subTest(phase=phase):
                await self._enable_temperature_recovery()
                silence = self.manager._temperature_silence["mapping_a"]
                await self.manager.async_stop()
                entry = unified_entry(
                    data={CONF_MAPPINGS: [self.mapping.as_dict()]},
                    options={CONF_RELOAD_SILENT_TEMPERATURE_SOURCE: True},
                )
                entry.add_to_hass(self.hass)
                self.assertTrue(
                    await self.hass.config_entries.async_setup(entry.entry_id)
                )
                self.manager = entry.runtime_data.manager
                self.manager._temperature_silence["mapping_a"] = silence
                source = self.hass.config_entries.async_get_entry(
                    self.homekit.config_entry_id
                )
                assert source is not None
                source.mock_state(self.hass, ConfigEntryState.LOADED)
                entered, release = asyncio.Event(), asyncio.Event()
                calls: list[str] = []

                async def boundary(
                    current: str,
                    *,
                    calls=calls,
                    phase=phase,
                    entered=entered,
                    release=release,
                ) -> bool:
                    calls.append(current)
                    if current == phase:
                        entered.set()
                        await release.wait()
                    return True

                async def source_unload(
                    hass, config_entry, *, boundary=boundary
                ) -> bool:
                    return await boundary("unload")

                async def source_setup(
                    hass, config_entry, *, boundary=boundary
                ) -> bool:
                    return await boundary("setup")

                mock_integration(
                    self.hass,
                    MockModule(
                        HOMEKIT_SOURCE_DOMAIN,
                        async_unload_entry=source_unload,
                        async_setup_entry=source_setup,
                    ),
                    top_level_files={"config_flow.py"},
                )
                mock_platform(self.hass, f"{HOMEKIT_SOURCE_DOMAIN}.config_flow", Mock())
                self.hass.config.components.add(HOMEKIT_SOURCE_DOMAIN)
                recovery = stop = None
                with patch.object(source, "async_migrate", return_value=True):
                    try:
                        self.manager.refresh_mapping("mapping_a")
                        recovery = self.manager._temperature_reload_tasks[
                            source.entry_id
                        ]
                        await self._wait_for_fixture_event(recovery, entered)
                        self.assertIs(
                            ConfigEntryState.UNLOAD_IN_PROGRESS
                            if phase == "unload"
                            else ConfigEntryState.SETUP_IN_PROGRESS,
                            source.state,
                        )
                        stop = asyncio.create_task(
                            self.hass.config_entries.async_unload(entry.entry_id)
                        )
                        # Platform teardown may await Core; wait for manager admission to close.
                        await self._wait_for_fixture_event(stop, self.manager._stopped)
                        self._assert_stopped()
                        self.assertFalse(recovery.done())
                        self.assertFalse(stop.done())
                        with self.assertRaises(ServiceValidationError):
                            await self._command("standard")
                        with patch(
                            "custom_components.ecobee_unified.manager.async_dispatcher_send"
                        ) as publish:
                            self.manager.refresh_all()
                            release.set()
                            self.assertTrue(
                                await asyncio.wait_for(asyncio.shield(stop), 1)
                            )
                            self.assertIsNone(await recovery)
                            publish.assert_not_called()
                        self.assertFalse(recovery.cancelled())
                        self.assertIs(ConfigEntryState.LOADED, source.state)
                        self.assertTrue(source.state.recoverable)
                        self.assertFalse(source.setup_lock.locked())
                        self.assertFalse(self.manager._temperature_reload_tasks)
                        self.assertFalse(self.manager._reloading_sources)
                        self.assertEqual(["unload", "setup"], calls)
                        self.assertTrue(
                            await self.hass.config_entries.async_reload(source.entry_id)
                        )
                        self.assertIs(ConfigEntryState.LOADED, source.state)
                        self.assertEqual(["unload", "setup"] * 2, calls)
                    finally:
                        release.set()
                        await self._settle_fixture_tasks(recovery, stop)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.source_mapping = self.mapping
        await self._new_manager()

    async def _new_manager(self) -> None:
        await self.manager.async_stop()
        self.mapping = replace(
            self.source_mapping,
            ecobee_notify_entity=self.ecobee_notify.id,
        )
        self.manager = MappingManager(self.hass, "entry_a", (self.mapping,), {})
        await self.manager.async_start()
        await self.hass.async_block_till_done()

    async def _command(self, kind: str, manager: MappingManager | None = None) -> None:
        manager = manager or self.manager
        if kind == "standard":
            await manager.async_standard_command(
                "mapping_a",
                "set_temperature",
                {"temperature": 22.0},
                {"target_temperature": 22.0},
                None,
            )
        elif kind == "vendor":
            await manager.async_set_minimum_fan_runtime("mapping_a", 20, None)
        elif kind == "action":
            await manager.async_vendor_action(
                "mapping_a", "set_occupancy_modes", {"follow_me": True}, None
            )
        elif kind == "resume":
            await manager.async_resume_program("mapping_a", None)
        elif kind == "preset":
            await manager.async_set_preset_mode("mapping_a", "Away", None)
        else:
            await manager.async_send_notification("mapping_a", "Test message", None)

    def _register_writer(self, kind: str, writer) -> None:
        domain, service = {
            "standard": ("climate", "set_temperature"),
            "vendor": ("ecobee", "set_fan_min_on_time"),
            "action": ("ecobee", "set_occupancy_modes"),
            "resume": ("button", "press"),
            "preset": ("select", "select_option"),
            "notification": ("notify", "send_message"),
        }[kind]
        self.hass.services.async_register(domain, service, writer)

    def _assert_stopped(self, manager: MappingManager | None = None) -> None:
        manager = manager or self.manager
        self.assertIsNone(manager._unsub_state)
        self.assertIsNone(manager._unsub_state_report)
        self.assertIsNone(manager._unsub_registry)
        self.assertIsNone(manager._unsub_device_registry)
        self.assertFalse(manager._unsub_timeouts)
        self.assertFalse(manager._unsub_stale_refreshes)
        self.assertFalse(manager._unsub_homekit_settles)
        self.assertFalse(manager._unsub_temperature_mismatches)

    async def test_stopped_manager_rejects_every_writer_entry_point(self) -> None:
        calls = []

        async def writer(call: ServiceCall) -> None:
            calls.append(call)

        kinds = ("standard", "vendor", "action", "resume", "preset", "notification")
        for kind in kinds:
            self._register_writer(kind, writer)
        await self.manager.async_stop()
        for kind in kinds:
            with self.subTest(kind=kind), self.assertRaises(ServiceValidationError):
                await self._command(kind)
        self.assertFalse(calls)
        self._assert_stopped()

    @asynccontextmanager
    async def _queued_command(self, command: Awaitable[None]):
        """Queue a real command behind a writer without changing tracked state."""

        started, release = asyncio.Event(), asyncio.Event()
        calls: list[ServiceCall] = []

        async def blocker(call: ServiceCall) -> None:
            started.set()
            await release.wait()

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        self.hass.services.async_register("notify", "send_message", blocker)
        for domain, services in (
            (
                "climate",
                (
                    "set_temperature",
                    "set_humidity",
                    "set_fan_mode",
                    "set_hvac_mode",
                    "turn_on",
                    "turn_off",
                ),
            ),
            ("ecobee", ("create_vacation", "set_sensors_used_in_climate")),
        ):
            for service in services:
                self.hass.services.async_register(domain, service, capture)
        active = asyncio.create_task(
            self.manager.async_send_notification("mapping_a", "Test message", None)
        )
        queued = None
        try:
            await self._wait_for_fixture_event(active, started)
            queued = asyncio.create_task(command)
            await asyncio.sleep(0)
            self.assertFalse(queued.done())
            yield queued, calls, release
            release.set()
            await asyncio.wait_for(asyncio.shield(active), timeout=1)
        finally:
            release.set()
            if queued is None and iscoroutine(command):
                command.close()
            await self._settle_fixture_tasks(active, queued)

    async def test_queued_standard_commands_revalidate_current_writer_contracts(
        self,
    ) -> None:
        attributes = self._attributes(20.0) | {
            "supported_features": int(
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
                | ClimateEntityFeature.TARGET_HUMIDITY
                | ClimateEntityFeature.FAN_MODE
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
            )
        }
        cases = (
            (
                "set_temperature",
                {"temperature": 30.0},
                {"max_temp": 25.0},
                "invalid_temperature",
            ),
            (
                "set_temperature",
                {"temperature": 22.0},
                {"supported_features": 0},
                "unsupported_command",
            ),
            (
                "set_temperature",
                {"target_temp_low": 20.0, "target_temp_high": 23.0},
                {"supported_features": 1},
                "unsupported_command",
            ),
            (
                "set_humidity",
                {"humidity": 45},
                {"max_humidity": 40},
                "invalid_humidity",
            ),
            (
                "set_humidity",
                {"humidity": 40},
                {"supported_features": 0},
                "unsupported_command",
            ),
            (
                "set_fan_mode",
                {"fan_mode": "on"},
                {"fan_modes": ["auto"]},
                "unsupported_fan_mode",
            ),
            (
                "set_hvac_mode",
                {"hvac_mode": HVACMode.COOL},
                {"hvac_modes": ["off", "heat"]},
                "unsupported_hvac_mode",
            ),
            ("turn_on", {}, {"supported_features": 0}, "unsupported_command"),
            ("turn_off", {}, {"supported_features": 0}, "unsupported_command"),
        )
        for service, payload, changed, error in cases:
            with self.subTest(service=service, changed=changed):
                self.hass.states.async_set(self.homekit.entity_id, "heat", attributes)
                self.manager.refresh_mapping("mapping_a")
                climate = EcobeeUnifiedClimate(self.manager, self.mapping)
                climate.hass = self.hass
                command = getattr(climate, f"async_{service}")(**payload)
                async with self._queued_command(command) as (queued, calls, release):
                    previous = self.manager.snapshot("mapping_a").command
                    # Keep the cached snapshot old: dispatch must read current
                    # source state even before its normal callback runs.
                    assert self.manager._unsub_state is not None
                    self.manager._unsub_state()
                    self.manager._unsub_state = None
                    self.hass.states.async_set(
                        self.homekit.entity_id, "heat", attributes | changed
                    )
                    release.set()
                    with self.assertRaises(ServiceValidationError) as raised:
                        await asyncio.wait_for(asyncio.shield(queued), timeout=1)
                    self.assertEqual(error, raised.exception.translation_key)
                    self.assertFalse(calls)
                    self.assertEqual(
                        previous, self.manager.snapshot("mapping_a").command
                    )
                    self.assertFalse(self.manager._command_locks["mapping_a"].locked())
                self.manager._subscribe_states()

    async def test_queued_vendor_actions_revalidate_bounds_and_device_ownership(
        self,
    ) -> None:
        registry = dr.async_get(self.hass)
        owned = registry.async_get_or_create(
            config_entry_id=self.ecobee.config_entry_id,
            identifiers={("ecobee", "selected_sensor")},
        )
        climate = EcobeeUnifiedClimate(self.manager, self.mapping)
        climate.hass = self.hass
        mutations: tuple[
            tuple[Callable[[], Awaitable[None]], Callable[[], object]], ...
        ] = (
            (
                lambda: climate.async_create_vacation("Trip", 30.0, 20.0),
                lambda: self.hass.states.async_set(
                    self.ecobee.entity_id,
                    "heat",
                    self._attributes(20.0) | {"max_temp": 25.0},
                ),
            ),
            (
                lambda: climate.async_set_sensors_used_in_climate([owned.id], "Home"),
                lambda: registry.async_update_device(
                    owned.id, new_config_entry_id=self.homekit.config_entry_id
                ),
            ),
        )
        for command, mutate in mutations:
            async with self._queued_command(command()) as (queued, calls, release):
                previous = self.manager.snapshot("mapping_a").command
                mutate()
                release.set()
                with self.assertRaises(ServiceValidationError):
                    await asyncio.wait_for(asyncio.shield(queued), timeout=1)
                self.assertFalse(calls)
                self.assertEqual(previous, self.manager.snapshot("mapping_a").command)
        registry.async_update_device(
            owned.id, new_config_entry_id=self.ecobee.config_entry_id
        )
        async with self._queued_command(
            climate.async_set_sensors_used_in_climate([owned.id], "Home")
        ) as (queued, calls, release):
            release.set()
            await asyncio.wait_for(asyncio.shield(queued), timeout=1)
            self.assertEqual(1, len(calls))
            self.assertEqual([owned.id], calls[0].data["device_ids"])
            self.assertEqual(self.ecobee.entity_id, calls[0].data["entity_id"])
            self.assertIs(
                CommandStatus.SUBMITTED,
                self.manager.snapshot("mapping_a").command.status,
            )

    async def test_oversized_command_numbers_fail_validation_before_tracking(
        self,
    ) -> None:
        self.hass.states.async_set(
            self.homekit.entity_id,
            "heat",
            self._attributes(20.0) | {"supported_features": 389},
        )
        self.manager.refresh_mapping("mapping_a")
        climate = EcobeeUnifiedClimate(self.manager, self.mapping)
        climate.hass = self.hass
        huge = 10**1000
        for command in (
            lambda: climate.async_set_temperature(temperature=huge),
            lambda: climate.async_set_humidity(huge),
            lambda: climate.async_create_vacation("Trip", huge, 20),
            lambda: self.manager.async_set_minimum_fan_runtime("mapping_a", huge, None),
        ):
            with self.assertRaises(ServiceValidationError):
                await command()
            self.assertIs(
                CommandStatus.NONE, self.manager.snapshot("mapping_a").command.status
            )

    async def test_stop_rejects_queue_and_fences_late_writer_results(self) -> None:
        for kind in ("standard", "action", "notification"):
            for fail in (False, True):
                with self.subTest(kind=kind, fail=fail):
                    await self._new_manager()
                    started, release = asyncio.Event(), asyncio.Event()
                    calls = []

                    async def writer(
                        call: ServiceCall,
                        *,
                        calls=calls,
                        started=started,
                        release=release,
                        fail=fail,
                    ) -> None:
                        calls.append(call)
                        started.set()
                        await release.wait()
                        if fail:
                            raise RuntimeError("private writer detail")

                    self._register_writer(kind, writer)
                    active = asyncio.create_task(self._command(kind))
                    queued = None
                    stop_task = None
                    try:
                        await self._wait_for_fixture_event(active, started)
                        queued = asyncio.create_task(self._command(kind))
                        await asyncio.sleep(0)
                        stop_task = asyncio.create_task(self.manager.async_stop())
                        await asyncio.wait_for(asyncio.shield(stop_task), timeout=1)
                        self.assertFalse(active.done())
                        with self.assertRaises(ServiceValidationError):
                            await asyncio.wait_for(queued, 1)
                        self._assert_stopped()
                        if kind != "notification":
                            self.assertIs(
                                CommandStatus.UNCONFIRMED,
                                self.manager.diagnostic_command_summary(
                                    "mapping_a"
                                ).status,
                            )
                        with patch(
                            "custom_components.ecobee_unified.manager.async_dispatcher_send"
                        ) as publish:
                            release.set()
                            if fail:
                                with self.assertRaises(HomeAssistantError) as raised:
                                    await asyncio.wait_for(
                                        asyncio.shield(active), timeout=1
                                    )
                                self.assertNotIn(
                                    "private writer detail", str(raised.exception)
                                )
                            else:
                                await asyncio.wait_for(
                                    asyncio.shield(active), timeout=1
                                )
                            publish.assert_not_called()
                        self.assertEqual(1, len(calls))
                        self._assert_stopped()

                    finally:
                        release.set()
                        await self._settle_fixture_tasks(active, queued, stop_task)

    async def test_cancelled_writer_preserves_uncertainty_without_retry(self) -> None:
        for kind in ("standard", "action", "notification"):
            for stop in (False, True):
                with self.subTest(kind=kind, stopped=stop):
                    await self._new_manager()
                    started, release = asyncio.Event(), asyncio.Event()
                    calls = []

                    async def writer(
                        call: ServiceCall,
                        *,
                        calls=calls,
                        started=started,
                        release=release,
                    ) -> None:
                        calls.append(call)
                        started.set()
                        await release.wait()

                    self._register_writer(kind, writer)
                    active = asyncio.create_task(self._command(kind))
                    stop_task = None
                    try:
                        await self._wait_for_fixture_event(active, started)
                        if stop:
                            stop_task = asyncio.create_task(self.manager.async_stop())
                            await asyncio.wait_for(asyncio.shield(stop_task), timeout=1)
                        active.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(asyncio.shield(active), timeout=1)
                        if kind != "notification":
                            self.assertIs(
                                CommandStatus.UNCONFIRMED,
                                self.manager.diagnostic_command_summary(
                                    "mapping_a"
                                ).status,
                            )
                            self.assertFalse(self.manager._unsub_timeouts)
                            if not stop:
                                self.assertIs(
                                    CommandStatus.UNCONFIRMED,
                                    self.manager.snapshot("mapping_a").command.status,
                                )
                        self.assertEqual(1, len(calls))
                        self.assertFalse(
                            self.manager._command_locks["mapping_a"].locked()
                        )
                        if stop:
                            self._assert_stopped()

                    finally:
                        release.set()
                        await self._settle_fixture_tasks(active, stop_task)

    async def test_cancelled_queue_does_not_leak_or_dispatch(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def writer(call: ServiceCall) -> None:
            calls.append(call)
            started.set()
            await release.wait()

        self._register_writer("standard", writer)
        active = asyncio.create_task(self._command("standard"))
        queued = None
        try:
            await self._wait_for_fixture_event(active, started)
            queued = asyncio.create_task(self._command("standard"))
            await asyncio.sleep(0)
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(queued), timeout=1)
            release.set()
            await asyncio.wait_for(asyncio.shield(active), timeout=1)
            self.assertEqual(1, len(calls))
            await asyncio.wait_for(self._command("standard"), timeout=1)
            self.assertEqual(2, len(calls))

        finally:
            release.set()
            await self._settle_fixture_tasks(active, queued)

    async def test_cancellation_after_writer_return_releases_command_lock(self) -> None:
        for kind in ("standard", "action", "notification"):
            with self.subTest(kind=kind):
                await self._new_manager()
                calls = []

                async def writer(call: ServiceCall, *, calls=calls) -> None:
                    calls.append(call)
                    if len(calls) == 1:
                        command_task = asyncio.current_task()
                        assert command_task is not None
                        # The writer returns synchronously; cancellation arrives at
                        # the command slot's next await, while its helpers are drained.
                        asyncio.get_running_loop().call_soon(command_task.cancel)

                self._register_writer(kind, writer)
                active = asyncio.create_task(self._command(kind))
                try:
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(asyncio.shield(active), timeout=1)
                    self.assertEqual(1, len(calls))
                    self.assertFalse(self.manager._command_locks["mapping_a"].locked())
                    self.assertTrue(active.cancelled())
                    if kind != "notification":
                        summary = self.manager.diagnostic_command_summary("mapping_a")
                        self.assertEqual(1, summary.revision)
                        self.assertEqual(
                            "set_temperature"
                            if kind == "standard"
                            else "set_occupancy_modes",
                            summary.operation,
                        )
                        self.assertIs(
                            CommandStatus.PENDING
                            if kind == "standard"
                            else CommandStatus.SUBMITTED,
                            summary.status,
                        )
                    await asyncio.wait_for(self._command(kind), 1)
                    self.assertEqual(2, len(calls))

                finally:
                    await self._settle_fixture_tasks(active)

    async def test_already_queued_source_events_cannot_revive_stopped_manager(
        self,
    ) -> None:
        self.hass.states.async_set(
            self.homekit.entity_id,
            "heat",
            self._attributes(20.3),
        )
        await self.manager.async_stop()
        with patch(
            "custom_components.ecobee_unified.manager.async_dispatcher_send"
        ) as publish:
            await self.hass.async_block_till_done()
            self.manager.refresh_all()
            publish.assert_not_called()
        self._assert_stopped()

    async def test_native_unload_fences_old_manager_without_waiting_for_writer(
        self,
    ) -> None:
        entry = unified_entry(
            title="Ecobee Unified",
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.source_mapping.as_dict()]},
        )
        entry.add_to_hass(self.hass)
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        manager = entry.runtime_data.manager
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def writer(call: ServiceCall) -> None:
            calls.append(call)
            started.set()
            await release.wait()

        self._register_writer("standard", writer)
        active = asyncio.create_task(self._command("standard", manager))
        queued = None
        unload = None
        try:
            await self._wait_for_fixture_event(active, started)
            queued = asyncio.create_task(self._command("standard", manager))
            await asyncio.sleep(0)
            unload = asyncio.create_task(
                self.hass.config_entries.async_unload(entry.entry_id)
            )
            self.assertTrue(await asyncio.wait_for(asyncio.shield(unload), timeout=1))
            self.assertFalse(active.done())
            with self.assertRaises(ServiceValidationError):
                await asyncio.wait_for(queued, 1)
            self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
            with patch(
                "custom_components.ecobee_unified.manager.async_dispatcher_send"
            ) as publish:
                release.set()
                await asyncio.wait_for(asyncio.shield(active), timeout=1)
                publish.assert_not_called()
            self.assertEqual(1, len(calls))
            self._assert_stopped(manager)
            self.assertIsNot(manager, entry.runtime_data.manager)
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))

        finally:
            release.set()
            await self._settle_fixture_tasks(active, queued, unload)

    async def test_native_local_observations_wait_for_writer_acceptance(self) -> None:
        for operation in ("set_preset_mode", "set_humidity"):
            for unchanged in (False, True):
                for fail in (False, True):
                    with self.subTest(
                        operation=operation, unchanged=unchanged, fail=fail
                    ):
                        await self._new_manager()
                        is_preset = operation == "set_preset_mode"
                        entity_id = (
                            self.homekit_preset.entity_id
                            if is_preset
                            else self.homekit.entity_id
                        )
                        wanted_state = "Away" if is_preset else "heat"
                        initial_state = "Home" if is_preset else "heat"
                        attributes = (
                            {"options": ["Home", "Away"]}
                            if is_preset
                            else self._attributes(20.0)
                            | {"humidity": 40, "supported_features": 389}
                        )
                        initial_attributes = (
                            attributes if is_preset else attributes | {"humidity": 36}
                        )
                        self.hass.states.async_set(
                            entity_id,
                            wanted_state if unchanged else initial_state,
                            attributes if unchanged else initial_attributes,
                        )
                        await self.hass.async_block_till_done()
                        calls = []

                        async def report(
                            call: ServiceCall,
                            *,
                            calls=calls,
                            entity_id=entity_id,
                            wanted_state=wanted_state,
                            attributes=attributes,
                            fail=fail,
                        ) -> None:
                            calls.append(call)
                            self.hass.states.async_set(
                                entity_id, wanted_state, attributes
                            )
                            await self.hass.async_block_till_done()
                            self.assertIs(
                                CommandStatus.PENDING,
                                self.manager.diagnostic_command_summary(
                                    "mapping_a"
                                ).status,
                            )
                            if fail:
                                raise RuntimeError(
                                    "writer failed after its observation"
                                )

                        self.hass.services.async_register(
                            "select" if is_preset else "climate",
                            "select_option" if is_preset else "set_humidity",
                            report,
                        )

                        async def command(is_preset=is_preset) -> None:
                            if is_preset:
                                await self.manager.async_set_preset_mode(
                                    "mapping_a", "Away", None
                                )
                            else:
                                await self.manager.async_standard_command(
                                    "mapping_a",
                                    "set_humidity",
                                    {"humidity": 40},
                                    {"target_humidity": 40},
                                    None,
                                )

                        if fail:
                            with self.assertRaises(HomeAssistantError):
                                await command()
                        else:
                            await command()
                        summary = self.manager.diagnostic_command_summary("mapping_a")
                        self.assertEqual(operation, summary.operation)
                        self.assertIs(
                            CommandStatus.FAILED if fail else CommandStatus.CONFIRMED,
                            summary.status,
                        )
                        self.assertFalse(self.manager._unsub_timeouts)
                        self.assertEqual(1, len(calls))

    async def test_preset_rejects_cloud_confirmation_then_accepts_local_report(
        self,
    ) -> None:
        async def writer(call: ServiceCall) -> None:
            return None

        self._register_writer("preset", writer)
        self.hass.states.async_set(
            self.homekit_preset.entity_id, "Away", {"options": ["Home", "Away"]}
        )
        await self.hass.async_block_till_done()
        await self.manager.async_set_preset_mode("mapping_a", "Away", None)
        state = self.hass.states.get(self.ecobee.entity_id)
        self.hass.states.async_set(self.ecobee.entity_id, state.state, state.attributes)
        await self.hass.async_block_till_done()
        self.assertIs(
            CommandStatus.PENDING, self.manager.snapshot("mapping_a").command.status
        )
        self.hass.states.async_set(
            self.homekit_preset.entity_id, "Away", {"options": ["Home", "Away"]}
        )
        await self.hass.async_block_till_done()
        self.assertIs(
            CommandStatus.CONFIRMED, self.manager.snapshot("mapping_a").command.status
        )
