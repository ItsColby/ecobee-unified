"""Verify parallel container lanes preserve ordering, failure, and cleanup."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
GIT = shutil.which("git")
PODMAN_STAND_IN = r"""
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
events = Path(os.environ["MATRIX_EVENTS"])
failure = os.environ.get("MATRIX_FAIL")
if any("actionlint@" in arg for arg in args):
    (events / "actionlint.done").touch()
    sys.exit(0)
if any("hassfest@" in arg for arg in args):
    assert (events / "minimum.done").exists()
    assert (events / "current.done").exists()
    (events / "release.done").touch()
    sys.exit(0)
lane = ("current" if "requirements-ha-current.txt" in args[-1] else
        "minimum" if "requirements-ha-test.txt" in args[-1] else "unit")
mount = next(args[index + 1] for index, arg in enumerate(args[:-1])
             if arg == "-v" and ":/workspace:" in args[index + 1])
source, target, access = mount.split(":")
assert access == "ro"
assert args[:2] == ["run", "--rm"]
(events / (lane + ".mount")).write_text(source)
(events / (lane + ".started")).touch()
if lane == "unit":
    assert (events / "actionlint.done").exists()
    if failure == "ignored_tracked":
        assert not (Path(source) / "local-only.txt").exists()
        history = next(args[index + 1].split(":")[0]
                       for index, arg in enumerate(args[:-1])
                       if arg == "-v" and ":/source-history:" in args[index + 1])
        result = subprocess.run(
            [sys.executable, "-B", str(Path(source) / "scripts/check_public_safety.py"),
             "--history-repository", history],
            cwd=source,
            check=False,
        )
        sys.exit(result.returncode)
else:
    assert (events / "unit.done").exists()
    peer = "minimum" if lane == "current" else "current"
    deadline = time.monotonic() + 5
    while not (events / (peer + ".started")).exists():
        if time.monotonic() > deadline:
            raise SystemExit("The two HA lanes did not overlap")
        time.sleep(0.01)
    if failure == peer:
        while not (events / (peer + ".done")).exists():
            if time.monotonic() > deadline:
                raise SystemExit("The peer lane did not finish")
            time.sleep(0.01)
        time.sleep(0.05)
        assert Path(source).is_dir(), "Payload removed before both lanes finished"
(events / (lane + ".done")).touch()
sys.exit(23 if failure == lane else 0)
"""


@unittest.skipUnless(os.name == "posix" and BASH and GIT, "requires Linux Bash/Git")
class ParallelValidationTests(unittest.TestCase):
    """Use real shell jobs and snapshots with isolated external-tool stand-ins."""

    def run_matrix(
        self, failure: str = ""
    ) -> tuple[subprocess.CompletedProcess[str], set[str], bool]:
        with tempfile.TemporaryDirectory(prefix="parallel validation ") as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            runner = source / "scripts" / "verify-release-local.sh"
            shutil.copyfile(ROOT / "scripts/verify-release-local.sh", runner)
            if failure == "ignored_tracked":
                shutil.copyfile(
                    ROOT / "scripts/check_public_safety.py",
                    source / "scripts/check_public_safety.py",
                )
                (source / "README.md").write_text("public-safe", encoding="utf-8")
            events = root / "events"
            events.mkdir()
            binary = root / "bin"
            binary.mkdir()
            podman = binary / "podman"
            podman.write_text(
                f"#!{sys.executable}\n{PODMAN_STAND_IN}", encoding="utf-8"
            )
            podman.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "MATRIX_EVENTS": str(events),
                "MATRIX_FAIL": failure,
            }
            for arguments in (
                ("init", "-q"),
                ("config", "user.name", "Validation Fixture"),
                ("config", "user.email", "fixture@example.test"),
                ("add", "-A"),
                ("commit", "-qm", "Validation fixture"),
            ):
                subprocess.run(
                    [str(GIT), "-C", str(source), *arguments],
                    env=env,
                    check=True,
                    capture_output=True,
                )
            if failure == "ignored_tracked":
                (source / ".gitignore").write_text(
                    "README.md\nlocal-only.txt\n", encoding="utf-8"
                )
                private_content = "private address " + "192" + ".168.1.2"
                for filename in ("README.md", "local-only.txt"):
                    (source / filename).write_text(private_content, encoding="utf-8")
            result = subprocess.run(
                [str(BASH), str(runner), "all", "container"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            remaining_payload = any(
                Path(path.read_text()).exists() for path in events.glob("*.mount")
            )
            return result, {path.name for path in events.iterdir()}, remaining_payload

    def test_support_lanes_overlap_between_unit_and_release(self) -> None:
        result, events, remaining_payload = self.run_matrix()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue({"minimum.done", "current.done", "release.done"} <= events)
        self.assertFalse(remaining_payload)

    def test_either_failure_waits_for_both_lanes_and_blocks_release(self) -> None:
        for failure in ("minimum", "current"):
            with self.subTest(failure=failure):
                result, events, remaining_payload = self.run_matrix(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue({"minimum.done", "current.done"} <= events)
                self.assertNotIn("release.done", events)
                self.assertFalse(remaining_payload)

    def test_unit_failure_does_not_start_support_lanes(self) -> None:
        result, events, remaining_payload = self.run_matrix("unit")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("minimum.started", events)
        self.assertNotIn("current.started", events)
        self.assertNotIn("release.done", events)
        self.assertFalse(remaining_payload)

    def test_snapshot_scans_tracked_files_despite_new_ignore_rules(self) -> None:
        result, events, remaining_payload = self.run_matrix("ignored_tracked")
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("README.md: private IPv4 address", output)
        self.assertIn("Source archive README.md: private IPv4 address", output)
        self.assertNotIn("minimum.started", events)
        self.assertNotIn("current.started", events)
        self.assertNotIn("release.done", events)
        self.assertFalse(remaining_payload)


if __name__ == "__main__":
    unittest.main()
