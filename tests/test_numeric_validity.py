"""Fault inputs must preserve honest field availability and source ownership."""

from __future__ import annotations

import unittest
from dataclasses import replace
from math import inf, nan
from types import MappingProxyType

from homeassistant.util import dt as dt_util

from custom_components.ecobee_unified.models import (
    RawSource,
    SourceHealth,
    build_snapshot,
    command_matches,
    finite_number,
    finite_temperature,
    homekit_temperature_agrees,
)
from custom_components.ecobee_unified.source_contracts import (
    AIR_QUALITY_SENSOR_CONTRACTS,
    sensor_contract_valid,
)

from . import test_runtime_core_api as runtime_tests


def climate(
    temperature: object = 74.0, *, unit: str = "°F", **attributes: object
) -> RawSource:
    return RawSource(
        "heat",
        {
            "current_temperature": temperature,
            "unit_of_measurement": unit,
            **attributes,
        },
        health=SourceHealth.HEALTHY,
    )


class NumericValidityTests(unittest.TestCase):
    def test_numeric_shapes_fail_closed_without_conversion_errors(self) -> None:
        for value in (True, False, nan, inf, -inf, 10**400, None, [], {}):
            with self.subTest(value_type=type(value).__name__):
                self.assertIsNone(finite_number(value))
        self.assertEqual(12.5, finite_number(12.5))
        self.assertIsNone(finite_number("12.5"))
        self.assertEqual(12.5, finite_number("12.5", allow_text=True))
        for value in ("invalid", "NaN", "Infinity", "1e400"):
            with self.subTest(state=value):
                self.assertIsNone(finite_number(value, allow_text=True))

    def test_absolute_zero_is_unit_specific_and_not_a_comfort_filter(self) -> None:
        for unit, absolute_zero in (("°C", -273.15), ("°F", -459.67), ("K", 0.0)):
            with self.subTest(unit=unit):
                self.assertEqual(absolute_zero, finite_temperature(absolute_zero, unit))
                self.assertIsNone(finite_temperature(absolute_zero - 0.001, unit))
        converted_zero = (-459.67 - 32) * 5 / 9
        self.assertEqual(converted_zero, finite_temperature(converted_zero, "°C"))
        for value, unit in ((-460.0, "°F"), (-273.2, "°C")):
            with self.subTest(serialized=value):
                self.assertEqual(
                    value, finite_temperature(value, unit, climate_source="homekit")
                )
                self.assertIsNone(finite_temperature(value, unit))
        self.assertEqual(
            -459.7, finite_temperature(-459.7, "°F", climate_source="ecobee")
        )
        self.assertIsNone(finite_temperature(-460, "°F", climate_source="ecobee"))
        for value, unit in ((-40.0, "°F"), (212.0, "°F"), (1000.0, "°C")):
            with self.subTest(value=value, unit=unit):
                self.assertEqual(value, finite_temperature(value, unit))

    def test_impossible_primary_falls_back_without_voting_or_health_rewrite(
        self,
    ) -> None:
        for unit, invalid, fallback in (
            ("°C", -273.21, 20.0),
            ("°F", -460.2, 74.0),
        ):
            with self.subTest(unit=unit):
                snapshot = build_snapshot(
                    "mapping", climate(invalid, unit=unit), climate(fallback, unit=unit)
                )
                self.assertEqual(fallback, snapshot.current_temperature)
                self.assertEqual("ecobee", snapshot.provenance["current_temperature"])
                self.assertEqual(
                    SourceHealth.HEALTHY, snapshot.source_health["homekit"]
                )
                self.assertIn(
                    "homekit_current_temperature_invalid", snapshot.degradation
                )
                self.assertIn("homekit_read_fallback", snapshot.degradation)

    def test_agreeing_impossible_sources_do_not_manufacture_precision(self) -> None:
        homekit = climate(-1000)
        precise = RawSource("-1000", health=SourceHealth.HEALTHY)
        self.assertIsNone(homekit_temperature_agrees(precise, homekit))
        snapshot = build_snapshot(
            "mapping", homekit, climate(-1000), homekit_temperature=precise
        )
        self.assertFalse(snapshot.available)
        self.assertIsNone(snapshot.current_temperature)
        self.assertIn("homekit_temperature_invalid", snapshot.degradation)
        self.assertIn("current_temperature_unavailable", snapshot.degradation)

    def test_real_extremes_and_disagreement_preserve_existing_ownership(self) -> None:
        precise = RawSource("74", health=SourceHealth.HEALTHY)
        snapshot = build_snapshot(
            "mapping", climate(212), climate(74), homekit_temperature=precise
        )
        self.assertEqual(212, snapshot.current_temperature)
        self.assertEqual("homekit", snapshot.provenance["current_temperature"])
        self.assertNotIn("homekit_current_temperature_invalid", snapshot.degradation)
        self.assertIn("homekit_temperature_diverged", snapshot.degradation)

    def test_normalized_precise_value_uses_climate_unit_not_original_unit(self) -> None:
        precise = RawSource(
            "-40",
            {"unit_of_measurement": "K"},
            health=SourceHealth.HEALTHY,
        )
        snapshot = build_snapshot(
            "mapping", climate(-40), climate(-40), homekit_temperature=precise
        )
        self.assertEqual(-40, snapshot.current_temperature)
        self.assertEqual(
            "homekit_temperature", snapshot.provenance["current_temperature"]
        )

    def test_normalization_failure_keeps_transport_health_and_reports_invalidity(
        self,
    ) -> None:
        for state in (None, "NaN", "1e400", "invalid"):
            with self.subTest(state=state):
                snapshot = build_snapshot(
                    "mapping",
                    climate(),
                    climate(),
                    homekit_temperature=RawSource(state, health=SourceHealth.HEALTHY),
                )
                self.assertEqual(74, snapshot.current_temperature)
                self.assertEqual("homekit", snapshot.provenance["current_temperature"])
                self.assertIs(
                    SourceHealth.HEALTHY, snapshot.source_health["homekit_temperature"]
                )
                self.assertIn("homekit_temperature_invalid", snapshot.degradation)
                self.assertNotIn(
                    "homekit_temperature_unavailable", snapshot.degradation
                )

    def test_invalid_optional_humidity_is_distinct_from_absence_and_recovers(
        self,
    ) -> None:
        invalid = build_snapshot(
            "mapping", climate(current_humidity=-1), climate(current_humidity=nan)
        )
        self.assertIsNone(invalid.current_humidity)
        self.assertIn("homekit_current_humidity_invalid", invalid.degradation)
        self.assertIn("ecobee_current_humidity_invalid", invalid.degradation)
        self.assertTrue(
            all(
                health is SourceHealth.HEALTHY
                for health in invalid.source_health.values()
            )
        )
        for homekit in (
            climate(),
            climate(current_humidity=None),
            climate(current_humidity=42),
        ):
            with self.subTest(attributes=homekit.attributes):
                recovered = build_snapshot("mapping", homekit, climate())
                self.assertFalse(
                    any(reason.endswith("_invalid") for reason in recovered.degradation)
                )

    def test_overflowing_attributes_fail_per_field_and_leave_other_values_usable(
        self,
    ) -> None:
        for attribute, field in (
            ("current_temperature", "current_temperature"),
            ("current_humidity", "current_humidity"),
            ("temperature", "target_temperature"),
            ("target_temp_low", "target_temperature_low"),
            ("target_temp_high", "target_temperature_high"),
        ):
            with self.subTest(attribute=attribute):
                primary = replace(
                    climate(), attributes={**climate().attributes, attribute: 10**400}
                )
                snapshot = build_snapshot(
                    "mapping",
                    primary,
                    replace(
                        climate(), attributes={**climate().attributes, attribute: 42}
                    ),
                )
                self.assertEqual(42, getattr(snapshot, field))
                self.assertIn(f"homekit_{field}_invalid", snapshot.degradation)
                self.assertTrue(snapshot.available)
        vendor_invalid = build_snapshot(
            "mapping", climate(), climate(fan_min_on_time=10**400)
        )
        self.assertIsNone(vendor_invalid.minimum_fan_runtime)
        self.assertIn("ecobee_minimum_fan_runtime_invalid", vendor_invalid.degradation)

    def test_invalid_bound_metadata_disables_only_affected_control_metadata(
        self,
    ) -> None:
        snapshot = build_snapshot(
            "mapping",
            climate(supported_features=1, min_temp=10**400, max_temp=90),
            climate(),
        )
        self.assertEqual(74, snapshot.current_temperature)
        self.assertTrue(snapshot.available)
        self.assertEqual(0, snapshot.supported_features)
        self.assertIn("homekit_temperature_metadata_unavailable", snapshot.degradation)

    def test_nonfinite_and_overflowing_confirmation_never_confirm(self) -> None:
        snapshot = build_snapshot("mapping", climate(), climate(temperature=74))
        for value in (10**400, nan, inf, True):
            with self.subTest(value_type=type(value).__name__):
                self.assertFalse(
                    command_matches(snapshot, {"target_temperature": value})
                )
                corrupt = replace(
                    snapshot,
                    confirmation_values=MappingProxyType({"target_temperature": value}),
                )
                self.assertFalse(command_matches(corrupt, {"target_temperature": 74}))


class NumericBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Exercise conversion against real Core registry/state objects."""

    async def asyncSetUp(self) -> None:
        self.runtime = runtime_tests.RuntimeCoreApiTests()
        self.runtime.setUp()
        await self.runtime.asyncSetUp()

    async def asyncTearDown(self) -> None:
        await self.runtime.asyncTearDown()
        self.runtime.tearDown()

    async def test_precise_conversion_retains_raw_state_and_transport_health(
        self,
    ) -> None:
        for state, unit, homekit_temperature, expected in (
            ("0", "K", -460.0, -459.67),
            ("-0.01", "K", 74.0, None),
            ("1e308", "°C", 74.0, None),
            ("NaN", "°F", 74.0, None),
        ):
            with self.subTest(state=state, unit=unit):
                self.runtime.hass.states.async_set(
                    self.runtime.homekit_temperature.entity_id,
                    state,
                    {"device_class": "temperature", "unit_of_measurement": unit},
                )
                homekit = climate(homekit_temperature)
                source = self.runtime.manager._temperature_raw_source(
                    self.runtime.homekit_temperature.id,
                    homekit,
                    now=dt_util.utcnow(),
                    report_times=None,
                    required_device_id=self.runtime.homekit.device_id,
                )
                assert source is not None
                self.assertIs(SourceHealth.HEALTHY, source.health)
                snapshot = build_snapshot(
                    "mapping", homekit, climate(), homekit_temperature=source
                )
                if expected is None:
                    self.assertIsNone(source.state)
                    self.assertEqual(74.0, snapshot.current_temperature)
                    self.assertIn("homekit_temperature_invalid", snapshot.degradation)
                else:
                    assert snapshot.current_temperature is not None
                    self.assertAlmostEqual(expected, snapshot.current_temperature)
                    self.assertEqual(
                        "homekit_temperature",
                        snapshot.provenance["current_temperature"],
                    )
                original = self.runtime.hass.states.get(
                    self.runtime.homekit_temperature.entity_id
                )
                assert original is not None
                self.assertEqual(state, original.state)
                self.assertEqual(unit, original.attributes["unit_of_measurement"])

    async def test_malformed_air_quality_contract_fails_without_conversion_error(
        self,
    ) -> None:
        for state, unit in (("1e400", None), ("42", []), ("NaN", None)):
            with self.subTest(state=state, unit=unit):
                self.runtime.hass.states.async_set(
                    self.runtime.ecobee_aqi.entity_id,
                    state,
                    {"device_class": "aqi", "unit_of_measurement": unit},
                )
                self.assertFalse(
                    sensor_contract_valid(
                        self.runtime.hass,
                        self.runtime.ecobee_aqi.id,
                        AIR_QUALITY_SENSOR_CONTRACTS["aqi"],
                    )
                )
