"""Bounded cloud-only sensor normalization tests."""

from __future__ import annotations

import unittest

from custom_components.ecobee_unified.sensor import equipment_stage


class EquipmentStageTests(unittest.TestCase):
    def test_known_stage_ignores_subordinate_fan(self) -> None:
        self.assertEqual("cool_stage_1", equipment_stage("compCool1,fan"))
        self.assertEqual("heat_pump_stage_2", equipment_stage("heatPump2,fan"))

    def test_idle_multiple_and_unknown_are_bounded(self) -> None:
        self.assertEqual("idle", equipment_stage(""))
        self.assertEqual("multiple", equipment_stage("compCool1,auxHeat1"))
        self.assertEqual("multiple", equipment_stage("compCool1,privateToken"))
        self.assertIsNone(equipment_stage(None))


if __name__ == "__main__":
    unittest.main()
