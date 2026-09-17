"""Native registry/state contracts for current Ecobee weather aliases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.weather_source import (
    validate_weather_feed,
    weather_observation,
    weather_snapshot,
)

from .runtime_fixture import CoreRuntimeTestCase


class WeatherSourceFixture(CoreRuntimeTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.provider_time = datetime(2024, 1, 2, 10, 11, 12, tzinfo=UTC)
        self.receipt_time = self.provider_time + timedelta(minutes=2)
        self.now = self.provider_time + timedelta(minutes=20)
        self.registry = er.async_get(self.hass)
        source_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert source_entry is not None
        self.native_entry = source_entry
        second_device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=source_entry.entry_id,
            identifiers={("ecobee", "thermostat_b")},
        )
        self.first = self.registry.async_get_or_create(
            "weather",
            "ecobee",
            "thermostat_a",
            config_entry=source_entry,
            device_id=self.ecobee.device_id,
        )
        self.second = self.registry.async_get_or_create(
            "weather",
            "ecobee",
            "thermostat_b",
            config_entry=source_entry,
            device_id=second_device.id,
        )

    def _state(
        self,
        entry: er.RegistryEntry | None = None,
        attributes: dict[str, Any] | None = None,
        *,
        condition: str = "sunny",
        receipt: datetime | None = None,
    ) -> State:
        return State(
            (entry or self.first).entity_id,
            condition,
            {
                "temperature": 72,
                "temperature_unit": "°F",
                "humidity": 80,
                "pressure": 30.27,
                "pressure_unit": "inHg",
                "wind_bearing": 196,
                "wind_speed": 3,
                "wind_speed_unit": "mph",
                "visibility": 12.43,
                "visibility_unit": "mi",
                "precipitation_unit": "in",
                "supported_features": 1,
                "unit_of_measurement": "not a weather unit",
                "attribution": "Ecobee weather provided by STATION-A at 2024-01-02 10:11:12 UTC",
            }
            | (attributes or {}),
            last_updated=receipt or self.receipt_time,
            last_reported=receipt or self.receipt_time,
        )

    def _read(self, role: str, **kwargs: Any) -> Any:
        return weather_observation(
            kwargs.pop("entry", self.first),
            kwargs.pop("state", self._state()),
            role,
            expected_station=kwargs.pop("expected_station", "STATION-A"),
            expected_config_entry_id=kwargs.pop(
                "expected_config_entry_id", self.native_entry.entry_id
            ),
            now=kwargs.pop("now", self.now),
            **kwargs,
        )


class WeatherSourceTests(WeatherSourceFixture):
    async def test_complete_weather_snapshot_preserves_one_report_and_absent_fields(
        self,
    ) -> None:
        state = self._state(attributes={"visibility": None}, condition="unknown")
        snapshot = weather_snapshot(
            self.first,
            state,
            expected_station="STATION-A",
            expected_config_entry_id=self.native_entry.entry_id,
            now=self.now,
        )
        self.assertIsNone(snapshot.condition)
        self.assertIsNone(snapshot.visibility)
        self.assertEqual(72, snapshot.temperature)
        self.assertEqual("°F", snapshot.temperature_unit)
        self.assertEqual("in", snapshot.precipitation_unit)
        self.assertEqual(1, snapshot.supported_features)
        self.assertEqual(self.provider_time, snapshot.provider_reported_at)
        self.assertEqual(self.receipt_time, snapshot.ha_reported_at)
        for attrs, reason in (
            ({"humidity": 101}, "weather_value_invalid"),
            ({"precipitation_unit": []}, "weather_unit_invalid"),
            ({"supported_features": True}, "weather_source_invalid"),
        ):
            with self.subTest(attrs=attrs), self.assertRaisesRegex(ValueError, reason):
                weather_snapshot(
                    self.first,
                    self._state(attributes=attrs),
                    expected_station="STATION-A",
                    expected_config_entry_id=self.native_entry.entry_id,
                    now=self.now,
                )

    async def test_seven_current_roles_preserve_native_units_and_distinct_clocks(
        self,
    ) -> None:
        expected = {
            "condition": ("sunny", None),
            "temperature": (72, "°F"),
            "humidity": (80, "%"),
            "pressure": (30.27, "inHg"),
            "wind_bearing": (196, "°"),
            "wind_speed": (3, "mph"),
            "visibility": (12.43, "mi"),
        }
        for role, value in expected.items():
            with self.subTest(role=role):
                reading = self._read(role)
                self.assertEqual(value, (reading.value, reading.unit))
                self.assertEqual("STATION-A", reading.station)
                self.assertEqual(self.provider_time, reading.provider_reported_at)
                self.assertEqual(self.receipt_time, reading.ha_reported_at)
        self.assertAlmostEqual(
            22.2222222222, self._read("temperature", output_unit="°C").value
        )
        repeated = self._read("temperature", state=self._state(receipt=self.now))
        self.assertEqual(self.provider_time, repeated.provider_reported_at)
        self.assertEqual(self.now, repeated.ha_reported_at)

    async def test_feed_identity_pairs_distinct_thermostats_only_with_same_native_entry_and_station(
        self,
    ) -> None:
        self.assertNotEqual(self.first.device_id, self.second.device_id)
        identity = validate_weather_feed(
            [(self.first, self._state()), (self.second, self._state(self.second))],
            role="temperature",
            output_unit="°C",
            now=self.now,
        )
        self.assertEqual(self.native_entry.entry_id, identity.config_entry_id)
        self.assertEqual("STATION-A", identity.station)
        other_entry = MockConfigEntry(domain="ecobee")
        other_entry.add_to_hass(self.hass)
        other = self.registry.async_get_or_create(
            "weather",
            "ecobee",
            "another_account",
            config_entry=other_entry,
            device_id=self.second.device_id,
        )
        with self.assertRaisesRegex(ValueError, "weather_config_entry_mismatch"):
            validate_weather_feed(
                [(self.first, self._state()), (other, self._state(other))],
                now=self.now,
            )
        with self.assertRaisesRegex(ValueError, "weather_source_duplicate"):
            validate_weather_feed([(self.first, self._state())] * 2, now=self.now)

    async def test_station_or_entry_drift_fails_each_read_and_does_not_rebind_together(
        self,
    ) -> None:
        drifted = self._state(
            attributes={
                "attribution": "Ecobee weather provided by STATION-B at 2024-01-02 10:11:12 UTC",
            }
        )
        with self.assertRaisesRegex(ValueError, "weather_station_mismatch"):
            self._read("temperature", state=drifted)
        with self.assertRaisesRegex(ValueError, "weather_config_entry_mismatch"):
            self._read("temperature", expected_config_entry_id="different_saved_entry")
        with self.assertRaisesRegex(ValueError, "weather_station_mismatch"):
            validate_weather_feed(
                [
                    (self.first, drifted),
                    (self.second, self._state(self.second, dict(drifted.attributes))),
                ],
                expected_station="STATION-A",
                now=self.now,
            )
        # A healthy second binding still qualifies independently for parent fallback.
        self.assertEqual(
            72,
            self._read(
                "temperature", entry=self.second, state=self._state(self.second)
            ).value,
        )

    async def test_missing_unknown_malformed_and_future_provider_metadata_fail_closed(
        self,
    ) -> None:
        cases = (
            (None, "weather_attribution_invalid"),
            (
                "Other provider at 2024-01-02 10:11:12 UTC",
                "weather_attribution_invalid",
            ),
            (
                "Ecobee weather provided by UNKNOWN at 2024-01-02 10:11:12 UTC",
                "weather_station_unknown",
            ),
            (
                "Ecobee weather provided by STATION-A at UNKNOWN UTC",
                "weather_timestamp_invalid",
            ),
            (
                "Ecobee weather provided by STATION-A at 2024-02-30 10:11:12 UTC",
                "weather_timestamp_invalid",
            ),
            (
                "Ecobee weather provided by STATION-A at 2024-01-02 10:32:13 UTC",
                "weather_timestamp_future",
            ),
        )
        for attribution, reason in cases:
            with (
                self.subTest(reason=reason, attribution=attribution),
                self.assertRaisesRegex(ValueError, reason),
            ):
                self._read(
                    "temperature",
                    state=self._state(attributes={"attribution": attribution}),
                )
        permitted = self._state(
            attributes={
                "attribution": "Ecobee weather provided by STATION-A at 2024-01-02 10:32:12 UTC",
            }
        )
        self.assertEqual(
            self.now + timedelta(seconds=60),
            self._read("temperature", state=permitted).provider_reported_at,
        )

    async def test_invalid_values_and_unit_relabeling_are_rejected_without_hiding_valid_other_roles(
        self,
    ) -> None:
        cases = (
            ("humidity", {"humidity": -1}, "weather_value_invalid"),
            ("humidity", {"humidity": 101}, "weather_value_invalid"),
            ("wind_bearing", {"wind_bearing": 361}, "weather_value_invalid"),
            ("pressure", {"pressure": 0}, "weather_value_invalid"),
            ("wind_speed", {"wind_speed": -1}, "weather_value_invalid"),
            ("visibility", {"visibility": True}, "weather_value_invalid"),
            ("temperature", {"temperature": "NaN"}, "weather_value_invalid"),
            (
                "temperature",
                {"temperature": -274, "temperature_unit": "°C"},
                "weather_value_invalid",
            ),
            (
                "temperature",
                {"temperature": 1e308, "temperature_unit": "°C"},
                "weather_value_invalid",
            ),
            ("temperature", {"temperature_unit": None}, "weather_unit_invalid"),
            ("pressure", {"pressure_unit": "%"}, "weather_unit_invalid"),
            ("visibility", {"visibility": None}, "weather_value_missing"),
        )
        for role, attrs, reason in cases:
            with (
                self.subTest(role=role, attrs=attrs),
                self.assertRaisesRegex(ValueError, reason),
            ):
                self._read(
                    role,
                    state=self._state(attributes=attrs),
                    output_unit="°F" if role == "temperature" else None,
                )
        for role, unit in (
            ("pressure", "hPa"),
            ("wind_speed", "km/h"),
            ("visibility", "km"),
            ("condition", "°F"),
        ):
            with (
                self.subTest(role=role),
                self.assertRaisesRegex(ValueError, "weather_unit_mismatch"),
            ):
                self._read(role, output_unit=unit)
        self.assertEqual(
            72,
            self._read(
                "temperature", state=self._state(attributes={"pressure": None})
            ).value,
        )
        self.assertEqual(
            80, self._read("humidity", state=self._state(condition="unknown")).value
        )
        with self.assertRaisesRegex(ValueError, "unknown"):
            self._read("condition", state=self._state(condition="unknown"))

    async def test_only_enabled_native_weather_sources_and_seven_roles_are_admitted(
        self,
    ) -> None:
        for role in (
            "precipitation_unit",
            "supported_features",
            "forecast",
            "dew_point",
        ):
            with (
                self.subTest(role=role),
                self.assertRaisesRegex(ValueError, "invalid_weather_role"),
            ):
                self._read(role)
        for entry, state, reason in (
            (self.homekit, self._state(self.homekit), "weather_source_invalid"),
            (self.first, self._state(self.second), "weather_source_invalid"),
            (self.first, None, "source_missing"),
            (self.first, self._state(condition="unavailable"), "unavailable"),
        ):
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(ValueError, reason),
            ):
                self._read("temperature", entry=entry, state=state)
        disabled = self.registry.async_update_entity(
            self.first.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        with self.assertRaisesRegex(ValueError, "source_disabled"):
            self._read("temperature", entry=disabled)
