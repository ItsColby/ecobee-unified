"""Source identity and numeric evidence required for precise-source recovery."""

from __future__ import annotations

import unittest

from custom_components.ecobee_unified.temperature_quality import (
    TemperatureObservation,
    TemperatureRecovery,
)

IDENTITY = ("climate_registry_id", "device_id", "sensor_registry_id", "device_id")


class TemperatureRecoveryTests(unittest.TestCase):
    def test_confirmed_frozen_value_requires_changed_agreeing_reading(self) -> None:
        recovery = TemperatureRecovery()
        original = TemperatureObservation(IDENTITY, 23.7)
        self.assertFalse(recovery.observe(IDENTITY, original, False))
        self.assertTrue(recovery.observe(IDENTITY, original, False, confirmed=True))
        self.assertTrue(recovery.observe(IDENTITY, original, True))
        self.assertTrue(recovery.observe(IDENTITY, original, True))
        changed = TemperatureObservation(IDENTITY, 23.6)
        self.assertFalse(recovery.observe(IDENTITY, changed, True))

    def test_availability_or_unknown_comparison_does_not_erase_evidence(self) -> None:
        recovery = TemperatureRecovery()
        original = TemperatureObservation(IDENTITY, 23.7)
        recovery.observe(IDENTITY, original, False, confirmed=True)
        self.assertTrue(recovery.observe(IDENTITY, None, None))
        self.assertTrue(recovery.observe(None, None, None))
        self.assertTrue(recovery.observe(IDENTITY, original, True))

    def test_conversion_noise_does_not_manufacture_recovery(self) -> None:
        recovery = TemperatureRecovery()
        original = TemperatureObservation(IDENTITY, 23.7)
        recovery.observe(IDENTITY, original, False, confirmed=True)
        converted = TemperatureObservation(IDENTITY, (74.66 - 32) * 5 / 9)
        self.assertTrue(recovery.observe(IDENTITY, converted, True))

    def test_new_confirmed_divergent_value_replaces_rejected_evidence(self) -> None:
        recovery = TemperatureRecovery()
        recovery.observe(
            IDENTITY, TemperatureObservation(IDENTITY, 23.7), False, confirmed=True
        )
        changed = TemperatureObservation(IDENTITY, 23.6)
        self.assertTrue(recovery.observe(IDENTITY, changed, False, confirmed=True))
        self.assertTrue(recovery.observe(IDENTITY, changed, True))

    def test_real_source_replacement_discards_previous_source_evidence(self) -> None:
        recovery = TemperatureRecovery()
        original = TemperatureObservation(IDENTITY, 23.7)
        recovery.observe(IDENTITY, original, False, confirmed=True)
        self.assertTrue(recovery.observe(IDENTITY, original, True))
        replacement_identity = (*IDENTITY[:2], "replacement_registry_id", "device_id")
        replacement = TemperatureObservation(replacement_identity, 23.7)
        self.assertFalse(recovery.observe(replacement_identity, replacement, True))

    def test_quiet_healthy_mapping_has_no_recovery_requirement(self) -> None:
        recovery = TemperatureRecovery()
        original = TemperatureObservation(IDENTITY, 23.7)
        for _ in range(5):
            self.assertFalse(recovery.observe(IDENTITY, original, True))
        other = TemperatureRecovery()
        recovery.observe(IDENTITY, original, False, confirmed=True)
        self.assertFalse(other.observe(IDENTITY, original, True))


if __name__ == "__main__":
    unittest.main()
