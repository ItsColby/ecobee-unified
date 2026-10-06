"""Static contracts for public metadata and translations."""

from __future__ import annotations

import ast
import json
import re
import tomllib
import unittest
from pathlib import Path

import yaml


def _core_pin(pyproject: Path) -> str:
    """Read the one stable exact Core pin in the ha-current dependency group."""
    group = tomllib.loads(pyproject.read_text(encoding="utf-8"))["dependency-groups"][
        "ha-current"
    ]
    pins = [
        match.group(1)
        for requirement in group
        if (
            match := re.fullmatch(
                r"homeassistant==([0-9]{4}\.(?:[1-9]|1[0-2])\.(?:0|[1-9][0-9]*))",
                requirement,
            )
        )
    ]
    if len(pins) != 1:
        raise AssertionError("ha-current must contain one exact Home Assistant pin")
    return pins[0]


class MetadataTests(unittest.TestCase):
    def test_declared_minimum_matches_pinned_core(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pinned = _core_pin(root / "pyproject.toml")
        hacs = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs["homeassistant"], pinned)

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
