"""Revision-guarded confirmation tests."""

from __future__ import annotations

import unittest

from custom_components.ecobee_unified.commands import CommandTracker
from custom_components.ecobee_unified.models import (
    CommandStatus,
    RawSource,
    SourceHealth,
    build_snapshot,
)


class CommandTrackerTests(unittest.TestCase):
    def test_late_observation_cannot_mutate_newer_revision(self) -> None:
        now = [100.0]
        tracker = CommandTracker(lambda: now[0])
        old_revision = tracker.begin(
            "mapping_a", "set_temperature", {"target_temperature": 21.0}
        )
        new_revision = tracker.begin(
            "mapping_a", "set_temperature", {"target_temperature": 23.0}
        )
        old_snapshot = _snapshot(21.0)
        new_snapshot = _snapshot(23.0)

        self.assertFalse(tracker.observe("mapping_a", old_revision, old_snapshot))
        self.assertEqual(CommandStatus.PENDING, tracker.summary("mapping_a").status)
        self.assertTrue(tracker.observe("mapping_a", new_revision, new_snapshot))
        self.assertEqual(CommandStatus.CONFIRMED, tracker.summary("mapping_a").status)

    def test_old_timeout_and_failure_are_revision_guarded(self) -> None:
        tracker = CommandTracker(lambda: 10.0)
        old_revision = tracker.begin(
            "mapping_a", "set_hvac_mode", {"hvac_mode": "heat"}
        )
        new_revision = tracker.begin(
            "mapping_a", "set_hvac_mode", {"hvac_mode": "cool"}
        )
        self.assertFalse(tracker.timeout("mapping_a", old_revision))
        self.assertFalse(tracker.fail("mapping_a", old_revision))
        self.assertEqual(CommandStatus.PENDING, tracker.summary("mapping_a").status)
        self.assertTrue(tracker.timeout("mapping_a", new_revision))
        self.assertEqual(CommandStatus.UNCONFIRMED, tracker.summary("mapping_a").status)

    def test_confirmation_uses_ecobee_observation_not_homekit_projection(self) -> None:
        tracker = CommandTracker(lambda: 10.0)
        revision = tracker.begin(
            "mapping_a", "set_temperature", {"target_temperature": 23.0}
        )
        snapshot = build_snapshot(
            "mapping_a",
            RawSource(
                "heat",
                {"current_temperature": 20.0, "temperature": 23.0},
                health=SourceHealth.HEALTHY,
            ),
            RawSource(
                "heat",
                {"current_temperature": 20.0, "temperature": 21.0},
                health=SourceHealth.HEALTHY,
            ),
        )
        self.assertEqual(23.0, snapshot.target_temperature)
        self.assertFalse(tracker.observe("mapping_a", revision, snapshot))
        self.assertEqual(CommandStatus.PENDING, tracker.summary("mapping_a").status)


def _snapshot(target: float):
    return build_snapshot(
        "mapping_a",
        RawSource(
            "heat",
            {"current_temperature": 20.0, "temperature": target},
            health=SourceHealth.HEALTHY,
        ),
        RawSource(
            "heat",
            {"current_temperature": 20.0, "temperature": target},
            health=SourceHealth.HEALTHY,
        ),
    )


if __name__ == "__main__":
    unittest.main()
