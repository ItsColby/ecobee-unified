"""A precise HomeKit sensor that stops answering its own climate characteristic."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from time import time
from unittest.mock import Mock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.ecobee_unified.const import (
    CONF_RELOAD_SILENT_TEMPERATURE_SOURCE,
    DOMAIN,
    SILENT_TEMPERATURE_RELOAD_COOLDOWN_SECONDS,
)
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import SourceHealth
from custom_components.ecobee_unified.repairs import (
    ReloadSilentTemperatureSourceFlow,
    async_create_fix_flow,
)

from .runtime_fixture import CoreRuntimeTestCase

PRECISE_ATTRIBUTES = {
    ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT: "°C",
}


class TemperatureSilenceRuntimeTests(CoreRuntimeTestCase):
    async def _start(
        self, mapping_id: str, options: dict[str, object] | None = None
    ) -> MappingManager:
        self.silence_mapping = replace(
            self.mapping,
            mapping_id=mapping_id,
            homekit_temperature_entity=self.homekit_temperature.id,
        )
        # Place the last precise report an hour ago so climate changes can be
        # spread over minutes, as a real frozen sensor's evidence is.
        self.base = time() - 3600
        self.hass.states.async_set(
            self.homekit_temperature.entity_id,
            "20.04",
            PRECISE_ATTRIBUTES,
            timestamp=self.base,
        )
        manager = MappingManager(
            self.hass, mapping_id, (self.silence_mapping,), options or {}
        )
        await manager.async_start()
        return manager

    async def _climate(
        self, manager: MappingManager, temperature: float, minutes: float
    ) -> None:
        self.hass.states.async_set(
            self.homekit.entity_id,
            "heat",
            self._attributes(temperature),
            timestamp=self.base + minutes * 60,
        )
        await self.hass.async_block_till_done()
        manager._handle_homekit_settle(self.silence_mapping.mapping_id)

    async def _precise(self, manager: MappingManager, state: str) -> None:
        self.hass.states.async_set(
            self.homekit_temperature.entity_id, state, PRECISE_ATTRIBUTES
        )
        await self.hass.async_block_till_done()
        manager._handle_homekit_settle(self.silence_mapping.mapping_id)

    def _issue(self) -> ir.IssueEntry | None:
        return ir.async_get(self.hass).async_get_issue(
            DOMAIN,
            f"homekit_temperature_silent_{self.silence_mapping.mapping_id}",
        )

    async def test_frozen_sensor_becomes_silent_then_recovers_on_report(self) -> None:
        """Two unanswered climate changes replace divergence with silence."""

        manager = await self._start("temperature_silent")
        mapping_id = self.silence_mapping.mapping_id
        try:
            with (
                patch(
                    "custom_components.ecobee_unified.manager.async_call_later",
                    return_value=Mock(),
                ),
                patch.object(
                    self.hass.config_entries, "async_schedule_reload"
                ) as schedule_reload,
            ):
                await self._climate(manager, 21.0, 5)
                diverged = manager.snapshot(mapping_id)
                self.assertIn("homekit_temperature_diverged", diverged.degradation)
                self.assertIn(
                    "homekit_temperature_recovery_pending", diverged.degradation
                )
                self.assertIsNone(self._issue())

                await self._climate(manager, 22.0, 20)
                silent = manager.snapshot(mapping_id)
                self.assertIn("homekit_temperature_silent", silent.degradation)
                self.assertNotIn("homekit_temperature_diverged", silent.degradation)
                self.assertNotIn(
                    "homekit_temperature_recovery_pending", silent.degradation
                )
                self.assertIs(
                    SourceHealth.STALE, silent.source_health["homekit_temperature"]
                )
                self.assertEqual(22.0, silent.current_temperature)
                self.assertEqual("homekit", silent.provenance["current_temperature"])
                issue = self._issue()
                assert issue is not None
                self.assertTrue(issue.is_fixable)
                self.assertEqual("homekit_temperature_silent", issue.translation_key)
                self.assertEqual(
                    {"source_entry_id": self.homekit.config_entry_id}, issue.data
                )
                self.assertEqual(
                    {"mapping": self.silence_mapping.name},
                    issue.translation_placeholders,
                )
                schedule_reload.assert_not_called()

                # A re-report of the frozen value ends silence but not rejection.
                await self._precise(manager, "20.04")
                reported = manager.snapshot(mapping_id)
                self.assertNotIn("homekit_temperature_silent", reported.degradation)
                self.assertIn(
                    "homekit_temperature_recovery_pending", reported.degradation
                )
                self.assertEqual("homekit", reported.provenance["current_temperature"])
                self.assertIsNone(self._issue())

                await self._precise(manager, "22.02")
                recovered = manager.snapshot(mapping_id)
                self.assertEqual(22.02, recovered.current_temperature)
                self.assertEqual(
                    "homekit_temperature", recovered.provenance["current_temperature"]
                )
                self.assertFalse(
                    {
                        "homekit_temperature_silent",
                        "homekit_temperature_recovery_pending",
                    }
                    & set(recovered.degradation)
                )
                self.assertIsNone(self._issue())
        finally:
            await manager.async_stop()

    async def test_answered_climate_changes_are_not_silence(self) -> None:
        """Ordinary paired reports never accumulate silence evidence."""

        manager = await self._start("temperature_answered")
        try:
            with patch(
                "custom_components.ecobee_unified.manager.async_call_later",
                return_value=Mock(),
            ):
                for climate, precise in (
                    (21.0, "21.04"),
                    (22.0, "21.96"),
                    (23.0, "23"),
                ):
                    self.hass.states.async_set(
                        self.homekit.entity_id, "heat", self._attributes(climate)
                    )
                    self.hass.states.async_set(
                        self.homekit_temperature.entity_id,
                        precise,
                        PRECISE_ATTRIBUTES,
                    )
                    await self.hass.async_block_till_done()
                    manager._handle_homekit_settle(self.silence_mapping.mapping_id)
                    snapshot = manager.snapshot(self.silence_mapping.mapping_id)
                    self.assertNotIn("homekit_temperature_silent", snapshot.degradation)
                    self.assertEqual(
                        "homekit_temperature",
                        snapshot.provenance["current_temperature"],
                    )
                self.assertIsNone(self._issue())
        finally:
            await manager.async_stop()

    async def test_opt_in_reload_is_bounded_and_escalates_to_repair(self) -> None:
        """Reload once, then require fresh evidence and respect the cooldown."""

        manager = await self._start(
            "temperature_reload", {CONF_RELOAD_SILENT_TEMPERATURE_SOURCE: True}
        )
        source_entry_id = self.homekit.config_entry_id
        assert source_entry_id is not None
        try:
            with (
                patch(
                    "custom_components.ecobee_unified.manager.async_call_later",
                    return_value=Mock(),
                ),
                patch.object(
                    self.hass.config_entries, "async_schedule_reload"
                ) as schedule_reload,
            ):
                await self._climate(manager, 21.0, 5)
                await self._climate(manager, 22.0, 20)
                schedule_reload.assert_called_once_with(source_entry_id)
                self.assertIsNone(self._issue())

                # Evidence was reset: one further change is not yet silence.
                await self._climate(manager, 23.0, 30)
                self.assertNotIn(
                    "homekit_temperature_silent",
                    manager.snapshot(self.silence_mapping.mapping_id).degradation,
                )
                await self._climate(manager, 24.0, 40)
                self.assertEqual(1, schedule_reload.call_count)
                issue = self._issue()
                assert issue is not None
                self.assertTrue(issue.is_fixable)

                manager._silent_source_reloads[source_entry_id] = (
                    dt_util.utcnow()
                    - timedelta(seconds=SILENT_TEMPERATURE_RELOAD_COOLDOWN_SECONDS)
                )
                await self._climate(manager, 25.0, 50)
                self.assertEqual(2, schedule_reload.call_count)
                self.assertIsNone(self._issue())
        finally:
            await manager.async_stop()

    async def test_opt_in_reload_waits_for_a_pending_command(self) -> None:
        """A reload never interrupts an observation the tracker still owns."""

        manager = await self._start(
            "temperature_pending", {CONF_RELOAD_SILENT_TEMPERATURE_SOURCE: True}
        )
        try:
            with (
                patch(
                    "custom_components.ecobee_unified.manager.async_call_later",
                    return_value=Mock(),
                ),
                patch.object(
                    self.hass.config_entries, "async_schedule_reload"
                ) as schedule_reload,
                patch.object(
                    manager._tracker, "pending_operation", return_value="set_hvac_mode"
                ),
            ):
                await self._climate(manager, 21.0, 5)
                await self._climate(manager, 22.0, 20)
                schedule_reload.assert_not_called()
                self.assertIsNotNone(self._issue())
        finally:
            await manager.async_stop()
        self.assertIsNone(self._issue())

    async def test_unowned_pairing_raises_manual_repair(self) -> None:
        """Without one HomeKit entry owning both entities, nothing is reloaded."""

        manager = await self._start(
            "temperature_unowned", {CONF_RELOAD_SILENT_TEMPERATURE_SOURCE: True}
        )
        try:
            with (
                patch(
                    "custom_components.ecobee_unified.manager.async_call_later",
                    return_value=Mock(),
                ),
                patch.object(
                    self.hass.config_entries, "async_schedule_reload"
                ) as schedule_reload,
                patch.object(
                    manager, "_temperature_source_entry_id", return_value=None
                ),
            ):
                await self._climate(manager, 21.0, 5)
                await self._climate(manager, 22.0, 20)
                schedule_reload.assert_not_called()
                issue = self._issue()
                assert issue is not None
                self.assertFalse(issue.is_fixable)
                self.assertEqual(
                    "homekit_temperature_silent_manual", issue.translation_key
                )
                self.assertIsNone(issue.data)
        finally:
            await manager.async_stop()

    async def test_fix_flow_reloads_only_the_named_homekit_entry(self) -> None:
        source_entry_id = self.homekit.config_entry_id
        assert source_entry_id is not None
        issue_id = "homekit_temperature_silent_mapping_a"
        flow = await async_create_fix_flow(
            self.hass, issue_id, {"source_entry_id": source_entry_id}
        )
        self.assertIsInstance(flow, ReloadSilentTemperatureSourceFlow)
        flow.hass = self.hass
        form = await flow.async_step_init()
        self.assertIs(FlowResultType.FORM, form["type"])
        self.assertEqual("confirm", form["step_id"])

        with patch.object(
            self.hass.config_entries, "async_reload", return_value=True
        ) as reload:
            done = await flow.async_step_confirm({})
        reload.assert_awaited_once_with(source_entry_id)
        self.assertIs(FlowResultType.CREATE_ENTRY, done["type"])

        with patch.object(self.hass.config_entries, "async_reload", return_value=False):
            failed = await flow.async_step_confirm({})
        self.assertEqual("source_reload_failed", failed["reason"])

        ecobee_entry_id = self.ecobee.config_entry_id
        assert ecobee_entry_id is not None
        wrong = await async_create_fix_flow(
            self.hass, issue_id, {"source_entry_id": ecobee_entry_id}
        )
        wrong.hass = self.hass
        with patch.object(self.hass.config_entries, "async_reload") as reload:
            aborted = await wrong.async_step_init()
        reload.assert_not_called()
        self.assertEqual("source_entry_missing", aborted["reason"])

        for bad_issue, data in (
            ("mapping_mapping_a", {"source_entry_id": source_entry_id}),
            (issue_id, None),
            (issue_id, {"source_entry_id": 3}),
        ):
            with (
                self.subTest(issue=bad_issue, data=data),
                self.assertRaises(ValueError),
            ):
                await async_create_fix_flow(self.hass, bad_issue, data)
