"""Tests for the repository privacy and workflow contract."""

from __future__ import annotations

import ast
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
        validation_plan = (root / "docs/validation-plan.md").read_text(encoding="utf-8")
        release_runner = (root / "scripts/verify-release-local.sh").read_text(
            encoding="utf-8"
        )
        release_wrapper = (root / "scripts/verify-release-local.ps1").read_text(
            encoding="utf-8"
        )
        dependabot = (root / ".github/dependabot.yml").read_text(encoding="utf-8")
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
        self.assertEqual("hub", manifest["integration_type"])
        self.assertIn(
            "Home Assistant integration tests (Core 2026.8.0 minimum)", workflow
        )
        self.assertIn(
            "Home Assistant integration tests (Core 2026.8.1 current)", workflow
        )
        self.assertEqual(
            2, release_runner.count("pytest-homeassistant-custom-component==")
        )
        self.assertIn(
            '"${source_git[@]}" ls-files --cached --others --exclude-standard -z',
            release_runner,
        )
        self.assertIn('--git-dir="$source_git_dir"', release_runner)
        self.assertIn("rev-parse --path-format=absolute --git-dir", release_wrapper)
        self.assertIn("$Mode container $linuxGitDir", release_wrapper)
        self.assertIn('tar -C "$source_root" --null --files-from=-', release_runner)
        self.assertIn('chmod a+rx "$repo_root"', release_runner)
        self.assertNotIn('cp -a "$source_root/."', release_runner)
        self.assertIn("bash scripts/verify-release-local.sh minimum native", workflow)
        self.assertIn("bash scripts/verify-release-local.sh current native", workflow)
        self.assertNotIn("matrix.", workflow)
        self.assertNotIn("ubuntu-latest", workflow)
        self.assertEqual(6, workflow.count("runs-on: ubuntu-24.04"))
        self.assertIn("CodeQL default setup is active", validation_plan)
        self.assertIn("Zizmor auditor", validation_plan)
        self.assertIn('"shellcheck-py==0.11.0.1" "zizmor==1.29.0"', release_runner)
        self.assertIn("shellcheck scripts/verify-release-local.sh", release_runner)
        self.assertIn("zizmor --strict-collection --persona auditor .", release_runner)
        self.assertEqual(1, dependabot.count("package-ecosystem: github-actions"))
        self.assertEqual(1, dependabot.count("interval: weekly"))
        self.assertIn("default-days: 7", dependabot)
        self.assertNotIn("package-ecosystem: pip", dependabot)
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
        minimum_lane = release_runner[
            release_runner.index("run_minimum()") : release_runner.index(
                "run_current()"
            )
        ]
        current_lane = release_runner[
            release_runner.index("run_current()") : release_runner.index(
                "run_release()"
            )
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

    def test_readme_distinguishes_reconfigure_from_options(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "Use the entry's **Reconfigure** action to add, edit, or remove "
            "thermostat\nmappings.",
            readme,
        )
        self.assertIn(
            "Use **Configure** to change the Ecobee source-staleness and command-\n"
            "confirmation timing thresholds.",
            readme,
        )
        self.assertNotIn(
            "Use **Configure** on\nthat entry to add, edit, or remove thermostat "
            "mappings.",
            readme,
        )

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
        for path in (root / "strings.json", root / "translations" / "en.json"):
            translations = json.loads(path.read_text(encoding="utf-8"))
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
        for path in (root / "strings.json", root / "translations" / "en.json"):
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
                field_descriptions[f"{current_action}.{current_field}"] = bool(
                    line.partition(":")[2].strip()
                )
        self.assertTrue(field_descriptions)
        self.assertTrue(
            all(field_descriptions.values()),
            [name for name, described in field_descriptions.items() if not described],
        )


if __name__ == "__main__":
    unittest.main()
