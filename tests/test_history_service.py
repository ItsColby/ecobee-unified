"""Native authorization, named-script consumption, and historical entry routing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import voluptuous as vol
import yaml
from homeassistant.auth import auth_manager_from_config
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.ecobee_unified.const import DOMAIN
from custom_components.ecobee_unified.history_service import (
    async_register_history_service,
)
from custom_components.ecobee_unified.runtime import EcobeeUnifiedRuntime

from .runtime_fixture import CoreRuntimeTestCase


class HistoryServiceTests(CoreRuntimeTestCase):
    """Use the real admin wrapper and real script integration, without Recorder I/O."""

    def setUp(self) -> None:
        super().setUp()
        path = (
            Path(__file__).resolve().parents[1]
            / "examples/ecobee_daily_history_report.yaml"
        )
        self.script_definition = yaml.safe_load(path.read_text(encoding="utf-8"))

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.hass.auth = await auth_manager_from_config(self.hass, [], [])
        self.admin = MockUser(is_owner=True).add_to_hass(self.hass)
        self.non_admin = MockUser().add_to_hass(self.hass)
        self.entry = MockConfigEntry(domain=DOMAIN, data={})
        self.entry.add_to_hass(self.hass)
        self.entry.mock_state(self.hass, ConfigEntryState.LOADED)
        self.payload = {
            "schema_version": 1,
            "families": [{"family_id": "room_temperature", "days": []}],
        }
        self.history = SimpleNamespace(
            families=(
                SimpleNamespace(
                    family_id="room_temperature",
                    name="Room temperature",
                    quantity="temperature",
                    unit="°C",
                    timezone="UTC",
                    policy="fixed_source",
                    sources=({},),
                ),
            ),
            async_read=AsyncMock(return_value=self.payload),
        )
        self.entry.runtime_data = EcobeeUnifiedRuntime(
            self.manager, history=self.history
        )
        async_register_history_service(self.hass)
        self.request = {
            "config_entry_id": self.entry.entry_id,
            "start_date": "2025-05-01",
            "end_date": "2025-05-02",
        }

    async def _call(self, data=None, context=None):
        return await self.hass.services.async_call(
            DOMAIN,
            "get_daily_history",
            self.request if data is None else data,
            context=context,
            blocking=True,
            return_response=True,
        )

    async def test_native_admin_context_and_default_families(self) -> None:
        context = Context(user_id=self.admin.id)
        self.assertEqual(self.payload, await self._call(context=context))
        kwargs = self.history.async_read.await_args.kwargs
        self.assertIs(context, kwargs["context"])
        self.assertEqual(["room_temperature"], kwargs["family_ids"])
        self.assertFalse(kwargs["include_provisional"])

    async def test_non_admin_rejected_before_reader(self) -> None:
        with self.assertRaises(Unauthorized):
            await self._call(context=Context(user_id=self.non_admin.id))
        self.history.async_read.assert_not_awaited()

    async def test_configuration_discovery_needs_admin_and_no_statistics_read(
        self,
    ) -> None:
        response = await self.hass.services.async_call(
            DOMAIN,
            "get_historical_configuration",
            {"config_entry_id": self.entry.entry_id},
            context=Context(user_id=self.admin.id),
            blocking=True,
            return_response=True,
        )
        self.assertEqual("room_temperature", response["families"][0]["family_id"])
        self.assertEqual("configured", response["families"][0]["equivalent_selection"])
        with self.assertRaises(Unauthorized):
            await self.hass.services.async_call(
                DOMAIN,
                "get_historical_configuration",
                {"config_entry_id": self.entry.entry_id},
                context=Context(user_id=self.non_admin.id),
                blocking=True,
                return_response=True,
            )
        self.history.async_read.assert_not_awaited()

    async def test_unloaded_foreign_and_missing_entry_rejected(self) -> None:
        self.entry.mock_state(self.hass, ConfigEntryState.NOT_LOADED)
        with self.assertRaises(ServiceValidationError):
            await self._call()
        foreign = MockConfigEntry(domain="sensor")
        foreign.add_to_hass(self.hass)
        foreign.mock_state(self.hass, ConfigEntryState.LOADED)
        foreign.runtime_data = self.entry.runtime_data
        for entry_id in (foreign.entry_id, "missing"):
            with (
                self.subTest(entry=entry_id),
                self.assertRaises(ServiceValidationError),
            ):
                await self._call({**self.request, "config_entry_id": entry_id})
        self.history.async_read.assert_not_awaited()

    async def test_explicit_empty_unknown_fields_and_no_response_rejected(self) -> None:
        for extra in ({"family_ids": []}, {"period": "hour"}, {"statistic_ids": []}):
            with self.subTest(extra=extra), self.assertRaises(vol.Invalid):
                await self._call({**self.request, **extra})
        with self.assertRaises(HomeAssistantError):
            await self.hass.services.async_call(
                DOMAIN, "get_daily_history", self.request, blocking=True
            )
        self.history.async_read.assert_not_awaited()

    async def test_real_named_script_returns_response_and_preserves_user(self) -> None:
        self.assertTrue(
            await async_setup_component(
                self.hass,
                "script",
                {"script": {"ecobee_daily_history_report": self.script_definition}},
            )
        )
        for families in (None, ["room_temperature"]):
            data = dict(self.request)
            if families is not None:
                data["family_ids"] = families
            with self.subTest(families=families):
                response = await self.hass.services.async_call(
                    "script",
                    "ecobee_daily_history_report",
                    data,
                    context=Context(user_id=self.admin.id),
                    blocking=True,
                    return_response=True,
                )
                self.assertEqual(self.payload, response)
                kwargs = self.history.async_read.await_args.kwargs
                self.assertEqual(self.admin.id, kwargs["context"].user_id)
                self.assertEqual(["room_temperature"], kwargs["family_ids"])
        calls_before = self.history.async_read.await_count
        with self.assertRaises(Unauthorized):
            await self.hass.services.async_call(
                "script",
                "ecobee_daily_history_report",
                self.request,
                context=Context(user_id=self.non_admin.id),
                blocking=True,
                return_response=True,
            )
        self.assertEqual(calls_before, self.history.async_read.await_count)
