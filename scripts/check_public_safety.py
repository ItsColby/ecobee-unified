"""Reject private or unreviewed content from the public repository payload."""

from __future__ import annotations

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
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    tracked = [
        Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item
    ]
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        archive_path = Path(temporary_directory) / "ecobee_unified_source.zip"
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for relative in tracked:
                path = root / relative
                if path.is_file():
                    archive.write(path, relative.as_posix())
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                failures.extend(
                    f"Source archive {name}: filename {failure}"
                    for failure in sorted(_text_failures(name))
                )
                suffix = Path(name).suffix.lower()
                if suffix not in TEXT_SUFFIXES:
                    failures.append(f"Source archive {name}: unreviewed binary content")
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
    result = subprocess.run(
        ["git", "log", "--format=", "-p", "--all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [f"Git history: {item}" for item in sorted(_text_failures(result.stdout))]


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
