"""Tests for the repository privacy and workflow contract."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.check_public_safety import _text_failures, run_guard


class PublicSafetyTests(unittest.TestCase):
    def test_generic_patterns_reject_sensitive_shapes(self) -> None:
        samples = {
            "absolute Windows user path": "C:" + r"\Users\Example\file.txt",
            "absolute Unix user path": "/" + "home/example/private.txt",
            "local hostname": "router" + ".local",
            "non-example email address": "person" + "@real-domain.dev",
            "private IPv4 address": "192" + ".168.1.2",
            "credential-like token": "ghp_" + ("a" * 36),
        }
        for expected, sample in samples.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, _text_failures(sample))

    def test_public_examples_and_github_noreply_are_allowed(self) -> None:
        text = "person@example.com 1361774+ItsColby@users.noreply.github.com"
        self.assertEqual(set(), _text_failures(text))

    def test_current_tree_is_text_only_and_public_safe(self) -> None:
        root = Path(__file__).resolve().parents[1]
        count, failures = run_guard(root)
        self.assertGreater(count, 20)
        self.assertEqual([], failures)

    def test_support_and_ci_have_one_exact_core_lane(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/validate.yaml").read_text(
            encoding="utf-8"
        )
        requirements = (root / "requirements-ha-test.txt").read_text(encoding="utf-8")
        hacs = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual("homeassistant==2026.8.0", requirements.strip())
        self.assertEqual("2026.8.0", hacs["homeassistant"])
        self.assertIn("Home Assistant integration tests (Core 2026.8.0)", workflow)
        self.assertEqual(1, workflow.count("pytest-homeassistant-custom-component=="))
        self.assertNotIn("matrix.", workflow)
        harness = (
            'python -m pip install "pytest-homeassistant-custom-component==0.13.354"'
        )
        core = "python -m pip install --upgrade -r requirements-ha-test.txt"
        dependency_check = "python -m pip check"
        self.assertLess(workflow.index(harness), workflow.index(core))
        self.assertLess(workflow.index(core), workflow.index(dependency_check))


if __name__ == "__main__":
    unittest.main()
