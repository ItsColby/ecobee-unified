"""Native weather platform, cached forecast reads, and subscription lifecycle."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.components.weather import Forecast, WeatherEntity
from homeassistant.components.weather.const import DATA_COMPONENT, WeatherEntityFeature
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.const import CONF_MAPPINGS, DOMAIN
from custom_components.ecobee_unified.datapoints import DatapointConfig, SourceBinding
from custom_components.ecobee_unified.weather import EcobeeUnifiedWeather

from .test_weather_source import WeatherSourceFixture


class NativeWeatherFixture(WeatherEntity):
    """A native source's public forecast subscription surface, without transport."""

    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY
    _attr_native_temperature_unit = "°F"
    _attr_native_pressure_unit = "inHg"
    _attr_native_wind_speed_unit = "mph"
    _attr_native_precipitation_unit = "in"

    async def async_forecast_daily(self) -> list[Forecast]:
        return [{"datetime": "2024-01-03T12:00:00+00:00", "native_temperature": 75}]


class WeatherPlatformTests(WeatherSourceFixture):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.manager.async_stop()
        self.provider_time = dt_util.utcnow().replace(microsecond=0) - timedelta(
            minutes=2
        )
        self.attribution = (
            "Ecobee weather provided by STATION-A at "
            f"{self.provider_time:%Y-%m-%d %H:%M:%S} UTC"
        )
        self.source_attributes = dict(self._state().attributes) | {
            "attribution": self.attribution,
        }
        for source in (self.first, self.second):
            self.hass.states.async_set(
                source.entity_id, "sunny", self.source_attributes
            )
        self.config = DatapointConfig(
            "weather_identity",
            "Shared weather",
            "weather",
            (SourceBinding(self.first.id), SourceBinding(self.second.id)),
            weather_station="STATION-A",
            weather_config_entry_id=self.native_entry.entry_id,
            max_age_seconds=3600,
        )
        self.unified_entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            version=1,
            minor_version=4,
            data={
                CONF_MAPPINGS: [self.mapping.as_dict()],
                "datapoints": [self.config.as_dict()],
            },
        )
        self.unified_entry.add_to_hass(self.hass)
        self.assertTrue(
            await self.hass.config_entries.async_setup(self.unified_entry.entry_id)
        )
        await self.hass.async_block_till_done()
        self.weather_manager = self.unified_entry.runtime_data.datapoints
        self.entity_id = self.registry.async_get_entity_id(
            "weather", DOMAIN, self.weather_manager.unique_id(self.config.datapoint_id)
        )
        assert self.entity_id is not None
        self.component = self.hass.data[DATA_COMPONENT]
        self.entity = self.component.get_entity(self.entity_id)
        assert isinstance(self.entity, EcobeeUnifiedWeather)
        self.rows: Any = [
            {
                "datetime": "2024-01-03T12:00:00.000455+00:00",
                "condition": "sunny",
                "temperature": 75,
                "templow": 52,
                "humidity": 70,
                "wind_bearing": 196,
                "wind_speed": 4,
                "precipitation": 0.2,
                "precipitation_probability": 15,
            }
        ]
        self.calls: list[dict[str, Any]] = []
        self.request_started: asyncio.Event | None = None
        self.release_request: asyncio.Event | None = None
        self.service_error: Exception | None = None
        self.hass.services.async_register(
            "weather",
            "get_forecasts",
            self._read_forecast,
            supports_response=SupportsResponse.ONLY,
        )

    async def _read_forecast(self, call: ServiceCall) -> dict[str, Any]:
        self.calls.append(dict(call.data))
        if self.request_started is not None:
            self.request_started.set()
        if self.release_request is not None:
            await self.release_request.wait()
        if self.service_error is not None:
            raise self.service_error
        return {call.data["entity_id"]: {"forecast": deepcopy(self.rows)}}

    async def test_native_setup_current_fields_reload_retention_and_unload(
        self,
    ) -> None:
        state = self.hass.states.get(self.entity_id)
        assert state is not None
        self.assertEqual("sunny", state.state)
        self.assertEqual(self.first.entity_id, state.attributes["source"])
        self.assertEqual(72, self.entity.native_temperature)
        self.assertEqual(30.27, self.entity.native_pressure)
        self.assertEqual(80, self.entity.humidity)
        self.assertEqual(196, self.entity.wind_bearing)
        self.assertEqual(3, self.entity.native_wind_speed)
        self.assertEqual(12.43, self.entity.native_visibility)
        self.assertEqual("°F", self.entity.native_temperature_unit)
        self.assertEqual("inHg", self.entity.native_pressure_unit)
        self.assertEqual("mph", self.entity.native_wind_speed_unit)
        self.assertEqual("mi", self.entity.native_visibility_unit)
        self.assertEqual(self.provider_time, state.attributes["provider_reported_at"])
        self.assertIsNone(self.registry.async_get(self.entity_id).device_id)
        self.assertIsNone(
            self.registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                self.weather_manager.unique_id(self.config.datapoint_id),
            )
        )
        self.assertIsNotNone(self.weather_manager._timer)
        old_manager = self.weather_manager
        old_entity = self.entity
        self.assertTrue(
            await self.hass.config_entries.async_reload(self.unified_entry.entry_id)
        )
        await self.hass.async_block_till_done()
        self.assertTrue(old_entity._unloaded)
        self.assertFalse(old_manager._running)
        self.assertEqual([], old_manager._unsub)
        self.assertEqual([], old_manager._state_unsub)
        self.assertIsNone(old_manager._timer)
        self.assertIsNotNone(self.hass.states.get(self.entity_id))
        self.assertEqual(
            self.entity_id,
            self.registry.async_get_entity_id(
                "weather", DOMAIN, old_manager.unique_id(self.config.datapoint_id)
            ),
        )
        self.assertTrue(
            await self.hass.config_entries.async_unload(self.unified_entry.entry_id)
        )
        self.assertIsNotNone(self.registry.async_get(self.entity_id))
        self.hass.config_entries.async_update_entry(
            self.unified_entry,
            data={CONF_MAPPINGS: [self.mapping.as_dict()], "datapoints": []},
        )
        self.assertTrue(
            await self.hass.config_entries.async_setup(self.unified_entry.entry_id)
        )
        await self.hass.async_block_till_done()
        self.assertIsNone(self.registry.async_get(self.entity_id))
        self.assertIsNotNone(self.registry.async_get(self.first.entity_id))
        self.assertIsNotNone(
            self.registry.async_get_entity_id(
                "climate", DOMAIN, self.mapping.mapping_id
            )
        )

    async def test_forecast_uses_selected_native_read_and_preserves_source_timestamp(
        self,
    ) -> None:
        original = deepcopy(self.rows)
        forecast = await self.entity.async_forecast_daily()
        assert forecast is not None
        self.assertEqual(
            [{"entity_id": self.first.entity_id, "type": "daily"}], self.calls
        )
        self.assertEqual(original, self.rows)
        self.assertEqual(original[0]["datetime"], forecast[0]["datetime"])
        self.assertEqual(75, forecast[0]["native_temperature"])
        self.assertEqual(52, forecast[0]["native_templow"])
        self.assertEqual(4, forecast[0]["native_wind_speed"])
        self.assertEqual(0.2, forecast[0]["native_precipitation"])
        self.assertNotIn("temperature", forecast[0])
        converted = self.entity._convert_forecast(forecast)
        self.assertAlmostEqual(23.9, converted[0]["temperature"], places=1)
        self.hass.states.async_set(self.first.entity_id, "unavailable")
        await self.hass.async_block_till_done()
        self.assertIsNotNone(await self.entity.async_forecast_daily())
        self.assertEqual(self.second.entity_id, self.calls[-1]["entity_id"])

    async def test_recorder_excludes_receipt_but_retains_weather_changes(self) -> None:
        before = self.hass.states.get(self.entity_id)
        assert before is not None
        self.hass.states.async_set(
            self.first.entity_id, "sunny", self.source_attributes
        )
        await self.hass.async_block_till_done()
        repeated = self.hass.states.get(self.entity_id)
        assert repeated is not None
        self.assertEqual(before.state, repeated.state)
        self.assertEqual(
            {"ha_reported_at"},
            {
                key
                for key in before.attributes.keys() | repeated.attributes.keys()
                if before.attributes.get(key) != repeated.attributes.get(key)
            },
        )
        self.assertEqual(
            self.provider_time, repeated.attributes["provider_reported_at"]
        )
        assert repeated.state_info is not None
        self.assertIn("ha_reported_at", repeated.state_info["unrecorded_attributes"])
        self.assertNotIn(
            "provider_reported_at", repeated.state_info["unrecorded_attributes"]
        )

        provider_time = self.provider_time + timedelta(seconds=30)
        self.hass.states.async_set(
            self.first.entity_id,
            "rainy",
            self.source_attributes
            | {
                "attribution": "Ecobee weather provided by STATION-A at "
                f"{provider_time:%Y-%m-%d %H:%M:%S} UTC",
            },
        )
        await self.hass.async_block_till_done()
        changed = self.hass.states.get(self.entity_id)
        assert changed is not None
        self.assertEqual("rainy", changed.state)
        self.assertNotEqual(repeated.state, changed.state)
        self.assertEqual(provider_time, changed.attributes["provider_reported_at"])
        assert changed.state_info is not None
        self.assertIn("ha_reported_at", changed.attributes)
        self.assertIn("ha_reported_at", changed.state_info["unrecorded_attributes"])
        self.assertNotIn(
            "provider_reported_at", changed.state_info["unrecorded_attributes"]
        )

    async def test_forecast_rejects_malformed_data_and_read_failure(self) -> None:
        for rows in (
            None,
            [{"datetime": "no timestamp", "temperature": 75}],
            [{"datetime": "2024-01-03T12:00:00", "temperature": 75}],
            [dict(self.rows[0], temperature=-500)],
            [dict(self.rows[0], temperature=10**500)],
            [dict(self.rows[0], humidity=101)],
            [dict(self.rows[0], wind_speed=float("nan"))],
            [dict(self.rows[0], precipitation=-1)],
        ):
            with self.subTest(rows=rows):
                self.rows = rows
                self.assertIsNone(await self.entity.async_forecast_daily())
        self.service_error = HomeAssistantError("native read failed")
        self.assertIsNone(await self.entity.async_forecast_daily())
        self.service_error = None
        self.hass.states.async_set(
            self.first.entity_id,
            "sunny",
            self.source_attributes | {"supported_features": 0},
        )
        await self.hass.async_block_till_done()
        before = len(self.calls)
        self.assertIsNone(await self.entity.async_forecast_daily())
        self.assertEqual(before, len(self.calls))

    async def test_forecast_fences_alias_provider_units_and_station_during_await(
        self,
    ) -> None:
        mutations = (
            ("unavailable", {}),
            (
                "sunny",
                {"attribution": self.attribution.replace("STATION-A", "STATION-B")},
            ),
            (
                "sunny",
                {
                    "attribution": (
                        "Ecobee weather provided by STATION-A at "
                        f"{self.provider_time + timedelta(seconds=1):%Y-%m-%d %H:%M:%S} UTC"
                    )
                },
            ),
            ("sunny", {"temperature_unit": "°C", "temperature": 22}),
        )
        for condition, attrs in mutations:
            with self.subTest(attrs=attrs, condition=condition):
                self.hass.states.async_set(
                    self.first.entity_id, "sunny", self.source_attributes
                )
                await self.hass.async_block_till_done()
                self.request_started = asyncio.Event()
                self.release_request = asyncio.Event()
                task = asyncio.create_task(self.entity.async_forecast_daily())
                await self.request_started.wait()
                self.hass.states.async_set(
                    self.first.entity_id, condition, self.source_attributes | attrs
                )
                # Deliver Core state/dispatcher callbacks while the source service waits.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.release_request.set()
                self.assertIsNone(await task)
                self.request_started = self.release_request = None
                await self.hass.async_block_till_done()

    async def test_public_forecast_callbacks_follow_selection_and_stop_on_unload(
        self,
    ) -> None:
        natives = {}
        for source in (self.first, self.second):
            native = NativeWeatherFixture()
            native.hass = self.hass
            native.entity_id = source.entity_id
            natives[source.entity_id] = native
        original_get = self.component.get_entity
        updates = []
        with patch.object(
            self.component,
            "get_entity",
            side_effect=lambda key: natives.get(key) or original_get(key),
        ):
            unsubscribe = self.entity.async_subscribe_forecast("daily", updates.append)
            self.assertEqual(
                1, len(natives[self.first.entity_id]._forecast_listeners["daily"])
            )
            await natives[self.first.entity_id].async_update_listeners(("daily",))
            await self.hass.async_block_till_done()
            self.assertEqual(1, len(updates))
            self.assertEqual(self.rows[0]["datetime"], updates[0][0]["datetime"])
            self.hass.states.async_set(self.first.entity_id, "unavailable")
            await self.hass.async_block_till_done()
            self.assertEqual(
                [], natives[self.first.entity_id]._forecast_listeners["daily"]
            )
            self.assertEqual(
                1, len(natives[self.second.entity_id]._forecast_listeners["daily"])
            )
            self.assertEqual(self.second.entity_id, self.calls[-1]["entity_id"])
            unsubscribe()
            self.assertEqual(
                [], natives[self.second.entity_id]._forecast_listeners["daily"]
            )
            self.entity.async_subscribe_forecast("daily", updates.append)
            self.assertTrue(
                await self.hass.config_entries.async_unload(self.unified_entry.entry_id)
            )
            self.assertEqual(
                [], natives[self.second.entity_id]._forecast_listeners["daily"]
            )
            self.assertIsNone(self.entity._refresh_task)
            self.assertIsNone(self.entity._forecast_unsub)
            before = len(self.calls)
            await natives[self.second.entity_id].async_update_listeners(("daily",))
            await self.hass.async_block_till_done()
            self.assertEqual(before, len(self.calls))

    async def test_forecast_rejects_transient_selection_change_but_accepts_repeated_receipt(
        self,
    ) -> None:
        for transient_change in (True, False):
            with self.subTest(transient_change=transient_change):
                self.request_started = asyncio.Event()
                self.release_request = asyncio.Event()
                task = asyncio.create_task(self.entity.async_forecast_daily())
                await self.request_started.wait()
                if transient_change:
                    self.hass.states.async_set(self.first.entity_id, "unavailable")
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                self.hass.states.async_set(
                    self.first.entity_id,
                    "sunny",
                    self.source_attributes,
                    force_update=True,
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.release_request.set()
                if transient_change:
                    self.assertIsNone(await task)
                else:
                    self.assertIsNotNone(await task)
                self.request_started = self.release_request = None
                await self.hass.async_block_till_done()

    async def test_unload_cancels_owned_forecast_publication_in_flight(self) -> None:
        updates = []
        self.entity.async_subscribe_forecast("daily", updates.append)
        self.request_started = asyncio.Event()
        self.release_request = asyncio.Event()
        self.hass.states.async_set(self.first.entity_id, "unavailable")
        await self.request_started.wait()
        self.assertIsNotNone(self.entity._refresh_task)
        try:
            self.assertTrue(
                await self.hass.config_entries.async_unload(self.unified_entry.entry_id)
            )
            self.assertIsNone(self.entity._refresh_task)
            self.assertTrue(self.entity._unloaded)
            self.assertEqual([], updates)
        finally:
            self.release_request.set()
        await self.hass.async_block_till_done()
