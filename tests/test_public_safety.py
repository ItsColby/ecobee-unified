"""Tests for the repository privacy and workflow contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.check_public_safety import (
    REVIEWED_BINARY_SHA256,
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

    def test_current_tree_ignores_private_local_control_overlays(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("public-safe", encoding="utf-8")
            overlay = root / ".codex" / "rules"
            overlay.mkdir(parents=True)
            (overlay / "publication.rules").write_bytes(b"\x00private local overlay")

            count, failures = run_guard(root)

        self.assertEqual(1, count)
        self.assertEqual([], failures)

    def test_reviewed_brand_asset_is_hash_pinned(self) -> None:
        root = Path(__file__).resolve().parents[1]
        relative = "custom_components/ecobee_unified/brand/icon.png"
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        self.assertEqual(REVIEWED_BINARY_SHA256[relative], digest)

    def test_tracked_source_archive_is_public_safe(self) -> None:
        root = Path(__file__).resolve().parents[1]
        count, failures = run_archive_guard(root)
        self.assertGreater(count, 20)
        self.assertEqual([], failures)

    def test_tracked_archive_reads_staged_bytes_not_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._git(root, "init")
            readme = root / "README.md"
            readme.write_text("private address " + "192" + ".168.1.2", encoding="utf-8")
            self._git(root, "add", "README.md")
            readme.write_text("public-safe worktree", encoding="utf-8")

            count, failures = run_archive_guard(root)

        self.assertEqual(1, count)
        self.assertIn("Source archive README.md: private IPv4 address", failures)

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

    def test_support_and_ci_have_exact_minimum_and_current_core_lanes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/validate.yaml").read_text(
            encoding="utf-8"
        )
        minimum_requirements = (root / "requirements-ha-test.txt").read_text(
            encoding="utf-8"
        )
        current_requirements = (root / "requirements-ha-current.txt").read_text(
            encoding="utf-8"
        )
        hacs = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
        manifest = json.loads(
            (root / "custom_components/ecobee_unified/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        icons = json.loads(
            (root / "custom_components/ecobee_unified/icons.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("homeassistant==2026.8.0", minimum_requirements.strip())
        self.assertEqual("homeassistant==2026.8.1", current_requirements.strip())
        self.assertEqual("2026.8.0", hacs["homeassistant"])
        self.assertIs(True, manifest["single_config_entry"])
        self.assertIn(
            "Home Assistant integration tests (Core 2026.8.0 minimum)", workflow
        )
        self.assertIn(
            "Home Assistant integration tests (Core 2026.8.1 current)", workflow
        )
        self.assertEqual(2, workflow.count("pytest-homeassistant-custom-component=="))
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
        self.assertEqual(
            "mdi:hvac",
            icons["entity"]["sensor"]["equipment_stage"]["default"],
        )
        minimum_harness = (
            'python -m pip install "pytest-homeassistant-custom-component==0.13.354"'
        )
        current_harness = (
            'python -m pip install "pytest-homeassistant-custom-component==0.13.355"'
        )
        minimum_core = "python -m pip install --upgrade -r requirements-ha-test.txt"
        current_core = "python -m pip install --upgrade -r requirements-ha-current.txt"
        dependency_check = "python -m pip check"
        all_tests = "pytest tests -q"
        minimum_lane = workflow[
            workflow.index("  home_assistant_minimum:") : workflow.index(
                "  home_assistant_current:"
            )
        ]
        current_lane = workflow[
            workflow.index("  home_assistant_current:") : workflow.index("  hassfest:")
        ]
        for lane, harness, core in (
            (minimum_lane, minimum_harness, minimum_core),
            (current_lane, current_harness, current_core),
        ):
            self.assertLess(lane.index(harness), lane.index(core))
            self.assertLess(lane.index(core), lane.index(dependency_check))
            self.assertLess(lane.index(dependency_check), lane.index(all_tests))
        self.assertIn(
            "needs: [unit, home_assistant_minimum, home_assistant_current, hassfest, hacs]",
            workflow,
        )
        self.assertNotIn("python -m unittest tests.test_models", workflow)


if __name__ == "__main__":
    unittest.main()
