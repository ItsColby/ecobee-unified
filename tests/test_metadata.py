"""Static contracts for public metadata and translations."""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


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


def _has_description_text(value: str) -> bool:
    """Check the maintained single-line plain or quoted description format."""
    value = re.sub(
        r"""("(?:\\.|[^"\\])*"|'(?:''|[^'])*')|(?<!\S)#.*""",
        lambda match: match[1] or "",
        value,
    ).strip()
    if value.startswith(("'", '"')):
        try:
            value = ast.literal_eval(value)
        except SyntaxError, ValueError:
            return False
    return isinstance(value, str) and bool(value.strip())


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

    def test_description_text_rejects_empty_scalars_and_preserves_quoted_hashes(
        self,
    ) -> None:
        for value in (
            "",
            "   ",
            "# comment",
            '""',
            "''",
            '"   " # comment',
            "'  ' # comment",
            '"\\t"',
        ):
            with self.subTest(value=value):
                self.assertFalse(_has_description_text(value))
        for value in (
            "A description",
            "A description # comment",
            '"# content"',
            "'# content' # comment",
            '"A description" # comment',
            "A #comment-free word",
            '" # content"',
            '"A \\"quoted\\" description"',
        ):
            with self.subTest(value=value):
                self.assertTrue(_has_description_text(value))

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

        services_lines = (
            (root / "services.yaml").read_text(encoding="utf-8").splitlines()
        )
        field_descriptions: dict[str, bool] = {}
        in_fields = False
        current_action = ""
        current_field: str | None = None
        for line in services_lines:
            if line and not line.startswith(" ") and line.endswith(":"):
                current_action = line[:-1]
                in_fields = False
                current_field = None
            elif line == "  fields:":
                in_fields = True
                current_field = None
            elif (
                in_fields and line.startswith("    ") and not line.startswith("      ")
            ):
                if line.endswith(":"):
                    current_field = line.strip()[:-1]
                    field_descriptions[f"{current_action}.{current_field}"] = False
            elif (
                in_fields
                and current_field is not None
                and line.startswith("      description:")
            ):
                field_descriptions[f"{current_action}.{current_field}"] = (
                    _has_description_text(line.partition(":")[2])
                )
        self.assertTrue(field_descriptions)
        self.assertTrue(
            all(field_descriptions.values()),
            [name for name, described in field_descriptions.items() if not described],
        )
