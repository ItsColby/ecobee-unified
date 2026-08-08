"""Reject private or unreviewed content from the public repository payload."""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}
TEXT_SUFFIXES = {
    "",
    ".json",
    ".md",
    ".py",
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
MAX_HISTORY_BLOB_BYTES = 1_000_000
MAINTAINER_AGENT_PARTS = frozenset({".agents", ".codex"})
REVIEWED_BINARY_SHA256 = {
    "custom_components/ecobee_unified/brand/icon.png": (
        "46021e7b36e50c480c1e649057ccc726dd95ae4a72ce4447356bbfa1030737c7"
    )
}
REVIEWED_BINARY_HASHES = frozenset(REVIEWED_BINARY_SHA256.values())


def _text_failures(text: str) -> set[str]:
    failures = {name for name, pattern in PATTERNS.items() if pattern.search(text)}
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


def _is_maintainer_agent_artifact(path: Path) -> bool:
    return path.name.casefold() == "agents.md" or any(
        part.casefold() in MAINTAINER_AGENT_PARTS for part in path.parts
    )


def _is_reviewed_binary(path: Path, content: bytes) -> bool:
    expected = REVIEWED_BINARY_SHA256.get(path.as_posix())
    return expected is not None and hashlib.sha256(content).hexdigest() == expected


def _record_unreviewed_binary(failures: set[str], message: str, content: bytes) -> None:
    if hashlib.sha256(content).hexdigest() not in REVIEWED_BINARY_HASHES:
        failures.add(message)


def run_guard(root: Path) -> tuple[int, list[str]]:
    failures: list[str] = []
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        relative = path.relative_to(root)
        failures.extend(
            f"{relative}: filename {failure}"
            for failure in sorted(_text_failures(str(relative)))
        )
        if path.suffix.lower() not in TEXT_SUFFIXES:
            if _is_reviewed_binary(relative, path.read_bytes()):
                count += 1
            else:
                failures.append(f"{relative}: unreviewed binary content")
            continue
        count += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            failures.append(f"{relative}: non-UTF-8 content")
            continue
        failures.extend(
            f"{relative}: {failure}" for failure in sorted(_text_failures(text))
        )
    return count, failures


def run_archive_guard(root: Path) -> tuple[int, list[str]]:
    """Build and inspect the exact tracked source archive in temporary storage."""

    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
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
                f"Source archive {relative.as_posix()}: unresolved index stage"
            )
            continue
        tracked.append((relative, object_id))
    with tempfile.TemporaryDirectory() as temporary_directory:
        archive_path = Path(temporary_directory) / "ecobee_unified_source.zip"
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for relative, object_id in tracked:
                if _is_maintainer_agent_artifact(relative):
                    failures.append(
                        f"Source archive {relative.as_posix()}: maintainer agent artifact"
                    )
                blob = subprocess.run(
                    ["git", "cat-file", "blob", object_id],
                    cwd=root,
                    check=True,
                    capture_output=True,
                )
                archive.writestr(relative.as_posix(), blob.stdout)
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                failures.extend(
                    f"Source archive {name}: filename {failure}"
                    for failure in sorted(_text_failures(name))
                )
                suffix = Path(name).suffix.lower()
                if suffix not in TEXT_SUFFIXES:
                    if not _is_reviewed_binary(Path(name), archive.read(name)):
                        failures.append(
                            f"Source archive {name}: unreviewed binary content"
                        )
                    continue
                try:
                    text = archive.read(name).decode("utf-8")
                except UnicodeDecodeError:
                    failures.append(f"Source archive {name}: non-UTF-8 content")
                    continue
                failures.extend(
                    f"Source archive {name}: {failure}"
                    for failure in sorted(_text_failures(text))
                )
    return len(tracked), failures


def _history_failures(root: Path) -> list[str]:
    failures: set[str] = set()
    metadata = subprocess.run(
        ["git", "log", "--all", "--format=%an%n%ae%n%cn%n%ce%n%B%x00"],
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
        ["git", "for-each-ref", "--format=%(refname)"],
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
        ["git", "log", "--all", "--format=", "--name-only", "-z"],
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
        ["git", "rev-list", "--objects", "--all"],
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
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input="\n".join(object_ids),
    )
    for detail in object_details.stdout.splitlines():
        object_id, object_type, raw_size = detail.split()
        if object_type not in {"blob", "tag"}:
            continue
        if int(raw_size) > MAX_HISTORY_BLOB_BYTES:
            failures.add(f"Git history {object_type}: oversized unreviewed content")
            continue
        blob = subprocess.run(
            ["git", "cat-file", object_type, object_id],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
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
    return sorted(failures)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    count, failures = run_guard(root)
    failures.extend(_history_failures(root))
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
