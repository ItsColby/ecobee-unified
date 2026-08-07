"""Deterministic normalization tests."""

from __future__ import annotations

import unittest

from custom_components.ecobee_unified.models import (
    CommandStatus,
    CommandSummary,
    RawSource,
    SourceHealth,
    build_snapshot,
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
            "supported_features": 385,
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
            "min_temp": ("min_temp", 7.0),
            "max_temp": ("max_temp", 35.0),
            "target_temperature_step": ("target_temp_step", 0.5),
            "temperature_unit": ("unit_of_measurement", "°C"),
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
                },
            ),
        )
        self.assertEqual("heat", snapshot.hvac_mode)
        self.assertEqual(20.0, snapshot.current_temperature)
        self.assertEqual(21.0, snapshot.target_temperature)
        self.assertEqual("homekit", snapshot.provenance["current_temperature"])
        self.assertNotEqual(24.0, snapshot.current_temperature)
        self.assertEqual("home", snapshot.preset_mode)

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

    def test_optional_context_is_bounded_and_does_not_affect_availability(self) -> None:
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
            RawSource(None, health=SourceHealth.UNAVAILABLE),
            None,
            CommandSummary(2, "set_temperature", CommandStatus.PENDING, 3),
        )
        self.assertTrue(snapshot.available)
        self.assertEqual(8, len(snapshot.active_sensors))
        self.assertIsNone(snapshot.scheduled_profile)
        self.assertEqual(CommandStatus.PENDING, snapshot.command.status)


if __name__ == "__main__":
    unittest.main()
