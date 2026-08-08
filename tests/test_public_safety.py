"""Tests for the repository privacy and workflow contract."""

from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.check_public_safety import (
    _history_failures,
    _text_failures,
    run_archive_guard,
    run_guard,
)


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

    def test_tracked_source_archive_is_public_safe(self) -> None:
        root = Path(__file__).resolve().parents[1]
        count, failures = run_archive_guard(root)
        self.assertGreater(count, 20)
        self.assertEqual([], failures)

    def test_tracked_maintainer_agent_artifacts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._git(root, "init")
            (root / "AGENTS.md").write_text("private workflow", encoding="utf-8")
            self._git(root, "add", "AGENTS.md")

            _count, failures = run_archive_guard(root)

        self.assertIn("Source archive AGENTS.md: maintainer agent artifact", failures)

    def test_pytest_collects_async_home_assistant_tests(self) -> None:
        root = Path(__file__).resolve().parents[1]
        configuration = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "auto", configuration["tool"]["pytest"]["ini_options"]["asyncio_mode"]
        )

    def test_retired_history_content_and_binary_blobs_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._git(root, "init")
            (root / "retired.txt").write_text(
                "private address " + "192" + ".168.1.2", encoding="utf-8"
            )
            (root / "retired.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00private")
            self._git(root, "add", "retired.txt", "retired.bin")
            self._git(root, "commit", "-m", "Add retired evidence")
            (root / "retired.txt").unlink()
            (root / "retired.bin").unlink()
            self._git(root, "add", "--update")
            self._git(
                root,
                "commit",
                "-m",
                "Remove retired evidence for " + "person" + "@real-domain.dev",
            )

            failures = _history_failures(root)

        self.assertIn("Git history blob: private IPv4 address", failures)
        self.assertIn("Git history metadata: non-example email address", failures)
        self.assertIn("Git history filename: unreviewed binary content", failures)
        self.assertIn("Git history blob: non-UTF-8 content", failures)

    @staticmethod
    def _git(root: Path, *arguments: str) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Example Maintainer",
                "-c",
                "user.email=maintainer@example.com",
                *arguments,
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )

    def test_support_and_ci_have_one_exact_core_lane(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/validate.yaml").read_text(
            encoding="utf-8"
        )
        requirements = (root / "requirements-ha-test.txt").read_text(encoding="utf-8")
        hacs = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
        icons = json.loads(
            (root / "custom_components/ecobee_unified/icons.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("homeassistant==2026.8.0", requirements.strip())
        self.assertEqual("2026.8.0", hacs["homeassistant"])
        self.assertIn(
            "Home Assistant integration tests (Core 2026.8.0 minimum)", workflow
        )
        self.assertEqual(1, workflow.count("pytest-homeassistant-custom-component=="))
        self.assertNotIn("matrix.", workflow)
        self.assertEqual(
            {
                "create_vacation",
                "delete_vacation",
                "resume_program",
                "set_occupancy_modes",
                "set_sensors_used_in_climate",
            },
            set(icons["services"]),
        )
        harness = (
            'python -m pip install "pytest-homeassistant-custom-component==0.13.354"'
        )
        core = "python -m pip install --upgrade -r requirements-ha-test.txt"
        dependency_check = "python -m pip check"
        all_tests = "pytest tests -q"
        self.assertLess(workflow.index(harness), workflow.index(core))
        self.assertLess(workflow.index(core), workflow.index(dependency_check))
        self.assertLess(workflow.index(dependency_check), workflow.index(all_tests))
        self.assertNotIn("python -m unittest tests.test_models", workflow)


if __name__ == "__main__":
    unittest.main()
