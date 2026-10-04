"""Static contracts for public metadata and translations."""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

import yaml


def _exact_core_pin(path: Path) -> str:
    """Read one unconditional exact Core pin, allowing other requirements."""
    pins = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        content = line.split("#", 1)[0].strip()
        if not re.match(r"homeassistant(?=[^A-Za-z0-9_.-]|$)", content, re.IGNORECASE):
            continue
        match = re.fullmatch(
            r"homeassistant\s*==\s*([0-9]{4}\.(?:[1-9]|1[0-2])\.(?:0|[1-9][0-9]*))",
            content,
            re.IGNORECASE,
        )
        if match is None:
            raise AssertionError(
                f"{path.name} must use an unconditional stable exact Home Assistant pin"
            )
        pins.append(match.group(1))
    if len(pins) != 1:
        raise AssertionError(f"{path.name} must contain exactly one Home Assistant pin")
    return pins[0]


class MetadataTests(unittest.TestCase):
    def test_declared_minimum_matches_distribution_requirement(self) -> None:
        root = Path(__file__).resolve().parents[1]
        minimum = _exact_core_pin(root / "requirements-ha-test.txt")
        hacs = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs["homeassistant"], minimum)

    def test_reconfigure_menu_has_complete_runtime_translations(self) -> None:
        root = (
            Path(__file__).resolve().parents[1] / "custom_components" / "ecobee_unified"
        )
        constants = ast.parse((root / "const.py").read_text(encoding="utf-8"))
        menu_options = next(
            ast.literal_eval(node.value)
            for node in constants.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "RECONFIGURE_MENU_OPTIONS"
        )
        self.assertFalse((root / "strings.json").exists())
        translations = json.loads(
            (root / "translations" / "en.json").read_text(encoding="utf-8")
        )
        reconfigure = translations["config"]["step"]["reconfigure"]
        self.assertTrue(reconfigure["title"].strip())
        self.assertTrue(reconfigure["description"].strip())
        labels = reconfigure["menu_options"]
        self.assertEqual(set(menu_options), set(labels))
        self.assertTrue(all(label.strip() for label in labels.values()))

    def test_user_facing_fields_have_nonblank_descriptions(self) -> None:
        root = (
            Path(__file__).resolve().parents[1] / "custom_components" / "ecobee_unified"
        )
        path = root / "translations" / "en.json"
        translations = json.loads(path.read_text(encoding="utf-8"))
        for owner in ("config", "options"):
            for step_name, step in translations[owner]["step"].items():
                data = step.get("data", {})
                if not data:
                    continue
                descriptions = step.get("data_description", {})
                self.assertEqual(set(data), set(descriptions), (path, step_name))
                self.assertTrue(
                    all(value.strip() for value in descriptions.values()),
                    (path, step_name),
                )

        services = yaml.safe_load((root / "services.yaml").read_text(encoding="utf-8"))
        self.assertTrue(services)
        undescribed = [
            f"{action}.{field}"
            for action, spec in services.items()
            for field, field_spec in spec.get("fields", {}).items()
            if not str(field_spec.get("description", "")).strip()
        ]
        self.assertEqual([], undescribed)
