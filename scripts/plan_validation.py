"""Select validation from changed files and their local Python consumers."""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

if __package__:
    from .check_public_safety import LOCAL_GIT_OVERRIDE_NAMES, require_source_paths
else:
    from check_public_safety import LOCAL_GIT_OVERRIDE_NAMES, require_source_paths

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = "custom_components/ecobee_unified"
PLANNER = "scripts/plan_validation.py"
TOOL_TESTS = {
    "tests/test_public_safety.py",
    "tests/test_parallel_validation.py",
    "tests/test_validation_selection.py",
}
METADATA_TEST = "tests/test_public_safety.py"
EXTRA_DEPENDENCIES: dict[str, set[str]] = {
    "tests/test_sensor.py": {f"{PRODUCT}/translations/en.json"},
    "tests/test_datapoint_recorder_ha.py": {f"{PRODUCT}/sensor.py"},
    "tests/test_history_service.py": {"examples/ecobee_daily_history_report.yaml"},
}
JOBS = ("unit", "minimum", "current", "release", "hacs")


def _reject_git_overrides() -> None:
    """Repository inputs must come from the caller's explicit target."""
    # GIT_CONFIG_KEY/VALUE entries are inert without GIT_CONFIG_COUNT; native
    # hook cleanup unsets the count and may leave those unused entries behind.
    inherited = sorted(name for name in os.environ if name in LOCAL_GIT_OVERRIDE_NAMES)
    if inherited:
        raise ValueError(
            "Inherited local Git overrides are not supported: " + ", ".join(inherited)
        )


def _git(*args: str, git_directory: str | None = None) -> str:
    _reject_git_overrides()
    command = ["git", "--no-replace-objects", "--no-optional-locks"]
    if git_directory:
        directory = Path(git_directory).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("--git-directory must identify a Git metadata directory")
        command.extend([f"--git-dir={directory}", f"--work-tree={ROOT}"])
    else:
        command.extend(["-C", str(ROOT)])
    actual_root = subprocess.check_output(
        [*command, "rev-parse", "--show-toplevel"], text=True
    ).strip()
    if Path(actual_root).resolve() != ROOT.resolve():
        raise ValueError("Git target root does not match the planner source root")
    return subprocess.check_output([*command, *args], text=True)


def changed_paths(
    base: str | None,
    head: str | None,
    paths: list[str] | None,
    git_directory: str | None = None,
) -> list[str]:
    """Require a caller-owned comparison or explicit selection, never infer all."""
    if paths is not None:
        if base or head:
            raise ValueError("Use either --path or --base/--head")
        selected = paths
    else:
        if not base or not head:
            raise ValueError(
                "Affected validation requires --base and --head, or --path"
            )
        base_oid = _git(
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{base}^{{commit}}",
            git_directory=git_directory,
        ).strip()
        head_oid = _git(
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{head}^{{commit}}",
            git_directory=git_directory,
        ).strip()
        if _git("rev-parse", "HEAD", git_directory=git_directory).strip() != head_oid:
            raise ValueError("--head must identify the checked-out candidate")
        if _git(
            "status",
            "--porcelain",
            "--untracked-files=normal",
            git_directory=git_directory,
        ).strip():
            raise ValueError(
                "Ref comparison requires a clean candidate; use explicit paths for working edits"
            )
        selected = _git(
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            base_oid,
            head_oid,
            "--",
            git_directory=git_directory,
        ).split("\0")[:-1]
    for path in selected:
        parsed = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or parsed.is_absolute()
            or ".." in parsed.parts
            or ":" in path
            or path.startswith("-")
        ):
            raise ValueError(f"Not a repository-relative path: {path!r}")
    return sorted(set(selected))


def _imports(path: str, files: set[str]) -> set[str]:
    """Read syntax only; do not import the product while planning validation."""
    require_source_paths(ROOT, [path])
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
    result: set[str] = set()
    package = path.removesuffix(".py").split("/")[:-1]
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*prefix, *([node.module] if node.module else [])])
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        for name in names:
            stem = name.replace(".", "/")
            if path.startswith("tests/") and (
                stem.startswith(PRODUCT + "/") or stem == PRODUCT
            ):
                initializer = PRODUCT + "/__init__.py"
                if initializer in files:
                    result.add(initializer)
            result.update(
                candidate
                for candidate in (f"{stem}.py", f"{stem}/__init__.py")
                if candidate in files
            )
        # Direct-file test loaders avoid importing the HA package initializer.
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.endswith(".py")
        ):
            candidates = {f"{PRODUCT}/{node.value}", f"scripts/{node.value}"}
            result.update(candidates & files)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_load_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            candidate = f"{PRODUCT}/{node.args[0].value}.py"
            if candidate in files:
                result.add(candidate)
    result.update(EXTRA_DEPENDENCIES.get(path, set()))
    return result


def _test_files(files: set[str]) -> tuple[set[str], set[str]]:
    tests = {
        path
        for path in files
        if path.startswith("tests/")
        and (Path(path).name.startswith("test_") or path.endswith("_test.py"))
    }
    return tests & TOOL_TESTS, tests - TOOL_TESTS


def _consumer_closure(changed: set[str], dependencies: dict[str, set[str]]) -> set[str]:
    impacted = set(changed)
    while True:
        consumers = {path for path, uses in dependencies.items() if uses & impacted}
        if consumers <= impacted:
            return impacted
        impacted.update(consumers)


def runner_dependencies(source: str) -> dict[str, str]:
    """Read exact dependency declarations from the existing Bash owner."""
    result = {}

    def one(pattern: str) -> str:
        values = set(re.findall(pattern, source, re.MULTILINE))
        if len(values) != 1:
            raise ValueError(f"Unresolved runner dependency declaration: {pattern}")
        return values.pop()

    for name in ("python_image", "actionlint_image", "hassfest_image"):
        result[name] = one(rf'^{name}="([^"$]+)"$')
    for package in ("ruff", "mypy", "zizmor", "shellcheck-py"):
        result[package] = one(rf'"({package}==[\w.+-]+)"')
    result["actionlint"] = one(
        r"go install (github.com/rhysd/actionlint/cmd/actionlint@\S+)"
    )
    for lane in ("minimum", "current"):
        body = one(rf"(?s)^run_{lane}\(\) \{{\n(.*?)^\}}")
        pins = re.findall(r'"(pytest-homeassistant-custom-component==[\w.+-]+)"', body)
        if len(pins) != 1:
            raise ValueError(f"Unresolved {lane} HA harness declaration")
        result[lane] = pins[0]
        requirements = re.findall(
            r"python -m pip install --upgrade -r ([\w./-]+)(?=\s|$)", body
        )
        if len(requirements) != 1:
            raise ValueError(f"Unresolved {lane} requirements declaration")
        result[lane + "_requirements"] = requirements[0]
    return result


def _workflow_content(source: str) -> tuple[str, ...]:
    """Compare maintained YAML bodies while preserving literal scalar payloads."""
    lines = []
    scalar_indent: int | None = None
    for raw in source.splitlines():
        indent = len(raw) - len(raw.lstrip())
        if scalar_indent is not None and (not raw.strip() or indent > scalar_indent):
            # A hash or blank line in shell/heredoc or env data is not a YAML comment.
            lines.append(raw)
            continue
        scalar_indent = None
        line = re.sub(
            r"""("(?:\\.|[^"\\])*"|'(?:''|[^'])*')|(?<!\S)#.*""",
            lambda match: match[1] or "",
            raw,
        ).rstrip()
        if not line.strip() or re.match(r"^(?: {4}| {6}- | {8})name:", line):
            continue
        if re.search(r":\s*[|>][+-]?\s*$", line):
            scalar_indent = indent
        lines.append(line)
    return tuple(lines)


def workflow_dependencies(source: str) -> dict[str, tuple[str, ...]]:
    """Read execution inputs in the maintained workflow's mapped job blocks."""
    shared = _workflow_content(
        "\n".join(
            re.findall(
                r"(?ms)^(?:env|defaults):.*?(?=^\S|\Z)",
                source.split("jobs:\n", 1)[0],
            )
        )
    )
    jobs = dict(
        re.findall(
            r"(?ms)^  ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(?:#[^\n]*)?\n(.*?)"
            r"(?=^  [A-Za-z_][A-Za-z0-9_-]*:[ \t]*(?:#[^\n]*)?(?:\n|\Z)|\Z)",
            source.split("jobs:\n", 1)[-1],
        )
    )
    names = {
        "unit": "unit",
        "home_assistant_minimum": "minimum",
        "home_assistant_current": "current",
        "hassfest": "release",
        "hacs": "hacs",
    }
    if set(jobs) - set(names) - {"plan", "release_gate"} or set(names) - set(jobs):
        raise ValueError("Unresolved workflow job dependency mapping")
    result = {}
    for name, lane in names.items():
        values = re.findall(
            r"^\s+(?:- )?(uses|python-version|runs-on|category):\s*([^#\n]+)",
            jobs[name],
            re.MULTILINE,
        )
        if not values or any("${{" in value for _, value in values):
            raise ValueError(f"Unresolved workflow dependencies: {name}")
        result[lane] = (*shared, *_workflow_content(jobs[name]))
    return result


def _select_environment(
    plan: dict, lane: str, ha_files: set[str], files: set[str]
) -> None:
    plan[lane] = True
    plan["lane_tests"][lane] = sorted(ha_files)
    plan["lane_typing"][lane] = sorted(
        path for path in files if path.startswith(PRODUCT + "/")
    )


def _select_unit_environment(plan: dict, unit_files: set[str], files: set[str]) -> None:
    plan["unit_tests"] = sorted(unit_files)
    plan["python"] = sorted(files)
    plan["workflow"] = True
    plan["shell"] = True


def _route_runner_dependencies(
    before: str,
    after: str,
    files: set[str],
    unit_files: set[str],
    ha_files: set[str],
    plan: dict,
) -> None:
    old, new = runner_dependencies(before), runner_dependencies(after)
    changed = {key for key in new if new[key] != old[key]}
    for lane in ("minimum", "current"):
        if changed & {lane, lane + "_requirements", "python_image"}:
            _select_environment(plan, lane, ha_files, files)
    if "python_image" in changed:
        _select_unit_environment(plan, unit_files, files)
    if "ruff" in changed:
        plan["python"] = sorted(files)
    if "mypy" in changed:
        for lane in ("minimum", "current"):
            plan[lane] = True
            plan["lane_typing"][lane] = sorted(
                path for path in files if path.startswith(PRODUCT + "/")
            )
    plan["workflow"] |= bool(
        changed & {"zizmor", "actionlint", "actionlint_image", "shellcheck-py"}
    )
    plan["shell"] |= "shellcheck-py" in changed
    plan["release"] |= "hassfest_image" in changed


def _configuration_equal(before: object, after: object) -> bool:
    """Compare TOML values without conflating booleans, integers, and floats."""
    if type(before) is not type(after):
        return False
    if isinstance(before, dict) and isinstance(after, dict):
        return before.keys() == after.keys() and all(
            _configuration_equal(value, after[key]) for key, value in before.items()
        )
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(
            _configuration_equal(left, right)
            for left, right in zip(before, after, strict=True)
        )
    if isinstance(before, float) and isinstance(after, float) and math.isnan(before):
        return math.isnan(after)
    return before == after


def _pyproject_configuration(source: str) -> dict:
    """Separate configured native tools from configuration without a mapped owner."""
    configuration = tomllib.loads(source)
    tools = configuration.pop("tool", {})
    if not isinstance(tools, dict):
        raise TypeError("tool must be a TOML table")
    sections = {name: tools.pop(name, {}) for name in ("ruff", "mypy")}
    pytest = tools.pop("pytest", {})
    if not isinstance(pytest, dict):
        raise TypeError("tool.pytest must be a TOML table")
    sections["pytest"] = pytest.pop("ini_options", {})
    if any(not isinstance(section, dict) for section in sections.values()):
        raise TypeError("Mapped tool configuration must be a TOML table")
    if pytest:
        tools["pytest"] = pytest
    configuration["tool"] = tools
    sections["unmapped"] = configuration
    return sections


def _route_pyproject_changes(
    base: str,
    git_directory: str | None,
    files: set[str],
    ha_files: set[str],
    plan: dict,
) -> None:
    """Select only tools whose parsed configuration differs from the exact base."""
    path = "pyproject.toml"
    try:
        before = _pyproject_configuration(
            _git("show", f"{base}:{path}", git_directory=git_directory)
        )
        require_source_paths(ROOT, [path])
        after = _pyproject_configuration((ROOT / path).read_text(encoding="utf-8"))
        changed = {
            name
            for name in before
            if not _configuration_equal(before[name], after[name])
        }
        if "unmapped" in changed:
            raise ValueError("changed configuration has no mapped tool consumer")
        if "ruff" in changed:
            plan["python"] = sorted(files)
        if "mypy" in changed:
            for lane in ("minimum", "current"):
                plan[lane] = True
                plan["lane_typing"][lane] = sorted(
                    path for path in files if path.startswith(PRODUCT + "/")
                )
        if "pytest" in changed:
            for lane in ("minimum", "current"):
                plan[lane] = True
                plan["lane_tests"][lane] = sorted(ha_files)
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError) as err:
        plan["unresolved"].append(
            f"{path}: configuration comparison unavailable: {err}"
        )


def _route_dependency_changes(
    paths: list[str],
    base: str,
    git_directory: str | None,
    files: set[str],
    unit_files: set[str],
    ha_files: set[str],
    plan: dict,
) -> None:
    """Compare dependency inputs, rather than treating every runner edit as HA work."""
    for path in paths:
        if path != "scripts/verify-release-local.sh" and not path.startswith(
            ".github/workflows/"
        ):
            continue
        try:
            if (
                path.startswith(".github/")
                and path != ".github/workflows/validate.yaml"
            ):
                raise ValueError(f"Unresolved workflow dependency owner: {path}")
            before = _git("show", f"{base}:{path}", git_directory=git_directory)
            require_source_paths(ROOT, [path])
            after = (ROOT / path).read_text(encoding="utf-8")
            if path.endswith(".sh"):
                _route_runner_dependencies(
                    before, after, files, unit_files, ha_files, plan
                )
            else:
                old, new = workflow_dependencies(before), workflow_dependencies(after)
                for lane in new:
                    if old[lane] == new[lane]:
                        continue
                    if lane in {"minimum", "current"}:
                        _select_environment(plan, lane, ha_files, files)
                    elif lane == "unit":
                        _select_unit_environment(plan, unit_files, files)
                    else:
                        plan[lane] = True
        except (OSError, ValueError, subprocess.CalledProcessError) as err:
            plan["unresolved"].append(
                f"{path}: dependency comparison unavailable: {err}"
            )


def build_plan(
    paths: list[str], base: str = "HEAD", git_directory: str | None = None
) -> dict:
    files: set[str] = set()
    for owner in ("custom_components", "tests", "scripts"):
        require_source_paths(ROOT, [owner])
        for directory, children, names in (ROOT / owner).walk():
            children[:] = [name for name in children if name != "__pycache__"]
            require_source_paths(
                ROOT,
                [(directory / name).relative_to(ROOT) for name in (*children, *names)],
            )
            files.update(
                (directory / name).relative_to(ROOT).as_posix()
                for name in names
                if name.endswith(".py")
            )
    require_source_paths(ROOT, files)
    dependencies = {path: _imports(path, files) for path in files}
    unit_files, ha_files = _test_files(files)
    for test in ha_files:
        dependencies[test].update(path for path in files if path == "tests/conftest.py")
    impacted = _consumer_closure(set(paths), dependencies)
    selected = (unit_files | ha_files) & impacted
    plan = {
        "paths": paths,
        "unit_tests": sorted(selected & unit_files),
        "ha_tests": sorted(selected & ha_files),
        "python": sorted(path for path in paths if path in files),
        "typing": sorted(
            path
            for path in impacted
            if path in files and path.startswith(PRODUCT + "/")
        ),
        "lane_tests": {"minimum": [], "current": []},
        "lane_typing": {"minimum": [], "current": []},
        "workflow": False,
        "shell": False,
        "safety": bool(paths),
        "minimum": False,
        "current": False,
        "release": False,
        "hacs": False,
        "unresolved": [],
    }
    for path in paths:
        consumers = _consumer_closure({path}, dependencies) & (unit_files | ha_files)
        _route_path(path, files, consumers, ha_files, plan)
    _route_dependency_changes(
        paths, base, git_directory, files, unit_files, ha_files, plan
    )
    if "pyproject.toml" in paths:
        _route_pyproject_changes(base, git_directory, files, ha_files, plan)
    if selected & ha_files:
        plan["current"] = True
        if any(path.startswith(PRODUCT + "/") for path in paths):
            plan["minimum"] = True
    for lane in ("minimum", "current"):
        if plan[lane]:
            plan["lane_tests"][lane] = sorted(
                set(plan["lane_tests"][lane])
                | (
                    (selected & ha_files)
                    if lane == "current"
                    or any(path.startswith(PRODUCT + "/") for path in paths)
                    else set()
                )
            )
            plan["lane_typing"][lane] = sorted(
                set(plan["lane_typing"][lane]) | set(plan["typing"])
            )
    plan["ha_tests"] = sorted(set().union(*map(set, plan["lane_tests"].values())))
    plan["jobs"] = {
        job: bool(plan[job])
        if job != "unit"
        else bool(
            plan["safety"]
            or plan["unit_tests"]
            or plan["python"]
            or plan["workflow"]
            or plan["shell"]
        )
        for job in JOBS
    }
    return plan


def _route_path(
    path: str, files: set[str], selected: set[str], ha_files: set[str], plan: dict
) -> None:
    if path in files:
        if path.startswith(PRODUCT + "/"):
            # Hassfest consumes integration imports and config-flow/schema declarations.
            plan["release"] = True
        if path.startswith(PRODUCT + "/") and not selected & ha_files:
            plan["unresolved"].append(f"No HA consumer found for {path}")
        if path.startswith("scripts/") and not selected:
            plan["unresolved"].append(f"No test consumer found for {path}")
        return
    if path in {"requirements-ha-test.txt", "requirements-ha-current.txt"}:
        lane = "minimum" if path.endswith("-test.txt") else "current"
        _select_environment(plan, lane, ha_files, files)
        plan["unit_tests"] = sorted(set(plan["unit_tests"]) | {METADATA_TEST})
    elif path in {
        "scripts/verify-release-local.sh",
        "scripts/verify-release-local.ps1",
    } or path.startswith(".github/"):
        plan["unit_tests"] = sorted(set(plan["unit_tests"]) | TOOL_TESTS)
        plan["workflow"] |= path.startswith(".github/")
        plan["shell"] |= path.endswith(".sh")
    elif (
        path.startswith(PRODUCT + "/")
        and Path(path).suffix in {".json", ".yaml", ".png"}
    ) or path == "hacs.json":
        plan["unit_tests"] = sorted(set(plan["unit_tests"]) | {METADATA_TEST})
        plan["release"] = True
        plan["hacs"] |= path == "hacs.json" or path.endswith("manifest.json")
    elif path == "pyproject.toml":
        # Parsed content is compared with the pinned base after path routing.
        pass
    elif selected and any(path in uses for uses in EXTRA_DEPENDENCIES.values()):
        # Explicit non-Python runtime inputs use their real native consumer.
        pass
    elif path.endswith(".md") or path in {
        "LICENSE",
        ".gitignore",
        ".gitattributes",
    }:
        pass
    else:
        plan["unresolved"].append(path)


def lane_command(plan: dict, lane: str) -> str:
    """Return quoted commands for the existing isolated environment runner."""
    require_source_paths(ROOT, ["scripts/verify-release-local.sh"])
    pins = runner_dependencies(
        (ROOT / "scripts/verify-release-local.sh").read_text(encoding="utf-8")
    )
    commands: list[str] = []
    if lane in {"minimum", "current"}:
        if plan["lane_typing"][lane]:
            commands.append(
                shlex.join(["python", "-m", "pip", "install", pins["mypy"]])
            )
        # The runner installs the lane's exact requirements before this command.
        commands.append("python -m pip check")
        if plan["lane_typing"][lane]:
            commands.append(
                shlex.join(["python", "-m", "mypy", *plan["lane_typing"][lane]])
            )
        if plan["lane_tests"][lane]:
            commands.append(
                shlex.join(["python", "-m", "pytest", "-q", *plan["lane_tests"][lane]])
            )
    else:
        if plan["python"]:
            commands.extend(
                [
                    shlex.join(["python", "-m", "pip", "install", pins["ruff"]]),
                    shlex.join(
                        ["python", "-m", "ruff", "format", "--check", *plan["python"]]
                    ),
                    shlex.join(["python", "-m", "ruff", "check", *plan["python"]]),
                    shlex.join(["python", "-m", "compileall", "-q", *plan["python"]]),
                ]
            )
        if plan["workflow"]:
            commands.extend(
                [
                    shlex.join(["python", "-m", "pip", "install", pins["zizmor"]]),
                    "zizmor --strict-collection --persona auditor .",
                ]
            )
        if plan["shell"]:
            commands.extend(
                [
                    shlex.join(
                        ["python", "-m", "pip", "install", pins["shellcheck-py"]]
                    ),
                    "shellcheck scripts/verify-release-local.sh",
                ]
            )
        commands.extend(
            shlex.join(
                [
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    Path(path).name,
                ]
            )
            for path in plan["unit_tests"]
        )
        if plan["safety"]:
            commands.append(
                'python scripts/check_public_safety.py --history-repository "$PUBLIC_SAFETY_HISTORY_REPOSITORY"'
            )
    return " &&\n".join(commands) or ":"


def _snapshot_selection(
    args: argparse.Namespace,
) -> tuple[str, str | None, list[str]]:
    """Bind copied source to the preview's exact selection and Git baseline."""
    if (
        not args.git_directory
        or args.full
        or args.base
        or args.head
        or args.path is not None
        or args.command
        or args.github_output
    ):
        raise ValueError("Snapshot planning requires only original Git metadata")
    selection = json.load(sys.stdin)
    if (
        not isinstance(selection, dict)
        or not isinstance(selection.get("base"), str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", selection["base"])
        or not isinstance(selection.get("paths"), list)
        or not all(isinstance(path, str) for path in selection["paths"])
    ):
        raise ValueError("Snapshot planning requires a pinned base and path list")
    base_oid = _git(
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{selection['base']}^{{commit}}",
        git_directory=args.git_directory,
    ).strip()
    paths = changed_paths(None, None, selection["paths"], args.git_directory)
    head_oid = selection.get("head")
    if head_oid is not None:
        if not isinstance(head_oid, str) or not re.fullmatch(
            r"[0-9a-f]{40}|[0-9a-f]{64}", head_oid
        ):
            raise ValueError("Snapshot comparison head must be pinned")
        if paths != changed_paths(base_oid, head_oid, None, args.git_directory):
            raise ValueError("Snapshot paths do not match the pinned comparison")
    return base_oid, head_oid, paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-directory")
    parser.add_argument(
        "--snapshot-plan",
        action="store_true",
        help="Replan the stdin selection against copied source and original Git metadata",
    )
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--path", action="append")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--command", choices=JOBS)
    parser.add_argument("--github-output", action="store_true")
    parser.add_argument(
        "--full", action="store_true", help="Explicit complete workflow dispatch"
    )
    args = parser.parse_args(argv)
    try:
        _reject_git_overrides()
        # Snapshot planning retains the already-resolved comparison, but acquires
        # source and execution commands only from the copied payload.
        if args.snapshot_plan:
            base_oid, head_oid, paths = _snapshot_selection(args)
        else:
            # Keep dependency-content reads on the same immutable base as the diff.
            base_oid = (
                None
                if args.full
                else _git(
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{args.base or 'HEAD'}^{{commit}}",
                    git_directory=args.git_directory,
                ).strip()
            )
            head_oid = (
                _git(
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{args.head}^{{commit}}",
                    git_directory=args.git_directory,
                ).strip()
                if args.head
                else None
            )
            paths = (
                []
                if args.full
                else changed_paths(
                    base_oid if args.base else None,
                    head_oid,
                    args.path,
                    args.git_directory,
                )
            )
        plan = build_plan(
            paths,
            base=base_oid or "HEAD",
            git_directory=args.git_directory,
        )
        plan["base"] = base_oid
        plan["head"] = head_oid
        if args.full:
            if args.command or args.path is not None or args.base or args.head:
                raise ValueError(
                    "--full is an explicit workflow plan, not an affected command"
                )
            plan["jobs"] = dict.fromkeys(JOBS, True)
        plan["mode"] = "full" if args.full else "affected"
        if plan["unresolved"]:
            raise ValueError(
                "Unresolved applicability: " + "; ".join(plan["unresolved"])
            )
        if not args.full:
            plan["commands"] = {
                lane: lane_command(plan, lane)
                for lane in ("unit", "minimum", "current")
                if plan["jobs"][lane]
            }
        if args.command:
            if not plan["jobs"][args.command]:
                raise ValueError(f"The plan did not select {args.command}")
            print(lane_command(plan, args.command))
        elif args.github_output:
            with Path(os.environ["GITHUB_OUTPUT"]).open(
                "a", encoding="utf-8"
            ) as output:
                output.writelines(
                    f"{job}={str(selected).lower()}\n"
                    for job, selected in plan["jobs"].items()
                )
                output.write(f"plan={json.dumps(plan, separators=(',', ':'))}\n")
        else:
            print(json.dumps(plan, indent=2))
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as err:
        print(f"Validation plan failed: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
