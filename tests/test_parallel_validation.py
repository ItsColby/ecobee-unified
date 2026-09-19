"""Verify validation lanes preserve ordering, failure, and cleanup."""

from __future__ import annotations

import json
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
AFFECTED_PLANNER = r"""
import json
import os
import sys

if "--command" in sys.argv:
    print(":")
else:
    selected = os.environ["MATRIX_PLAN_LANES"].split()
    print(json.dumps({"jobs": {lane: lane in selected for lane in
          ("unit", "minimum", "current", "release", "hacs")}, "workflow": True}))
"""

PODMAN_STAND_IN = r"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
events = Path(os.environ["MATRIX_EVENTS"])
failure = os.environ.get("MATRIX_FAIL")
lanes = set(os.environ["MATRIX_LANES"].split())
if any("actionlint@" in arg for arg in args):
    (events / "actionlint.done").touch()
    sys.exit(0)
if any("hassfest@" in arg for arg in args):
    for lane in lanes & {"minimum", "current"}:
        assert (events / (lane + ".done")).exists()
    (events / "release.done").touch()
    sys.exit(0)
lane = ("current" if "requirements-ha-current.txt" in args[-1] else
        "minimum" if "requirements-ha-test.txt" in args[-1] else "unit")
(events / (lane + ".command")).write_text(json.dumps(args))
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
    if "unit" in lanes:
        assert (events / "unit.done").exists()
    peer = "minimum" if lane == "current" else "current"
    deadline = time.monotonic() + 5
    while peer in lanes and not (events / (peer + ".started")).exists():
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
    if peer in lanes and failure == peer:
        while not (events / (peer + ".done")).exists():
            if time.monotonic() > deadline:
                raise SystemExit("The peer lane did not finish")
            time.sleep(0.01)
        time.sleep(0.05)
        assert Path(source).is_dir(), "Payload removed before both lanes finished"
(events / (lane + ".done")).touch()
sys.exit(23 if failure == lane else 0)
"""
NATIVE_TOOL_STAND_IN = r"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
events = Path(os.environ["NATIVE_EVENTS"])
event = {"tool": name, "args": args, "path": sys.argv[0], "cwd": os.getcwd(),
         "history": os.environ.get("PUBLIC_SAFETY_HISTORY_REPOSITORY")}
kind = name
if name == "python" and args[:2] == ["-m", "venv"]:
    kind = "venv"
    event["environment"] = args[2]
elif name == "python" and args[:2] == ["-m", "pip"]:
    kind = "pip"
event["kind"] = kind
with (events / "commands.jsonl").open("a") as log:
    log.write(json.dumps(event) + "\n")
if os.environ.get("NATIVE_FAIL") == kind:
    sys.exit(37)
if kind == "venv":
    target = Path(args[2]) / "bin" / "python"
    target.parent.mkdir()
    shutil.copyfile(__file__, target)
    target.chmod(0o755)
elif kind == "pip":
    for tool in ("shellcheck", "pytest", "zizmor"):
        target = Path(sys.argv[0]).parent / tool
        shutil.copyfile(__file__, target)
        target.chmod(0o755)
elif name == "go":
    target = Path(os.environ["GOBIN"]) / "actionlint"
    shutil.copyfile(__file__, target)
    target.chmod(0o755)
elif name == "actionlint":
    shellcheck = shutil.which("shellcheck")
    assert shellcheck == str(Path(sys.argv[0]).parent / "shellcheck")
    subprocess.run([shellcheck, "--version"], check=True)
"""


@unittest.skipUnless(os.name == "posix" and BASH, "requires Linux Bash")
class NativeValidationTests(unittest.TestCase):
    """Run the native launcher with task-owned external-tool stand-ins."""

    def run_native(
        self, mode: str, failure: str = ""
    ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], list[str]]:
        with tempfile.TemporaryDirectory(prefix="native validation ") as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            runner = source / "scripts/verify-release-local.sh"
            shutil.copyfile(ROOT / "scripts/verify-release-local.sh", runner)
            # Native fixtures own their Git identity, independent of whether the
            # caller's worktree metadata uses Windows or Linux paths.
            subprocess.run(
                [str(GIT), "-c", "init.templateDir=", "init", "-q", str(source)],
                check=True,
                capture_output=True,
            )
            self.native_source = str(source)
            events = root / "events"
            events.mkdir()
            binary = root / "bin"
            binary.mkdir()
            scratch = root / "scratch"
            scratch.mkdir()
            for name in ("python", "go", "docker"):
                tool = binary / name
                tool.write_text(
                    f"#!{sys.executable}\n{NATIVE_TOOL_STAND_IN}", encoding="utf-8"
                )
                tool.chmod(0o755)
            result = subprocess.run(
                [
                    str(BASH),
                    str(runner),
                    mode,
                    "native",
                ],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
                    "TMPDIR": str(scratch),
                    "NATIVE_EVENTS": str(events),
                    "NATIVE_FAIL": failure,
                    "PUBLIC_SAFETY_HISTORY_REPOSITORY": "/synthetic-ambient-history",
                },
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            log = events / "commands.jsonl"
            commands = (
                [json.loads(line) for line in log.read_text().splitlines()]
                if log.exists()
                else []
            )
            return result, commands, [path.name for path in scratch.iterdir()]

    def test_native_actionlint_provisions_shellcheck_and_cleans_every_exit(
        self,
    ) -> None:
        for failure in ("venv", "pip", "go", "actionlint", ""):
            with self.subTest(failure=failure):
                result, events, remaining = self.run_native("unit", failure)
                self.assertEqual(result.returncode, 37 if failure else 0, result.stderr)
                self.assertEqual(remaining, [])
                kinds = [event["kind"] for event in events]
                expected = ["venv", "pip", "go", "actionlint", "shellcheck"]
                if failure:
                    self.assertEqual(kinds, expected[: expected.index(failure) + 1])
                else:
                    self.assertEqual(kinds[:5], expected)
                    self.assertEqual(events[3]["cwd"], self.native_source)
                    self.assertEqual(
                        Path(str(events[4]["path"])).parent,
                        Path(str(events[3]["path"])).parent,
                    )

    def test_all_native_lanes_use_separate_environments_and_original_history(
        self,
    ) -> None:
        result, events, remaining = self.run_native("all")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(remaining, [])
        environments = [
            str(event["environment"]) for event in events if "environment" in event
        ]
        self.assertEqual(len(environments), 4)  # Actionlint, unit, minimum, current.
        self.assertEqual(len(set(environments)), 4)
        lane_pips = [
            event
            for event in events
            if event["kind"] == "pip"
            and Path(str(event["path"])).parent
            in {Path(environment) / "bin" for environment in environments[1:]}
        ]
        self.assertTrue(lane_pips)
        self.assertTrue(
            all(event["history"] == self.native_source for event in lane_pips)
        )
        self.assertTrue(all(event["cwd"] == self.native_source for event in lane_pips))
        for environment in environments[1:]:
            self.assertTrue(
                any(
                    Path(str(event["path"])).parent == Path(environment) / "bin"
                    for event in lane_pips
                )
            )
        test_paths = [
            str(event["path"]) for event in events if event["tool"] == "pytest"
        ]
        self.assertEqual(
            test_paths,
            [str(Path(environment) / "bin/pytest") for environment in environments[2:]],
        )

    def test_native_python_lane_failure_stops_before_tests_and_cleans(self) -> None:
        for failure in ("venv", "pip"):
            with self.subTest(failure=failure):
                result, events, remaining = self.run_native("minimum", failure)
                self.assertEqual(result.returncode, 37, result.stderr)
                self.assertEqual(remaining, [])
                self.assertEqual(
                    [event["kind"] for event in events],
                    ["venv"] if failure == "venv" else ["venv", "pip"],
                )

    def test_native_failed_minimum_blocks_current_and_release(self) -> None:
        result, events, remaining = self.run_native("all", "pytest")
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual(remaining, [])
        self.assertEqual(sum(event["kind"] == "venv" for event in events), 3)
        self.assertEqual(sum(event["tool"] == "pytest" for event in events), 1)
        self.assertFalse(any(event["tool"] == "docker" for event in events))


@unittest.skipUnless(os.name == "posix" and BASH and GIT, "requires Linux Bash/Git")
class ParallelValidationTests(unittest.TestCase):
    """Use real shell jobs and snapshots with isolated external-tool stand-ins."""

    def run_matrix(
        self,
        failure: str = "",
        *,
        interrupt: signal.Signals | None = None,
        commands: dict[str, list[str]] | None = None,
        mode: str = "all",
        lanes: tuple[str, ...] = ("unit", "minimum", "current", "release"),
        only: str = "",
    ) -> tuple[subprocess.CompletedProcess[str], set[str], bool]:
        with tempfile.TemporaryDirectory(prefix="parallel validation ") as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            runner = source / "scripts" / "verify-release-local.sh"
            shutil.copyfile(ROOT / "scripts/verify-release-local.sh", runner)
            (source / "scripts/plan_validation.py").write_text(
                AFFECTED_PLANNER, encoding="utf-8"
            )
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
                "MATRIX_PLAN_LANES": " ".join(lanes),
                "MATRIX_LANES": only or " ".join(lanes),
                "MATRIX_FAIL": failure,
                "VALIDATION_PYTHON": sys.executable,
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
                [
                    str(BASH),
                    str(runner),
                    mode,
                    "container",
                    "",
                    *(["--only", only] if only else []),
                ],
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
            if commands is not None:
                commands.update(
                    (path.stem, json.loads(path.read_text()))
                    for path in events.glob("*.command")
                )
            return result, {path.name for path in events.iterdir()}, remaining_payload

    def test_container_provisioning_and_payload_failures(self) -> None:
        commands: dict[str, list[str]] = {}
        result, _, remaining_payload = self.run_matrix(commands=commands)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(remaining_payload)
        apt_calls = ["apt update -qq", "apt install -y -qq --no-install-recommends git"]
        for lane, failure, payload_status, expected_status, expected_calls in (
            ("unit", "", 0, 0, [*apt_calls, "payload"]),
            ("minimum", "", 0, 0, ["payload"]),
            ("current", "", 0, 0, ["payload"]),
            ("unit", "update", 0, 37, apt_calls[:1]),
            ("unit", "install", 0, 41, apt_calls),
            ("minimum", "", 43, 43, ["payload"]),
        ):
            with self.subTest(
                lane=lane, failure=failure, payload_status=payload_status
            ):
                args = commands[lane]
                command = args[args.index("bash") :]
                command[0] = str(BASH)
                # A function remains effective even if the login shell resets PATH.
                command[2] = r"""
apt-get() {
  printf 'apt %s\n' "$*" >&2
  if [[ "$PROVISION_FAIL" == update && "$1" == update ]]; then return 37; fi
  if [[ "$PROVISION_FAIL" == install && "$1" == install ]]; then return 41; fi
  return 0
}
""" + command[2]
                command[-1] = 'printf "payload\\n" >&2; exit "$PAYLOAD_STATUS"'
                probe = subprocess.run(
                    command,
                    env={
                        **os.environ,
                        "PROVISION_FAIL": failure,
                        "PAYLOAD_STATUS": str(payload_status),
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                self.assertEqual(probe.returncode, expected_status, probe.stderr)
                self.assertEqual(probe.stderr.splitlines(), expected_calls)

    def test_affected_selection_preserves_order_overlap_and_exclusions(self) -> None:
        for lanes, only in (
            (("unit", "minimum", "current", "release"), ""),
            (("minimum", "current"), ""),
            (("unit",), ""),
            (("minimum",), ""),
            (("current",), ""),
            (("release",), ""),
            (("minimum", "current"), "current"),
        ):
            with self.subTest(lanes=lanes, only=only):
                result, events, remaining = self.run_matrix(
                    mode="affected", lanes=lanes, only=only
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = set((only,) if only else lanes)
                self.assertEqual(
                    {
                        name.removesuffix(".done")
                        for name in events
                        if name.endswith(".done") and name != "actionlint.done"
                    },
                    expected,
                )
                self.assertFalse(remaining)

    def test_affected_failure_drains_selected_lanes_and_blocks_release(self) -> None:
        for failure in ("unit", "minimum", "current"):
            with self.subTest(failure=failure):
                result, events, remaining = self.run_matrix(failure, mode="affected")
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("release.done", events)
                if failure == "unit":
                    self.assertNotIn("minimum.started", events)
                    self.assertNotIn("current.started", events)
                else:
                    self.assertTrue({"minimum.done", "current.done"} <= events)
                self.assertFalse(remaining)

    def test_affected_interrupt_drains_selected_lanes(self) -> None:
        for interrupt in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(interrupt=interrupt):
                result, events, remaining = self.run_matrix(
                    mode="affected", interrupt=interrupt
                )
                self.assertEqual(result.returncode, 128 + interrupt)
                self.assertTrue({"minimum.done", "current.done"} <= events)
                self.assertNotIn("release.done", events)
                self.assertFalse(remaining)

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
