"""Source identity and numeric evidence required for precise-source recovery."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ecobee_unified.temperature_quality import (
    TemperatureObservation,
    TemperatureRecovery,
    TemperatureSilence,
)

IDENTITY = ("climate_registry_id", "device_id", "sensor_registry_id", "device_id")
START = datetime(2026, 9, 28, 23, 49, tzinfo=UTC)
LATER = START + timedelta(hours=1)


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


class TemperatureSilenceTests(unittest.TestCase):
    def test_two_unanswered_climate_changes_mark_the_sensor_silent(self) -> None:
        silence = TemperatureSilence()
        silence.climate_changed(IDENTITY, START + timedelta(minutes=5))
        self.assertFalse(silence.silent(IDENTITY, START, LATER))
        silence.climate_changed(IDENTITY, START + timedelta(minutes=20))
        self.assertTrue(silence.silent(IDENTITY, START, LATER))

    def test_a_burst_of_changes_is_not_yet_silence(self) -> None:
        silence = TemperatureSilence()
        first = START + timedelta(minutes=5)
        silence.climate_changed(IDENTITY, first)
        silence.climate_changed(IDENTITY, first + timedelta(seconds=1))
        self.assertFalse(silence.silent(IDENTITY, START, first + timedelta(seconds=59)))
        self.assertTrue(silence.silent(IDENTITY, START, first + timedelta(seconds=60)))

    def test_a_later_precise_value_change_answers_earlier_changes(self) -> None:
        silence = TemperatureSilence()
        silence.climate_changed(IDENTITY, START + timedelta(minutes=5))
        silence.climate_changed(IDENTITY, START + timedelta(minutes=20))
        answered = START + timedelta(minutes=20, milliseconds=40)
        self.assertFalse(silence.silent(IDENTITY, answered, LATER))
        silence.climate_changed(IDENTITY, START + timedelta(minutes=30))
        self.assertFalse(silence.silent(IDENTITY, answered, LATER))

    def test_one_reordered_pair_is_tolerated(self) -> None:
        silence = TemperatureSilence()
        changed = START + timedelta(minutes=5)
        silence.climate_changed(IDENTITY, changed)
        # The precise report of this pair arrives just before the climate write.
        self.assertFalse(
            silence.silent(IDENTITY, changed - timedelta(milliseconds=5), LATER)
        )

    def test_source_replacement_and_reset_require_fresh_evidence(self) -> None:
        silence = TemperatureSilence()
        silence.climate_changed(IDENTITY, START + timedelta(minutes=5))
        silence.climate_changed(IDENTITY, START + timedelta(minutes=20))
        replacement = (*IDENTITY[:2], "replacement_registry_id", "device_id")
        self.assertFalse(silence.silent(replacement, START, LATER))
        silence.climate_changed(replacement, START + timedelta(minutes=25))
        self.assertFalse(silence.silent(replacement, START, LATER))
        silence.climate_changed(replacement, START + timedelta(minutes=30))
        self.assertTrue(silence.silent(replacement, START, LATER))
        silence.reset()
        self.assertFalse(silence.silent(replacement, START, LATER))

    def test_unresolved_association_records_nothing(self) -> None:
        silence = TemperatureSilence()
        silence.climate_changed(None, START + timedelta(minutes=5))
        silence.climate_changed(None, START + timedelta(minutes=20))
        self.assertFalse(silence.silent(None, START, LATER))
        self.assertFalse(silence.silent(IDENTITY, START, LATER))


if __name__ == "__main__":
    unittest.main()
