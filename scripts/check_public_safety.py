"""Reject local-only or unreviewed content from the public repository payload."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

TEXT_SUFFIXES = {
    "",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
PATTERNS = {
    "absolute Windows path": re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:\\"),
    "absolute Windows user path": re.compile(
        r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE
    ),
    "absolute Unix user path": re.compile(r"/(?:home|Users)/[^/\s]+", re.IGNORECASE),
    "private IPv4 address": re.compile(
        r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])"
    ),
    "local hostname": re.compile(r"\b[a-z0-9-]+\.local(?:\.|\b)", re.IGNORECASE),
    "credential-like token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})\b"
    ),
}
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE
)
PUBLIC_URL_PATTERN = re.compile(r"(?<![\w:/])https?://[^\s<>()\"'`]+")
REVIEWED_UNIX_PATH_URLS = frozenset(
    {"https://www.ecobee.com/home/developer/api/documentation/v1/objects/Runtime.shtml"}
)
MAX_HISTORY_BLOB_BYTES = 1_000_000
REVIEWED_BINARY_SHA256 = {
    "custom_components/ecobee_unified/brand/icon.png": (
        "46021e7b36e50c480c1e649057ccc726dd95ae4a72ce4447356bbfa1030737c7"
    )
}
REVIEWED_BINARY_HASHES = frozenset(REVIEWED_BINARY_SHA256.values())


LOCAL_GIT_OVERRIDE_NAMES = frozenset(
    {
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
    }
)


def _git_command(*arguments: str) -> list[str]:
    # GIT_CONFIG_KEY/VALUE entries are inert without GIT_CONFIG_COUNT; native
    # hook cleanup unsets the count and may leave those unused entries behind.
    inherited = sorted(name for name in os.environ if name in LOCAL_GIT_OVERRIDE_NAMES)
    if inherited:
        raise ValueError(
            "Inherited local Git overrides are not supported: " + ", ".join(inherited)
        )

    return ["git", "--no-replace-objects", "--no-optional-locks", *arguments]


def _text_failures(text: str) -> set[str]:
    # Only this path check may ignore an exact reviewed public documentation URL.
    unix_path_text = PUBLIC_URL_PATTERN.sub(
        lambda match: (
            "" if match.group(0) in REVIEWED_UNIX_PATH_URLS else match.group(0)
        ),
        text,
    )
    failures = {
        name
        for name, pattern in PATTERNS.items()
        if pattern.search(text if name != "absolute Unix user path" else unix_path_text)
    }
    for match in EMAIL_PATTERN.finditer(text):
        email = match.group(0).rstrip(".").lower()
        domain = match.group(1).rstrip(".").lower()
        if domain not in {
            "example.com",
            "example.test",
            "github.com",
        } and not email.endswith("@users.noreply.github.com"):
            failures.add("non-example email address")
    return failures


def _safe_filename(name: str) -> str:
    if _text_failures(name):
        identifier = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        return f"sensitive filename [{identifier}]"
    return name


def _is_reviewed_binary(path: Path, content: bytes) -> bool:
    expected = REVIEWED_BINARY_SHA256.get(path.as_posix())
    return expected is not None and hashlib.sha256(content).hexdigest() == expected


def _record_unreviewed_binary(failures: set[str], message: str, content: bytes) -> None:
    if hashlib.sha256(content).hexdigest() not in REVIEWED_BINARY_HASHES:
        failures.add(message)


def _content_failures(path: Path, content: bytes) -> tuple[bool, list[str]]:
    """Classify payload bytes and preserve the working-tree scan denominator."""

    if path.suffix.lower() not in TEXT_SUFFIXES:
        if _is_reviewed_binary(path, content):
            return True, []
        return False, ["unreviewed binary content"]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return True, ["non-UTF-8 content"]
    if "\0" in text:
        return True, ["unreviewed binary content"]
    return True, sorted(_text_failures(text))


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat()
    except FileNotFoundError:
        return False
    return bool(
        getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def is_linked_source(root: Path, path: Path) -> bool:
    """Apply the working-tree guard's leaf and ancestor admission policy."""
    return any(
        candidate.is_symlink()
        or candidate.is_junction()
        or _is_reparse_point(candidate)
        for candidate in (path, *path.parents)
        if candidate != root and root in candidate.parents
    )


def require_source_paths(root: Path, paths) -> None:
    """Refuse linked inputs before a consumer reads or copies their contents."""
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Source admission requires repository-relative paths")
        if is_linked_source(root, root / relative):
            raise ValueError("Source admission refused an unreviewed linked path")


def run_guard(root: Path) -> tuple[int, list[str]]:
    failures: list[str] = []
    count = 0
    result = subprocess.run(
        _git_command("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        cwd=root,
        check=True,
        capture_output=True,
    )
    for raw_relative in result.stdout.split(b"\0"):
        if not raw_relative:
            continue
        try:
            relative = Path(raw_relative.decode("utf-8"))
        except UnicodeDecodeError:
            failures.append("Working tree filename: non-UTF-8 content")
            continue
        path = root / relative
        if is_linked_source(root, path):
            failures.append(
                f"{_safe_filename(str(relative))}: unreviewed symbolic link or reparse point"
            )
            continue
        if not path.is_file():
            continue
        failures.extend(
            f"{_safe_filename(str(relative))}: filename {failure}"
            for failure in sorted(_text_failures(str(relative)))
        )
        counted, content_failures = _content_failures(relative, path.read_bytes())
        count += counted
        failures.extend(
            f"{_safe_filename(str(relative))}: {failure}"
            for failure in content_failures
        )
    return count, failures


def _read_batch_object(
    stream: BinaryIO, object_id: str, object_type: str, expected_size: int | None
) -> bytes:
    """Read one raw object without accepting missing or malformed batch output."""
    header = stream.readline(256)
    fields = header.removesuffix(b"\n").split(b" ")
    if (
        not header.endswith(b"\n")
        or len(fields) != 3
        or fields[0] != object_id.encode("ascii")
        or fields[1] != object_type.encode("ascii")
        or not re.fullmatch(rb"[0-9]+", fields[2])
    ):
        raise ValueError("Git object batch returned an invalid header")
    size = int(fields[2])
    if expected_size is not None and size != expected_size:
        raise ValueError("Git object batch returned an unexpected size")
    content = stream.read(size)
    if len(content) != size or stream.read(1) != b"\n":
        raise ValueError("Git object batch returned incomplete content")
    return content


def _git_object_bytes(
    root: Path, objects: list[tuple[str, str, int | None]]
) -> Iterator[bytes]:
    """Stream admitted objects through one Git process and settle it on every exit."""
    if not objects:
        return
    command = _git_command("cat-file", "--batch")
    process = subprocess.Popen(
        command,
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        for object_id, object_type, expected_size in objects:
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            yield _read_batch_object(
                process.stdout, object_id, object_type, expected_size
            )
        process.stdin.close()
        if process.stdout.read(1):
            raise ValueError("Git object batch returned unexpected trailing content")
        return_code = process.wait()
        if return_code:
            raise subprocess.CalledProcessError(return_code, command)
    finally:
        # Closing a pipe while Git is still writing can deadlock on its buffer.
        # Kill an unfinished reader before closing its pipes, then always reap it.
        if process.poll() is None:
            process.kill()
        try:
            process.stdin.close()
        finally:
            process.stdout.close()
            process.wait()


def run_archive_guard(root: Path) -> tuple[int, list[str]]:
    """Build and inspect the exact tracked source archive in temporary storage."""

    result = subprocess.run(
        _git_command("ls-files", "--stage", "-z"),
        cwd=root,
        check=True,
        capture_output=True,
    )
    failures: list[str] = []
    tracked: list[tuple[Path, str]] = []
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        metadata, raw_name = item.split(b"\t", 1)
        _mode, object_id, stage = metadata.decode("ascii").split()
        relative = Path(raw_name.decode("utf-8"))
        if stage != "0":
            failures.append(
                f"Source archive {_safe_filename(relative.as_posix())}: unresolved index stage"
            )
            continue
        tracked.append((relative, object_id))
    with tempfile.TemporaryDirectory() as temporary_directory:
        archive_path = Path(temporary_directory) / "ecobee_unified_source.zip"
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            objects = [(object_id, "blob", None) for _, object_id in tracked]
            with closing(_git_object_bytes(root, objects)) as contents:
                for (relative, _), content in zip(tracked, contents, strict=True):
                    archive.writestr(relative.as_posix(), content)
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                failures.extend(
                    f"Source archive {_safe_filename(name)}: filename {failure}"
                    for failure in sorted(_text_failures(name))
                )
                _, content_failures = _content_failures(Path(name), archive.read(name))
                failures.extend(
                    f"Source archive {_safe_filename(name)}: {failure}"
                    for failure in content_failures
                )
    return len(tracked), failures


def _history_source_failure(root: Path) -> str | None:
    try:
        shallow = subprocess.run(
            _git_command("rev-parse", "--is-shallow-repository"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            _git_command("rev-parse", "--verify", "HEAD"),
            cwd=root,
            check=True,
            capture_output=True,
        )
    except OSError, subprocess.CalledProcessError:
        return "Git history: requested repository is unavailable"
    if shallow.stdout.strip() != "false":
        return "Git history: complete history is required; repository is shallow"
    return None


def _history_failures(root: Path) -> list[str]:
    if source_failure := _history_source_failure(root):
        return [source_failure]
    return _scan_history(root)


def _scan_history(root: Path) -> list[str]:
    failures: set[str] = set()
    metadata = subprocess.run(
        _git_command("log", "--all", "--format=%an%n%ae%n%cn%n%ce%n%B%x00"),
        cwd=root,
        check=True,
        capture_output=True,
    )
    try:
        metadata_text = metadata.stdout.decode("utf-8")
    except UnicodeDecodeError:
        failures.add("Git history metadata: non-UTF-8 content")
    else:
        failures.update(
            f"Git history metadata: {item}" for item in _text_failures(metadata_text)
        )

    references = subprocess.run(
        _git_command("for-each-ref", "--format=%(refname)"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    failures.update(
        f"Git history reference: {item}" for item in _text_failures(references.stdout)
    )

    filenames = subprocess.run(
        _git_command("log", "--all", "--format=", "--name-only", "-z"),
        cwd=root,
        check=True,
        capture_output=True,
    )
    for raw_name in filenames.stdout.split(b"\0"):
        if not raw_name:
            continue
        try:
            name = raw_name.decode("utf-8").strip("\n")
        except UnicodeDecodeError:
            failures.add("Git history filename: non-UTF-8 content")
            continue
        failures.update(
            f"Git history filename: {item}" for item in _text_failures(name)
        )
        if (
            Path(name).suffix.lower() not in TEXT_SUFFIXES
            and Path(name).as_posix() not in REVIEWED_BINARY_SHA256
        ):
            failures.add("Git history filename: unreviewed binary content")

    objects = subprocess.run(
        _git_command("rev-list", "--objects", "--all"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    object_ids = sorted(
        {line.split(maxsplit=1)[0] for line in objects.stdout.splitlines()}
    )
    object_details = subprocess.run(
        _git_command(
            "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"
        ),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input="\n".join(object_ids),
    )
    failures.update(_history_object_failures(root, object_details.stdout))
    return sorted(failures)


def _history_object_failures(root: Path, details: str) -> set[str]:
    failures: set[str] = set()
    admitted: list[tuple[str, str, int | None]] = []
    for detail in details.splitlines():
        object_id, object_type, raw_size = detail.split()
        if object_type not in {"blob", "tag"}:
            continue
        if int(raw_size) > MAX_HISTORY_BLOB_BYTES:
            failures.add(f"Git history {object_type}: oversized unreviewed content")
            continue
        admitted.append((object_id, object_type, int(raw_size)))
    with closing(_git_object_bytes(root, admitted)) as contents:
        for (_, object_type, _), blob in zip(admitted, contents, strict=True):
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                _record_unreviewed_binary(
                    failures,
                    f"Git history {object_type}: non-UTF-8 content",
                    blob,
                )
                continue
            if "\0" in text:
                _record_unreviewed_binary(
                    failures,
                    f"Git history {object_type}: unreviewed binary content",
                    blob,
                )
                continue
            failures.update(
                f"Git history {object_type}: {item}" for item in _text_failures(text)
            )
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history-repository",
        type=Path,
        default=root,
        help="Complete original Git repository when payload validation uses a snapshot",
    )
    parser.add_argument("--check-source-paths", action="store_true")
    arguments = parser.parse_args()
    if arguments.check_source_paths:
        try:
            require_source_paths(
                root,
                (
                    os.fsdecode(path)
                    for path in sys.stdin.buffer.read().split(b"\0")
                    if path
                ),
            )
        except OSError, ValueError:
            print(
                "Source admission refused an input or could not inspect it.",
                file=sys.stderr,
            )
            return 1
        return 0
    count, failures = run_guard(root)
    failures.extend(_history_failures(arguments.history_repository))
    archive_count, archive_failures = run_archive_guard(root)
    failures.extend(archive_failures)
    if failures:
        print("Public-safety failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        "Public-safety guard passed for "
        f"{count} working-tree files, {archive_count} tracked archive files, "
        "and Git history."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
