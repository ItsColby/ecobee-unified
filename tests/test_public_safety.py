"""Tests for the repository public-payload and workflow contract."""

from __future__ import annotations

import ast
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.check_public_safety import (
    MAX_HISTORY_BLOB_BYTES,
    _git_object_bytes,
    _history_failures,
    _read_batch_object,
    _text_failures,
    run_archive_guard,
    run_guard,
)


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


class PublicSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("GIT_")
        }
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        environment_patch = patch.dict(os.environ, environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

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

    def test_reviewed_public_url_is_allowed_without_exempting_other_content(
        self,
    ) -> None:
        url = (
            "https://www.ecobee.com/"
            "home/developer/api/documentation/v1/objects/Runtime.shtml"
        )
        for text in (url, f"[Ecobee Runtime]({url})", f"<{url}>"):
            with self.subTest(text=text):
                self.assertEqual(set(), _text_failures(text))

        unsafe_samples = {
            "absolute Unix user path": "/" + "home/example/private.txt",
            "private IPv4 address": "192" + ".168.1.2",
            "credential-like token": "ghp_" + ("a" * 36),
        }
        for expected, sample in unsafe_samples.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, _text_failures(f"{url} {sample}"))

    def test_unix_path_url_allowance_requires_the_exact_reviewed_url(self) -> None:
        path = "/" + "home/developer/api/documentation/v1/objects/Runtime.shtml"
        url = "https://www.ecobee.com" + path
        for text in (
            path,
            "https://example.com" + path,
            "http://www.ecobee.com" + path,
            url.replace("Runtime.shtml", "private.txt"),
            url + "/private.txt",
            url + "?extra=value",
            url + "#fragment",
            "https://example.com/" + url,
            "/" + url,
        ):
            with self.subTest(text=text):
                self.assertIn("absolute Unix user path", _text_failures(text))

    def test_current_tree_uses_git_candidate_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._git(root, "init")
            (root / "README.md").write_text("public-safe", encoding="utf-8")
            local_only = root / "local-only"
            local_only.mkdir()
            (local_only / "scratch.bin").write_bytes(b"\x00local checkout data")
            (root / ".git" / "info" / "exclude").write_text(
                "local-only/\n", encoding="utf-8"
            )

            count, failures = run_guard(root)

        self.assertEqual(1, count)
        self.assertEqual([], failures)

    def test_current_tree_scans_nonignored_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._git(root, "init")
            (root / "README.md").write_text("public-safe", encoding="utf-8")
            (root / "local.txt").write_text(
                "private address " + "192" + ".168.1.2", encoding="utf-8"
            )

            count, failures = run_guard(root)

        self.assertEqual(2, count)
        self.assertIn("local.txt: private IPv4 address", failures)

    def test_working_tree_refuses_linked_candidates_before_reading(self) -> None:
        for linked_part, kind in (
            ("candidate", "symlink"),
            ("ancestor", "junction"),
            ("candidate", "reparse"),
            ("ancestor", "reparse"),
        ):
            with (
                self.subTest(part=linked_part, kind=kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                relative = Path("nested") / "linked.txt"
                candidate = root / relative
                candidate.parent.mkdir()
                candidate.write_text("ordinary public text", encoding="utf-8")
                linked = candidate if linked_part == "candidate" else candidate.parent
                native_lstat = Path.lstat

                def lstat(path, *, linked=linked, kind=kind, native_lstat=native_lstat):
                    info = native_lstat(path)
                    if path == linked and kind == "reparse":
                        return SimpleNamespace(
                            st_mode=info.st_mode,
                            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                        )
                    return info

                result = subprocess.CompletedProcess(
                    [], 0, relative.as_posix().encode() + b"\0"
                )
                with (
                    patch(
                        "scripts.check_public_safety.subprocess.run",
                        return_value=result,
                    ),
                    patch.object(
                        Path,
                        "is_symlink",
                        autospec=True,
                        side_effect=lambda path, linked=linked, kind=kind: (
                            path == linked and kind == "symlink"
                        ),
                    ),
                    patch.object(
                        Path,
                        "is_junction",
                        autospec=True,
                        side_effect=lambda path, linked=linked, kind=kind: (
                            path == linked and kind == "junction"
                        ),
                    ),
                    patch.object(Path, "lstat", autospec=True, side_effect=lstat),
                    patch.object(
                        Path, "read_bytes", return_value=b"ordinary public text"
                    ) as read_bytes,
                ):
                    count, failures = run_guard(root)
                read_bytes.assert_not_called()
                self.assertEqual(0, count)
                self.assertEqual(
                    [f"{relative}: unreviewed symbolic link or reparse point"], failures
                )

    def test_sensitive_filenames_are_opaque_in_worktree_and_archive(self) -> None:
        filename = "person" + "@real-domain.dev.txt"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init")
            (root / filename).write_text("192" + ".168.1.2", encoding="utf-8")
            self._git(root, "add", filename)
            for guard in (run_guard, run_archive_guard):
                with self.subTest(guard=guard.__name__):
                    count, failures = guard(root)
                    self.assertEqual(1, count)
                    self.assertEqual(2, len(failures))
                    self.assertTrue(all(filename not in item for item in failures))
                    self.assertTrue(
                        all("sensitive filename [" in item for item in failures)
                    )
                    self.assertTrue(
                        any(
                            "filename non-example email address" in item
                            for item in failures
                        )
                    )
                    self.assertTrue(
                        any("private IPv4 address" in item for item in failures)
                    )

    def test_guards_refuse_inherited_repository_selectors(self) -> None:
        selectors = (
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG",
            "GIT_CONFIG_PARAMETERS",
            "GIT_CONFIG_COUNT",
            "GIT_OBJECT_DIRECTORY",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_IMPLICIT_WORK_TREE",
            "GIT_GRAFT_FILE",
            "GIT_INDEX_FILE",
            "GIT_REPLACE_REF_BASE",
            "GIT_PREFIX",
            "GIT_SHALLOW_FILE",
            "GIT_COMMON_DIR",
            "GIT_CEILING_DIRECTORIES",
            "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        )
        for name in selectors:
            for guard in (run_guard, run_archive_guard, _history_failures):
                with (
                    self.subTest(name=name, guard=guard.__name__),
                    patch.dict(os.environ, {name: ""}),
                    self.assertRaisesRegex(ValueError, name),
                ):
                    guard(Path("unused-explicit-source"))

    def test_history_reads_original_blobs_despite_replacement_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init")
            (root / "retired.txt").write_text("192" + ".168.1.2", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial content")
            original = subprocess.check_output(
                ["git", "rev-parse", "HEAD:retired.txt"], cwd=root, text=True
            ).strip()
            replacement = subprocess.check_output(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=root,
                input="safe",
                text=True,
            ).strip()
            self._git(root, "replace", original, replacement)
            with patch.dict(os.environ):
                os.environ.pop("GIT_NO_REPLACE_OBJECTS", None)
                self.assertIn(
                    "Git history blob: private IPv4 address", _history_failures(root)
                )

    def test_batch_preserves_empty_and_binary_objects_and_settles_early_close(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init")
            contents = [b"", b"line\n\x00\xfftail\n", b"last"]
            objects = [
                (
                    subprocess.check_output(
                        ["git", "hash-object", "-w", "--stdin"], cwd=root, input=data
                    )
                    .decode()
                    .strip(),
                    "blob",
                    len(data),
                )
                for data in contents
            ]
            self.assertEqual(contents, list(_git_object_bytes(root, objects)))
            native_popen = subprocess.Popen
            processes = []

            def capture_process(*args, **kwargs):
                process = native_popen(*args, **kwargs)
                processes.append(process)
                return process

            with patch("scripts.check_public_safety.subprocess.Popen", capture_process):
                stream = _git_object_bytes(root, objects)
                self.assertEqual(contents[0], next(stream))
                stream.close()
            self.assertIsNotNone(processes[0].poll())
            self.assertTrue(processes[0].stdin.closed)
            self.assertTrue(processes[0].stdout.closed)

    def test_batch_rejects_invalid_or_incomplete_frames_without_exposing_bytes(
        self,
    ) -> None:
        object_id = "1" * 40
        prefix = object_id.encode()
        cases = [
            prefix + b" missing\n",
            b"2" * 40 + b" blob 1\nx\n",
            prefix + b" tag 1\nx\n",
            prefix + b" blob -1\n",
            prefix + b" blob 2\nxx\n",
            prefix + b" blob 1\n",
            prefix + b" blob 1\nx",
            prefix + b" blob 1\nxx",
            prefix + b" blob " + b"0" * 256 + b"\n",
        ]
        for payload in cases:
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(ValueError, "Git object batch"),
            ):
                _read_batch_object(io.BytesIO(payload), object_id, "blob", 1)

    def test_batch_missing_object_reaps_failed_reader(self) -> None:
        native_popen = subprocess.Popen
        processes = []

        def capture_process(*args, **kwargs):
            process = native_popen(*args, **kwargs)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init")
            with (
                patch("scripts.check_public_safety.subprocess.Popen", capture_process),
                self.assertRaisesRegex(ValueError, "invalid header"),
            ):
                list(_git_object_bytes(root, [("0" * 40, "blob", None)]))
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdin.closed)
        self.assertTrue(processes[0].stdout.closed)

    def test_batch_refuses_nonzero_exit_and_unexpected_trailing_content(self) -> None:
        object_id = "1" * 40
        native_popen = subprocess.Popen
        for extra, status, error in (
            (b"", 7, subprocess.CalledProcessError),
            (b"extra", 0, ValueError),
        ):
            processes = []
            payload = object_id.encode() + b" blob 1\nx\n" + extra

            def fake_git(
                *args, payload=payload, status=status, processes=processes, **kwargs
            ):
                process = native_popen(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys; sys.stdin.buffer.readline(); "
                            f"sys.stdout.buffer.write({payload!r}); sys.stdout.buffer.flush(); "
                            f"sys.exit({status})"
                        ),
                    ],
                    **kwargs,
                )
                processes.append(process)
                return process

            with (
                self.subTest(status=status, extra=extra),
                patch("scripts.check_public_safety.subprocess.Popen", fake_git),
                self.assertRaises(error),
            ):
                list(_git_object_bytes(Path.cwd(), [(object_id, "blob", 1)]))
            self.assertIsNotNone(processes[0].poll())
            self.assertTrue(processes[0].stdin.closed)
            self.assertTrue(processes[0].stdout.closed)

    def test_history_keeps_annotated_tags_and_refuses_oversized_object_reads(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init")
            (root / "large.txt").write_bytes(b"x" * (MAX_HISTORY_BLOB_BYTES + 1))
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Add oversized content")
            self._git(root, "tag", "-a", "release", "-m", "192" + ".168.1.2")
            admitted = []

            def read_objects(root, objects):
                admitted.extend(objects)
                return _git_object_bytes(root, objects)

            with patch("scripts.check_public_safety._git_object_bytes", read_objects):
                failures = _history_failures(root)
        self.assertIn("Git history blob: oversized unreviewed content", failures)
        self.assertIn("Git history tag: private IPv4 address", failures)
        self.assertTrue(admitted)
        self.assertTrue(all(size <= MAX_HISTORY_BLOB_BYTES for _, _, size in admitted))

    def test_working_tree_rejects_utf16_content_under_text_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._git(root, "init")
            (root / "notes.txt").write_bytes(
                ("private address " + "192" + ".168.1.2").encode("utf-16-le")
            )

            count, failures = run_guard(root)

        self.assertEqual(1, count)
        self.assertEqual(["notes.txt: unreviewed binary content"], failures)

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

    def test_tracked_archive_rejects_utf16_content_under_text_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._git(root, "init")
            notes = root / "notes.txt"
            notes.write_bytes(
                ("private address " + "192" + ".168.1.2").encode("utf-16-le")
            )
            self._git(root, "add", "notes.txt")
            notes.write_text("public-safe worktree", encoding="utf-8")

            count, failures = run_archive_guard(root)

        self.assertEqual(1, count)
        self.assertEqual(
            ["Source archive notes.txt: unreviewed binary content"], failures
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

    def test_history_guard_rejects_unavailable_and_shallow_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.assertEqual(
                ["Git history: requested repository is unavailable"],
                _history_failures(root / "missing"),
            )
            source = root / "source"
            source.mkdir()
            self._git(source, "init")
            (source / "README.md").write_text("safe", encoding="utf-8")
            self._git(source, "add", "README.md")
            self._git(source, "commit", "-m", "Initial content")
            self._git(root, "clone", "--depth", "1", source.as_uri(), "shallow")
            self.assertEqual(
                ["Git history: complete history is required; repository is shallow"],
                _history_failures(root / "shallow"),
            )

    @unittest.skipUnless(
        os.name == "posix" and shutil.which("bash"),
        "The product shell runner executes in the Linux validation lanes",
    )
    def test_local_unit_orchestration_checks_original_history_and_exact_payload(
        self,
    ) -> None:
        product_root = Path(__file__).resolve().parents[1]
        cases = {
            "clean": None,
            "removed_blob": "Git history blob: private IPv4 address",
            "replaced_blob": "Git history blob: private IPv4 address",
            "detached_metadata": "Git history metadata: non-example email address",
            "linked_metadata": "Git history metadata: non-example email address",
            "worktree": "README.md: private IPv4 address",
            "shallow": "Complete original Git history is required",
        }
        for case, expected in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source"
                scripts = source / "scripts"
                scripts.mkdir(parents=True)
                for filename in ("verify-release-local.sh", "check_public_safety.py"):
                    (scripts / filename).write_text(
                        (product_root / "scripts" / filename).read_text(
                            encoding="utf-8"
                        ),
                        encoding="utf-8",
                        newline="\n",
                    )
                readme = source / "README.md"
                private_address = "192" + ".168.1.2"
                readme.write_text(
                    private_address
                    if case in {"removed_blob", "replaced_blob"}
                    else "safe",
                    encoding="utf-8",
                )
                self._git(source, "init")
                self._git(source, "add", ".")
                self._git(source, "commit", "-m", "Initial candidate")
                if case in {"removed_blob", "replaced_blob"}:
                    readme.write_text("safe", encoding="utf-8")
                    self._git(source, "add", "README.md")
                    self._git(source, "commit", "-m", "Remove private content")
                    if case == "replaced_blob":
                        original = subprocess.check_output(
                            ["git", "rev-parse", "HEAD~:README.md"],
                            cwd=source,
                            text=True,
                        ).strip()
                        replacement = subprocess.check_output(
                            ["git", "rev-parse", "HEAD:README.md"],
                            cwd=source,
                            text=True,
                        ).strip()
                        self._git(source, "replace", original, replacement)
                elif case in {"detached_metadata", "linked_metadata"}:
                    if case == "linked_metadata":
                        self._git(
                            source, "worktree", "add", "--detach", str(root / "linked")
                        )
                        source = root / "linked"
                    else:
                        self._git(source, "checkout", "--detach")
                    self._git(
                        source,
                        "commit",
                        "--allow-empty",
                        "-m",
                        "Candidate for " + "person" + "@real-domain.dev",
                    )
                elif case == "worktree":
                    readme.write_text(private_address, encoding="utf-8")
                elif case == "shallow":
                    self._git(root, "clone", "--depth", "1", source.as_uri(), "shallow")
                    source = root / "shallow"

                binary_directory = root / "bin"
                binary_directory.mkdir()
                podman = binary_directory / "podman"
                podman.write_text(
                    f"#!{sys.executable}\n"
                    + textwrap.dedent(
                        """\
                        import os
                        import subprocess
                        import sys
                        from pathlib import Path

                        arguments = sys.argv[1:]
                        mounts = {}
                        environment = {}
                        for index, argument in enumerate(arguments[:-1]):
                            if argument == "-v":
                                source, target, *_ = arguments[index + 1].split(":")
                                mounts[target] = source
                            elif argument == "-e" and "=" in arguments[index + 1]:
                                key, value = arguments[index + 1].split("=", 1)
                                environment[key] = value
                        if "/workspace" not in mounts:
                            sys.exit(0)  # The unrelated Actionlint image.
                        assert arguments[:2] == ["run", "--rm"]
                        assert environment["PIP_CACHE_DIR"] == "/pip-cache"
                        assert environment["PIP_COMPILE"] == "0"
                        assert environment["MYPY_CACHE_DIR"] == "/dev/null"
                        cache = arguments[arguments.index("--mount") + 1]
                        assert cache == "type=volume,source=ecobee-unified-validation-pip,target=/pip-cache"
                        assert '--history-repository "$PUBLIC_SAFETY_HISTORY_REPOSITORY"' in arguments[-1]
                        history = mounts[environment["PUBLIC_SAFETY_HISTORY_REPOSITORY"]]
                        workspace = mounts["/workspace"]
                        result = subprocess.run(
                            [sys.executable, "-B", str(Path(workspace) / "scripts/check_public_safety.py"),
                             "--history-repository", history],
                            cwd=workspace,
                            # Containers receive only explicitly forwarded Git variables.
                             env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
                             | environment | {"PYTHONDONTWRITEBYTECODE": "1"},
                        )
                        sys.exit(result.returncode)
                        """
                    ),
                    encoding="utf-8",
                )
                podman.chmod(0o755)
                command = [
                    "bash",
                    "scripts/verify-release-local.sh",
                    "unit",
                    "container",
                ]
                if case == "linked_metadata":
                    git_directory = subprocess.run(
                        ["git", "rev-parse", "--path-format=absolute", "--git-dir"],
                        cwd=source,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    command.append(git_directory)
                result = subprocess.run(
                    command,
                    cwd=source,
                    env=os.environ
                    | {
                        "PATH": f"{binary_directory}:{os.environ['PATH']}",
                        "VALIDATION_PYTHON": sys.executable,
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                output = result.stdout + result.stderr
                if expected is None:
                    self.assertEqual(0, result.returncode, output)
                    self.assertIn("Public-safety guard passed", output)
                else:
                    self.assertNotEqual(0, result.returncode, output)
                    self.assertIn(expected, output)

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

    def test_declared_minimum_matches_distribution_requirement(self) -> None:
        root = Path(__file__).resolve().parents[1]
        minimum = _exact_core_pin(root / "requirements-ha-test.txt")
        hacs = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs["homeassistant"], minimum)

    def test_windows_wrapper_forwards_explicit_worktree_git_directory(self) -> None:
        root = Path(__file__).resolve().parents[1]
        wrapper = (root / "scripts/verify-release-local.ps1").read_text(
            encoding="utf-8"
        )
        # Unit tests cannot exercise the Windows-to-WSL handoff.
        self.assertIn("rev-parse --path-format=absolute --git-dir", wrapper)
        self.assertIn("$Mode container $linuxGitDir", wrapper)

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


if __name__ == "__main__":
    unittest.main()
