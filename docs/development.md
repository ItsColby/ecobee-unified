# Development

Ecobee Unified is a custom Home Assistant integration with no additional package
requirements in its manifest. Contributors work against real Core APIs and the
published custom-component test harness. [Architecture](architecture.md) describes the
source boundaries; [validation](validation-plan.md) maps behavior to tests.

## Compatibility owners

| Lane | Home Assistant Core | Test harness | Requirement owner |
|---|---|---|---|
| Distribution minimum | 2026.8.0 | `pytest-homeassistant-custom-component==0.13.354` | [requirements-ha-test.txt](../requirements-ha-test.txt) |
| Maintained current | 2026.9.1 | `pytest-homeassistant-custom-component==0.13.364` | [requirements-ha-current.txt](../requirements-ha-current.txt) |

Both lanes use Python 3.14. [hacs.json](../hacs.json) declares the distribution
floor. "Maintained current" means the repository's selected test version; it
does not track upstream latest automatically or identify a user's installation.
The runner pins each harness, installs the exact Core requirement separately,
installs typing tools, and requires `python -m pip check` before typing/tests.
There is no accepted dependency-conflict exception in these lanes.

Change these versions as one supported-compatibility decision, with the runner,
workflow, metadata, tests, and documentation kept aligned. Weekly Dependabot
updates cover GitHub Actions with a seven-day cooldown; Core and harness pins
are maintained explicitly.

## Run validation in containers

Run from the repository root. The supported container route requires Bash,
Git, tar, and Podman on Linux, plus network access for the pinned images and
dependency installation. It uses the exact working tree, including nonignored
untracked files, and requires complete available Git history.

```sh
bash scripts/verify-release-local.sh all container
```

On Windows, the PowerShell wrapper maps both the checkout and its Git directory
into the WSL distribution named `Ubuntu-24.04`, then invokes the same Podman
route. Install and configure WSL/Podman separately before using it:

```powershell
.\scripts\verify-release-local.ps1 -Mode all
```

The default mode is `all`. The shell runner also defaults to the `container`
backend. Select one mode for focused work:

| Mode | Runs |
|---|---|
| `unit` | actionlint; Zizmor auditor checks; ShellCheck; Ruff formatting/lint; public-safety and runner-orchestration unittests; compilation; payload, history, and archive privacy checks. |
| `minimum` | Minimum-lane dependencies, dependency closure, strict mypy over the integration, and all tests. |
| `current` | Current-lane dependencies, dependency closure, strict mypy over the integration, and all tests. |
| `release` | Pinned Hassfest container. This does not publish a release. |
| `all` | `unit`, both isolated Home Assistant lanes concurrently, then Hassfest. A failed prerequisite blocks the later phase. |

For example:

```sh
bash scripts/verify-release-local.sh current container
```

```powershell
.\scripts\verify-release-local.ps1 -Mode current
```

The `unit` name refers to repository/static checks; it does not execute the
complete integration test suite. `all` includes both complete pytest lanes but
does not run the hosted HACS Action or reproduce the hosted aggregate gate.

### Isolation, resources, and cleanup

The container runner copies the candidate to temporary storage and constructs a
separate read-only mirror of the source repository's available refs and HEAD.
Containers read that payload and history without changing the checkout. This
also supports a linked worktree or detached HEAD. Ignored files are excluded;
required new source must not be hidden in ignored directories.

Python caches are redirected away from the payload, bytecode and pytest cache
output are controlled, and containers are removed on exit. Downloaded Python
packages remain in the named Podman volume `ecobee-unified-validation-pip` for
reuse. The shell trap removes its temporary payload/history after both support
lanes have exited. Images and the shared package cache are not automatically
deleted. There are no runner options for changing concurrency, cache location,
or retention; do not remove the shared cache while validation is running.

The two support lanes can consume substantial memory and download bandwidth.
For constrained hosts, invoke `minimum` and `current` separately. The script
does not impose a local wall-clock deadline; use a caller deadline appropriate
to image downloads and the complete selected mode. A timeout is an incomplete
result: inspect surviving processes and output before starting another run.

## Native Linux development

The `native` backend runs commands through Bash login shells on the host and may
write normal tool caches there. Ensure those shells resolve Python 3.14.
Hosted CI invokes one mode in each fresh job. Use a fresh disposable environment
for each Home Assistant lane;
`all native` does not isolate its sequential lane installations and should not
be used as independent-lane evidence.

```sh
bash scripts/verify-release-local.sh current native
```

The native `unit` route also requires Go for actionlint and an available
ShellCheck executable for actionlint's shell analysis. It installs the other
pinned Python tooling itself. The native `release` route requires Docker.
Home Assistant tests belong on Linux/WSL or hosted CI; successful native Windows
repository checks do not establish Core compatibility.

For repeated focused tests, create a development environment with the current
lane's dependencies:

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install pytest-homeassistant-custom-component==0.13.364
python -m pip install --upgrade -r requirements-ha-current.txt
python -m pip install mypy==2.3.0
python -m pip check
python -m mypy --strict custom_components/ecobee_unified
pytest tests/test_configuration_source_contracts.py -q
```

Use `pytest tests -q` for the complete suite. `tests/conftest.py` enables the
real custom-component harness; `pyproject.toml` enables async test collection.
Do not disable that fixture/plugin or replace Core imports to make an HA lane
appear to pass. The dependency-light repository checks can also run with Python
3.14 on Windows:

```powershell
python -m unittest tests.test_public_safety tests.test_parallel_validation
python scripts/check_public_safety.py
```

The native Windows unittest command skips the Linux shell-orchestration tests;
run the Linux/container `unit` mode when that coverage is needed.

## Hosted validation and public content

[Validate](../.github/workflows/validate.yaml) runs on pushes to `main`, pull
requests, and manual dispatch. Its six jobs are repository/static checks, two
separate Core lanes, Hassfest, HACS, and the aggregate **Release gate**. Every
required predecessor must succeed. Jobs use Ubuntu 24.04, bounded timeouts,
read-only repository permissions, immutable action pins, and checkout without
persisted credentials. Overlapping runs for the same workflow event/ref are
cancelled. Additional repository settings are not established by this YAML.

[check_public_safety.py](../scripts/check_public_safety.py) checks tracked and
nonignored untracked working files, staged source-archive bytes, and available
complete Git history. The historical scan includes refs, commit metadata,
filenames, reachable blobs and tag objects, rejects oversized unreviewed
content, and permits only the reviewed binary hashes. A direct invocation
checks the index archive separately from dirty working files; the container
runner stages its exact snapshot so the exported archive matches the candidate.

Use synthetic fixtures and examples. Review diagnostics, logs, issue text, and
release notes before publishing: the guard's pattern coverage cannot identify
every private value. Tests and local checks neither upload an installation nor
install the integration, restart Home Assistant, create a tag, or publish a
release. Keep execution results in the relevant run/release evidence rather
than inserting transient test counts or machine paths into maintained docs.
