"""Bounded cloud-only sensor normalization tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.ecobee_unified.const import SUFFIX_EQUIPMENT_STAGE
from custom_components.ecobee_unified.sensor import (
    _EQUIPMENT_STAGES,
    EQUIPMENT_STAGE_OPTIONS,
    PROJECTIONS,
    equipment_stage,
)


class EquipmentStageTests(unittest.TestCase):
    def test_known_stage_ignores_subordinate_fan(self) -> None:
        self.assertEqual("cool_stage_1", equipment_stage("compCool1,fan"))
        self.assertEqual("heat_pump_stage_2", equipment_stage("heatPump2,fan"))

    def test_idle_multiple_and_unknown_are_bounded(self) -> None:
        self.assertEqual("idle", equipment_stage(""))
        self.assertEqual("unknown", equipment_stage("privateToken"))
        self.assertEqual("multiple", equipment_stage("compCool1,auxHeat1"))
        self.assertEqual("multiple", equipment_stage("compCool1,privateToken"))
        self.assertIsNone(equipment_stage(None))

    def test_projection_is_a_complete_native_enum(self) -> None:
        projection = PROJECTIONS[SUFFIX_EQUIPMENT_STAGE]

        self.assertEqual(SensorDeviceClass.ENUM, projection.device_class)
        self.assertEqual(EQUIPMENT_STAGE_OPTIONS, projection.options)
        self.assertIsNone(projection.unit)
        self.assertIsNone(projection.state_class)
        self.assertEqual(
            len(EQUIPMENT_STAGE_OPTIONS), len(set(EQUIPMENT_STAGE_OPTIONS))
        )
        self.assertEqual(
            set(EQUIPMENT_STAGE_OPTIONS),
            {*_EQUIPMENT_STAGES.values(), "idle", "multiple", "unknown"},
        )

    def test_every_enum_option_has_a_translation(self) -> None:
        root = (
            Path(__file__).resolve().parents[1] / "custom_components" / "ecobee_unified"
        )

        for path in (root / "strings.json", root / "translations" / "en.json"):
            translations = json.loads(path.read_text(encoding="utf-8"))
            states = translations["entity"]["sensor"]["equipment_stage"]["state"]
            self.assertEqual(set(EQUIPMENT_STAGE_OPTIONS), set(states))
            self.assertTrue(all(states.values()))


if __name__ == "__main__":
    unittest.main()
