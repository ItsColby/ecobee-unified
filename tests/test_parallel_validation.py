"""Verify validation lanes preserve ordering, failure, and cleanup."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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
    if os.environ.get("MATRIX_INTERRUPT"):
        while not (events / "interrupt.sent").exists():
            if time.monotonic() > deadline:
                raise SystemExit("The runner was not interrupted")
            time.sleep(0.01)
        time.sleep(0.1)
        assert Path(source).is_dir(), "Payload removed while interrupted lanes were active"
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
GO_STAND_IN = r"""
import os
import sys
from pathlib import Path

events = Path(os.environ["ACTIONLINT_EVENTS"])
binary = Path(os.environ["GOBIN"])
(events / "binary.path").write_text(str(binary))
if os.environ.get("ACTIONLINT_FAIL") == "install":
    sys.exit(37)
actionlint = binary / "actionlint"
actionlint.write_text(
    '#!/bin/sh\n'
    'touch "$ACTIONLINT_EVENTS/actionlint.ran"\n'
    'exit "${ACTIONLINT_STATUS:-0}"\n'
)
actionlint.chmod(0o755)
"""


@unittest.skipUnless(os.name == "posix" and BASH, "requires Linux Bash")
class NativeActionlintValidationTests(unittest.TestCase):
    """Exercise the native runner without installing or invoking real tools."""

    def test_native_actionlint_cleans_temporary_binary_on_every_exit(self) -> None:
        for failure, expected_status in (("install", 37), ("lint", 41), ("", 0)):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory(prefix="native validation ") as temporary,
            ):
                root = Path(temporary)
                events = root / "events"
                events.mkdir()
                binary = root / "bin"
                binary.mkdir()
                scratch = root / "scratch"
                scratch.mkdir()
                go = binary / "go"
                go.write_text(f"#!{sys.executable}\n{GO_STAND_IN}", encoding="utf-8")
                go.chmod(0o755)
                # The real outer Bash runs the script; only its login-shell
                # Python lane is replaced, so the test cannot install packages.
                login_shell = binary / "bash"
                login_shell.write_text(
                    '#!/bin/sh\n[ "$1" = "-lc" ] || exit 99\n'
                    'touch "$ACTIONLINT_EVENTS/python.started"\n',
                    encoding="utf-8",
                )
                login_shell.chmod(0o755)
                result = subprocess.run(
                    [
                        str(BASH),
                        str(ROOT / "scripts/verify-release-local.sh"),
                        "unit",
                        "native",
                    ],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
                        "TMPDIR": str(scratch),
                        "ACTIONLINT_EVENTS": str(events),
                        "ACTIONLINT_FAIL": failure,
                        "ACTIONLINT_STATUS": "41" if failure == "lint" else "0",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                self.assertEqual(
                    result.returncode, expected_status, result.stdout + result.stderr
                )
                temporary_binary = Path((events / "binary.path").read_text())
                self.assertEqual(temporary_binary.parent, scratch)
                self.assertEqual(
                    (events / "actionlint.ran").exists(), failure != "install"
                )
                self.assertEqual((events / "python.started").exists(), not failure)
                self.assertFalse(temporary_binary.exists())
                self.assertEqual(list(scratch.iterdir()), [])


@unittest.skipUnless(os.name == "posix" and BASH and GIT, "requires Linux Bash/Git")
class ParallelValidationTests(unittest.TestCase):
    """Use real shell jobs and snapshots with isolated external-tool stand-ins."""

    def run_matrix(
        self, failure: str = "", *, interrupt: signal.Signals | None = None
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
                "MATRIX_INTERRUPT": "1" if interrupt else "",
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
            with subprocess.Popen(
                [str(BASH), str(runner), "all", "container"],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as process:
                try:
                    if interrupt:
                        deadline = time.monotonic() + 10
                        while not all(
                            (events / f"{lane}.started").exists()
                            for lane in ("minimum", "current")
                        ):
                            if (
                                process.poll() is not None
                                or time.monotonic() > deadline
                            ):
                                self.fail("The two HA lanes did not start")
                            time.sleep(0.01)
                        process.send_signal(interrupt)
                        (events / "interrupt.sent").touch()
                    stdout, stderr = process.communicate(timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=10)
            result = subprocess.CompletedProcess(
                process.args, process.returncode, stdout, stderr
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

    def test_interrupt_preserves_status_and_waits_before_cleanup(self) -> None:
        for interrupt in (signal.SIGINT, signal.SIGTERM):
            for failure in ("", "minimum"):
                with self.subTest(interrupt=interrupt, failure=failure):
                    result, events, remaining_payload = self.run_matrix(
                        failure, interrupt=interrupt
                    )
                    self.assertTrue(
                        {"minimum.done", "current.done"} <= events,
                        result.stdout + result.stderr,
                    )
                    self.assertNotIn("release.done", events)
                    self.assertFalse(remaining_payload)
                    self.assertEqual(result.returncode, 128 + interrupt)

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
