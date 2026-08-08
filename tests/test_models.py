"""Deterministic normalization tests."""

from __future__ import annotations

import unittest
from dataclasses import replace
from math import inf, nan
from types import MappingProxyType

from custom_components.ecobee_unified.models import (
    CommandStatus,
    CommandSummary,
    RawSource,
    SourceHealth,
    build_snapshot,
    command_matches,
)


def source(
    state: str,
    attributes: dict[str, object],
    health: SourceHealth = SourceHealth.HEALTHY,
) -> RawSource:
    return RawSource(state, attributes, age_seconds=10, health=health)


class SnapshotTests(unittest.TestCase):
    def test_every_standard_field_uses_primary_owner(self) -> None:
        homekit_attributes = {
            "current_temperature": 20.0,
            "current_humidity": 41.0,
            "humidity": 36.0,
            "min_humidity": 20.0,
            "max_humidity": 50.0,
            "temperature": 21.0,
            "target_temp_low": 19.0,
            "target_temp_high": 24.0,
            "hvac_action": "heating",
            "fan_mode": "auto",
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temp_step": 0.5,
            "unit_of_measurement": "°C",
            "hvac_modes": ["off", "heat", "cool", "heat_cool"],
            "fan_modes": ["auto", "on"],
            "supported_features": 389,
        }
        ecobee_attributes = {
            key: 99.0 if isinstance(value, float) else "ecobee_value"
            for key, value in homekit_attributes.items()
            if key not in {"hvac_modes", "fan_modes", "supported_features"}
        }
        snapshot = build_snapshot(
            "mapping_a",
            source("heat", homekit_attributes),
            source("cool", ecobee_attributes),
        )
        expected = {
            "hvac_mode": "heat",
            "hvac_action": "heating",
            "current_temperature": 20.0,
            "current_humidity": 41.0,
            "target_humidity": 36.0,
            "min_humidity": 20.0,
            "max_humidity": 50.0,
            "target_temperature": 21.0,
            "target_temperature_low": 19.0,
            "target_temperature_high": 24.0,
            "fan_mode": "auto",
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temperature_step": 0.5,
            "temperature_unit": "°C",
        }
        for field_name, value in expected.items():
            with self.subTest(field=field_name):
                self.assertEqual(value, getattr(snapshot, field_name))
                self.assertEqual("homekit", snapshot.provenance[field_name])

    def test_each_missing_primary_attribute_uses_only_ecobee_fallback(self) -> None:
        fields = {
            "hvac_action": ("hvac_action", "heating"),
            "current_humidity": ("current_humidity", 40.0),
            "target_temperature": ("temperature", 22.0),
            "target_temperature_low": ("target_temp_low", 19.0),
            "target_temperature_high": ("target_temp_high", 24.0),
            "fan_mode": ("fan_mode", "auto"),
        }
        for field_name, (attribute, value) in fields.items():
            with self.subTest(field=field_name):
                snapshot = build_snapshot(
                    "mapping_a",
                    source("heat", {"current_temperature": 20.0}),
                    source(
                        "heat",
                        {"current_temperature": 21.0, attribute: value},
                    ),
                )
                self.assertEqual(value, getattr(snapshot, field_name))
                self.assertEqual("ecobee", snapshot.provenance[field_name])

    def test_writer_metadata_never_uses_generic_ecobee_fallback(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source(
                "heat",
                {
                    "current_temperature": 20.0,
                    "supported_features": 389,
                },
            ),
            source(
                "heat",
                {
                    "current_temperature": 21.0,
                    "min_temp": 7.0,
                    "max_temp": 35.0,
                    "target_temp_step": 0.5,
                    "unit_of_measurement": "°C",
                    "humidity": 36.0,
                    "min_humidity": 20.0,
                    "max_humidity": 50.0,
                },
            ),
            temperature_step_fusion_proven=True,
        )
        self.assertIsNone(snapshot.min_temp)
        self.assertIsNone(snapshot.max_temp)
        self.assertIsNone(snapshot.target_temperature_step)
        self.assertIsNone(snapshot.temperature_unit)
        self.assertIsNone(snapshot.target_humidity)
        self.assertEqual(384, snapshot.supported_features)
        self.assertIn("homekit_temperature_metadata_unavailable", snapshot.degradation)
        self.assertIn("homekit_humidity_bounds_unavailable", snapshot.degradation)

    def test_climate_writer_rejects_non_climate_temperature_unit(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source(
                "heat",
                {
                    "current_temperature": 293.15,
                    "temperature": 294.15,
                    "min_temp": 280.15,
                    "max_temp": 308.15,
                    "target_temp_step": 0.5,
                    "unit_of_measurement": "K",
                    "supported_features": 385,
                },
            ),
            source("heat", {"current_temperature": 20.0}),
        )

        self.assertIsNone(snapshot.temperature_unit)
        self.assertIsNone(snapshot.min_temp)
        self.assertIsNone(snapshot.max_temp)
        self.assertIsNone(snapshot.target_temperature_step)
        self.assertEqual(384, snapshot.supported_features)
        self.assertIn("homekit_temperature_metadata_unavailable", snapshot.degradation)

    def test_primary_owns_standard_fields_even_when_values_disagree(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source(
                "heat",
                {
                    "current_temperature": 20.0,
                    "temperature": 21.0,
                    "hvac_action": "heating",
                    "hvac_modes": ["off", "heat", "cool"],
                    "supported_features": 385,
                    "unit_of_measurement": "°C",
                },
            ),
            source(
                "cool",
                {
                    "current_temperature": 28.0,
                    "temperature": 18.0,
                    "hvac_action": "cooling",
                    "preset_mode": "home",
                    "climate_mode": "Home",
                },
            ),
        )
        self.assertEqual("heat", snapshot.hvac_mode)
        self.assertEqual(20.0, snapshot.current_temperature)
        self.assertEqual(21.0, snapshot.target_temperature)
        self.assertEqual("homekit", snapshot.provenance["current_temperature"])
        self.assertNotEqual(24.0, snapshot.current_temperature)
        self.assertIsNone(snapshot.preset_mode)
        self.assertEqual("home", snapshot.ecobee_preset_mode)
        self.assertEqual("Home", snapshot.climate_mode)

    def test_ecobee_fallback_is_read_only_and_explicit(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source(
                "unavailable",
                {"current_temperature": 99.0, "supported_features": 511},
                SourceHealth.UNAVAILABLE,
            ),
            source(
                "heat",
                {
                    "current_temperature": 22.0,
                    "temperature": 23.0,
                    "hvac_modes": ["off", "heat"],
                    "supported_features": 385,
                },
            ),
        )
        self.assertTrue(snapshot.available)
        self.assertFalse(snapshot.homekit_writable)
        self.assertEqual(0, snapshot.supported_features)
        self.assertEqual((), snapshot.hvac_modes)
        self.assertEqual((), snapshot.fan_modes)
        self.assertEqual("ecobee", snapshot.provenance["current_temperature"])
        self.assertIn("homekit_read_fallback", snapshot.degradation)

    def test_stale_fallback_does_not_fabricate_required_semantics(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            RawSource(None, health=SourceHealth.MISSING),
            source(
                "heat",
                {"current_temperature": 22.0},
                SourceHealth.STALE,
            ),
        )
        self.assertFalse(snapshot.available)
        self.assertIsNone(snapshot.hvac_mode)
        self.assertIn("required_climate_semantics_unavailable", snapshot.degradation)

    def test_malformed_primary_fields_use_valid_typed_fallbacks(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source(
                "invalid_mode",
                {
                    "current_temperature": "twenty",
                    "current_humidity": 101,
                    "temperature": nan,
                    "target_temp_low": inf,
                    "target_temp_high": True,
                    "hvac_action": "invalid_action",
                    "fan_mode": "",
                    "min_temp": object(),
                    "max_temp": "thirty-five",
                    "target_temp_step": 0,
                    "unit_of_measurement": "invalid_unit",
                },
            ),
            source(
                "heat",
                {
                    "current_temperature": 20.0,
                    "current_humidity": 40.0,
                    "temperature": 21.0,
                    "target_temp_low": 18.0,
                    "target_temp_high": 24.0,
                    "hvac_action": "heating",
                    "fan_mode": "auto",
                    "min_temp": 7.0,
                    "max_temp": 35.0,
                    "target_temp_step": 0.5,
                    "unit_of_measurement": "°C",
                },
            ),
        )
        expected = {
            "hvac_mode": "heat",
            "current_temperature": 20.0,
            "current_humidity": 40.0,
            "target_temperature": 21.0,
            "target_temperature_low": 18.0,
            "target_temperature_high": 24.0,
            "hvac_action": "heating",
            "fan_mode": "auto",
        }
        for field_name, value in expected.items():
            with self.subTest(field=field_name):
                self.assertEqual(value, getattr(snapshot, field_name))
                self.assertEqual("ecobee", snapshot.provenance[field_name])
        self.assertIsNone(snapshot.min_temp)
        self.assertIsNone(snapshot.max_temp)
        self.assertIsNone(snapshot.target_temperature_step)
        self.assertIsNone(snapshot.temperature_unit)

    def test_same_device_step_fusion_requires_explicit_proof_and_matching_unit(
        self,
    ) -> None:
        homekit = source(
            "heat",
            {
                "current_temperature": 20.0,
                "min_temp": 7.0,
                "max_temp": 35.0,
                "unit_of_measurement": "°C",
            },
        )
        ecobee = source(
            "heat",
            {
                "current_temperature": 20.6,
                "target_temp_step": 0.5,
                "unit_of_measurement": "°C",
            },
        )
        unproven = build_snapshot("mapping_a", homekit, ecobee)
        self.assertIsNone(unproven.target_temperature_step)
        fused = build_snapshot(
            "mapping_a", homekit, ecobee, temperature_step_fusion_proven=True
        )
        self.assertEqual(0.5, fused.target_temperature_step)
        self.assertEqual(
            "ecobee_same_device_fusion",
            fused.provenance["target_temperature_step"],
        )
        stale_metadata = build_snapshot(
            "mapping_a",
            homekit,
            source(
                "heat",
                {
                    "current_temperature": 20.6,
                    "target_temp_step": 0.5,
                    "unit_of_measurement": "°C",
                },
                SourceHealth.STALE,
            ),
            temperature_step_fusion_proven=True,
        )
        self.assertEqual(0.5, stale_metadata.target_temperature_step)
        self.assertEqual(
            "ecobee_same_device_fusion",
            stale_metadata.provenance["target_temperature_step"],
        )
        mismatched = build_snapshot(
            "mapping_a",
            homekit,
            source(
                "heat",
                {
                    "current_temperature": 20.6,
                    "target_temp_step": 0.5,
                    "unit_of_measurement": "°F",
                },
            ),
            temperature_step_fusion_proven=True,
        )
        self.assertIsNone(mismatched.target_temperature_step)

    def test_primary_precision_is_not_replaced_by_more_precise_fallback(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source("heat", {"current_temperature": 20.0}),
            source("heat", {"current_temperature": 20.63}),
        )
        self.assertEqual(20.0, snapshot.current_temperature)
        self.assertEqual("homekit", snapshot.provenance["current_temperature"])

    def test_explicit_homekit_temperature_sensor_preserves_local_precision(
        self,
    ) -> None:
        homekit = source(
            "heat",
            {"current_temperature": 72.0, "unit_of_measurement": "°F"},
        )
        ecobee = source("heat", {"current_temperature": 72.3})
        precise = RawSource(
            "72.4",
            {"device_class": "temperature", "unit_of_measurement": "°F"},
            age_seconds=86_400,
            health=SourceHealth.HEALTHY,
        )

        snapshot = build_snapshot(
            "mapping_a",
            homekit,
            ecobee,
            homekit_temperature=precise,
        )

        self.assertEqual(72.4, snapshot.current_temperature)
        self.assertEqual(
            "homekit_temperature", snapshot.provenance["current_temperature"]
        )
        self.assertEqual(
            SourceHealth.HEALTHY, snapshot.source_health["homekit_temperature"]
        )
        self.assertNotIn("homekit_temperature_unavailable", snapshot.degradation)

    def test_precise_temperature_requires_current_homekit_semantic_agreement(
        self,
    ) -> None:
        homekit = source(
            "cool",
            {"current_temperature": 76.0, "unit_of_measurement": "°F"},
        )
        ecobee = source("cool", {"current_temperature": 76.6})
        diverged = RawSource(
            "77.72",
            {"device_class": "temperature", "unit_of_measurement": "°F"},
            health=SourceHealth.HEALTHY,
        )

        snapshot = build_snapshot(
            "mapping_a",
            homekit,
            ecobee,
            homekit_temperature=diverged,
        )

        self.assertEqual(76.0, snapshot.current_temperature)
        self.assertEqual("homekit", snapshot.provenance["current_temperature"])
        self.assertIn("homekit_temperature_diverged", snapshot.degradation)

    def test_precise_temperature_rounding_envelope_is_unit_aware(self) -> None:
        cases = (
            ("°F", 72.0, 72.5, True),
            ("°F", 72.0, 72.52, False),
            ("°C", 20.0, 20.05, True),
            ("°C", 20.0, 20.06, False),
        )
        for unit, climate_value, sensor_value, accepted in cases:
            with self.subTest(unit=unit, sensor_value=sensor_value):
                snapshot = build_snapshot(
                    "mapping_a",
                    source(
                        "heat",
                        {
                            "current_temperature": climate_value,
                            "unit_of_measurement": unit,
                        },
                    ),
                    source("heat", {"current_temperature": climate_value}),
                    homekit_temperature=RawSource(
                        str(sensor_value), health=SourceHealth.HEALTHY
                    ),
                )
                expected_owner = "homekit_temperature" if accepted else "homekit"
                self.assertEqual(
                    expected_owner,
                    snapshot.provenance["current_temperature"],
                )
                self.assertEqual(
                    not accepted,
                    "homekit_temperature_diverged" in snapshot.degradation,
                )

    def test_precise_temperature_is_not_used_without_local_climate_proof(
        self,
    ) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            RawSource(None, health=SourceHealth.UNAVAILABLE),
            source("heat", {"current_temperature": 72.3}),
            homekit_temperature=RawSource("74.8", health=SourceHealth.HEALTHY),
        )

        self.assertEqual(72.3, snapshot.current_temperature)
        self.assertEqual("ecobee", snapshot.provenance["current_temperature"])
        self.assertIn("homekit_temperature_unverifiable", snapshot.degradation)

    def test_temperature_confirmation_uses_writer_step_tolerance(self) -> None:
        homekit = source(
            "heat_cool",
            {
                "current_temperature": 72.0,
                "min_temp": 45.0,
                "max_temp": 95.0,
                "target_temp_step": 0.5,
                "unit_of_measurement": "°F",
            },
        )
        quantized = build_snapshot(
            "mapping_a",
            homekit,
            source(
                "heat_cool",
                {"current_temperature": 72.1, "target_temp_low": 69.2},
            ),
        )
        half_step = build_snapshot(
            "mapping_a",
            homekit,
            source(
                "heat_cool",
                {"current_temperature": 72.1, "target_temp_low": 69.25},
            ),
        )
        wrong_target = build_snapshot(
            "mapping_a",
            homekit,
            source(
                "heat_cool",
                {"current_temperature": 72.1, "target_temp_low": 69.26},
            ),
        )

        expected = {"target_temperature_low": 69.0}
        self.assertTrue(command_matches(quantized, expected))
        self.assertTrue(command_matches(half_step, expected))
        self.assertFalse(command_matches(wrong_target, expected))

        fine_step = build_snapshot(
            "mapping_a",
            source(
                "heat",
                {
                    "current_temperature": 20.0,
                    "min_temp": 7.0,
                    "max_temp": 35.0,
                    "target_temp_step": 0.1,
                    "unit_of_measurement": "\N{DEGREE SIGN}C",
                },
            ),
            source("heat", {"current_temperature": 20.0}),
        )
        fine_step = replace(
            fine_step,
            confirmation_values=MappingProxyType({"target_temperature": 20.05}),
        )
        self.assertTrue(command_matches(fine_step, {"target_temperature": 20.0}))
        fine_step = replace(
            fine_step,
            confirmation_values=MappingProxyType({"target_temperature": 20.051}),
        )
        self.assertFalse(command_matches(fine_step, {"target_temperature": 20.0}))

    def test_non_temperature_confirmation_keeps_strict_tolerance(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source(
                "heat",
                {"current_temperature": 20.0, "humidity": 36.2},
            ),
            source("heat", {"current_temperature": 20.0}),
        )

        self.assertFalse(command_matches(snapshot, {"target_humidity": 36.0}))

    def test_explicit_temperature_falls_back_only_on_actual_unavailability(
        self,
    ) -> None:
        homekit = source("heat", {"current_temperature": 20.0})
        ecobee = source("heat", {"current_temperature": 20.6})
        unavailable = RawSource(None, health=SourceHealth.UNAVAILABLE)

        local_fallback = build_snapshot(
            "mapping_a",
            homekit,
            ecobee,
            homekit_temperature=unavailable,
        )
        self.assertEqual(20.0, local_fallback.current_temperature)
        self.assertEqual("homekit", local_fallback.provenance["current_temperature"])
        self.assertIn("homekit_temperature_unavailable", local_fallback.degradation)

        cloud_fallback = build_snapshot(
            "mapping_a",
            RawSource(None, health=SourceHealth.UNAVAILABLE),
            ecobee,
            homekit_temperature=unavailable,
        )
        self.assertEqual(20.6, cloud_fallback.current_temperature)
        self.assertEqual("ecobee", cloud_fallback.provenance["current_temperature"])
        self.assertIn("homekit_read_fallback", cloud_fallback.degradation)

    def test_malformed_values_are_not_published_or_confirmed(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source("invalid", {"current_temperature": nan}),
            source(
                "invalid",
                {
                    "current_temperature": inf,
                    "temperature": True,
                    "fan_min_on_time": 61,
                    "active_sensors": ["", "sensor_a", "sensor_a"],
                },
            ),
        )
        self.assertFalse(snapshot.available)
        self.assertIsNone(snapshot.current_temperature)
        self.assertIsNone(snapshot.minimum_fan_runtime)
        self.assertEqual(("sensor_a",), snapshot.active_sensors)
        self.assertEqual({}, snapshot.confirmation_values)

    def test_negative_cloud_metrics_and_fractional_fan_runtime_are_rejected(
        self,
    ) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source("heat", {"current_temperature": 20.0}),
            source(
                "heat",
                {
                    "current_temperature": 21.0,
                    "fan_min_on_time": 15.5,
                },
            ),
            air_quality_index=RawSource("-1", health=SourceHealth.HEALTHY),
            co2=RawSource("-2", health=SourceHealth.HEALTHY),
            voc=RawSource("-3", health=SourceHealth.HEALTHY),
        )
        self.assertIsNone(snapshot.minimum_fan_runtime)
        self.assertIsNone(snapshot.air_quality_index)
        self.assertIsNone(snapshot.co2)
        self.assertIsNone(snapshot.voc)

    def test_malformed_supported_features_degrade_to_no_controls(self) -> None:
        for value in (float("inf"), float("nan"), -1, 1.5):
            with self.subTest(value=value):
                snapshot = build_snapshot(
                    "mapping_a",
                    source(
                        "heat",
                        {
                            "current_temperature": 20.0,
                            "supported_features": value,
                        },
                    ),
                    source("heat", {"current_temperature": 21.0}),
                )
                self.assertEqual(0, snapshot.supported_features)

    def test_current_temperature_never_uses_similar_raw_sensor_field(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source(
                "heat",
                {
                    "current_temperature": 20.0,
                    "thermostat_temperature": 31.0,
                },
            ),
            source("heat", {"current_temperature": 21.0}),
        )
        self.assertEqual(20.0, snapshot.current_temperature)

    def test_optional_vendor_projections_are_bounded_and_independent(self) -> None:
        snapshot = build_snapshot(
            "mapping_a",
            source("heat", {"current_temperature": 20.0}),
            source(
                "heat",
                {
                    "current_temperature": 21.0,
                    "active_sensors": [f"sensor_{index}" for index in range(20)],
                },
            ),
            RawSource(
                "Home",
                {"options": [f"mode_{index}" for index in range(20)]},
                health=SourceHealth.HEALTHY,
            ),
            RawSource("42", health=SourceHealth.HEALTHY),
            RawSource(None, health=SourceHealth.UNAVAILABLE),
            None,
            CommandSummary(2, "set_temperature", CommandStatus.PENDING, 3),
        )
        self.assertTrue(snapshot.available)
        self.assertEqual(8, len(snapshot.active_sensors))
        self.assertEqual("Home", snapshot.preset_mode)
        self.assertEqual(8, len(snapshot.preset_modes))
        self.assertEqual(42.0, snapshot.air_quality_index)
        self.assertIsNone(snapshot.co2)
        self.assertIn("co2_unavailable", snapshot.degradation)
        self.assertEqual(
            SourceHealth.UNAVAILABLE,
            snapshot.source_health["co2"],
        )
        self.assertEqual(SourceHealth.MISSING, snapshot.source_health["voc"])
        self.assertEqual(CommandStatus.PENDING, snapshot.command.status)


if __name__ == "__main__":
    unittest.main()
