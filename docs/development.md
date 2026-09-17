# Development

Use the checked-in validation runner to check a candidate, and a separate Python
environment for focused development. Run commands below from the repository root.

## Select validation for the change

The default runner mode is `affected`. Preview an exact candidate comparison
before running it:

```powershell
.\scripts\verify-release-local.ps1 -Base <base-commit> -Head HEAD -PlanOnly
.\scripts\verify-release-local.ps1 -Base <base-commit> -Head HEAD
```

For a working edit, use `-ChangedPath scripts/verify-release-local.sh` instead of
refs. On Linux, use `bash scripts/verify-release-local.sh affected container ""`
with `--base <base-commit> --head HEAD`, or repeated `--path <relative-path>`;
add `--plan-only` to inspect the JSON plan without snapshots or installations.
Planning uses an existing host Python 3.14 (`python3.14`, an installed uv runtime,
or `VALIDATION_PYTHON`) to parse source without importing the integration. It
does not download a runtime; HA execution keeps its isolated Python 3.14 lane.
Explicit paths describe the complete change being accepted. The refs mode
requires the checked-out candidate as its head; it does not include uncommitted
edits. An empty verified comparison selects no jobs. Missing comparison input
and unmapped changes fail with an unresolved applicability message.

The product-owned planner traces local Python imports and reviewed direct-file
consumers. Changed tests run in their native collector; runtime changes include
the affected success, failure, and recovery consumers in both maintained HA
environments. A support requirements change selects that environment, without
invalidating the unchanged sibling lane. Runner and workflow dependency declarations
are compared against the supplied base, or HEAD for working-path selections;
changed harness, Python image, action, and tool pins select their actual consumers.
An unavailable dependency comparison remains unresolved. The Bash runner remains
the owner of exact local tool versions. Tooling, workflow, public-content and
metadata checks are selected independently of product tests. Configuration
changes without a reviewed tool-specific mapping need explicit review, rather
than an automatic complete run.

Pull requests and main pushes use this same selection. The stable Release gate
requires the planning job and every selected job to succeed, and accepts skipped
jobs only when the plan excludes them. Manual workflow dispatch explicitly runs
the complete lanes. `all`, `unit`, `minimum`, `current`, and `release` remain
explicit complete-lane requests. Reuse evidence whose source and environment
have not changed; a merge alone does not invalidate it. Local checks do not
replace HACS, authorize publication, or establish live behavior.


## Check the complete candidate

On Linux, install Git, Bash, tar, and Podman, then run:

```bash
bash scripts/verify-release-local.sh all container
```

On Windows, the wrapper requires Git for Windows, WSL with a distribution named
`Ubuntu-24.04`, and Podman available inside that distribution:

```powershell
.\scripts\verify-release-local.ps1 -Mode all
```

Both routes need complete, non-shallow Git history and network access to fetch
the pinned tools and dependencies. The Windows wrapper resolves the checkout's
actual Git directory before entering WSL, including for linked worktrees.

The container runner captures the tracked and nonignored untracked working-tree
files, including uncommitted edits, into one read-only validation payload. It
preserves the original Git history separately for the public-safety scan. The
minimum and current Home Assistant lanes run concurrently in separate containers
after the static checks pass; both finish before Hassfest or temporary-file
cleanup. On interruption, the runner waits for active lanes before removing
that payload and returns the interrupt status; this wait has no shutdown deadline.
The named pip cache volume is retained for later runs. Only the unit container
provisions Git; the Home Assistant lanes do not run the Git-dependent checks.

Use a mode for a narrower check:

| Mode | What it runs |
| --- | --- |
| `unit` | Actionlint, Zizmor, ShellCheck, Ruff formatting and lint, public-safety and runner-orchestration unit tests, Python compilation, and the public-safety guard. |
| `minimum` | The minimum Core/harness pair, dependency consistency, strict mypy, and all product tests through pytest. |
| `current` | The current Core/harness pair, dependency consistency, strict mypy, and all product tests through pytest. |
| `release` | Hassfest only. |
| `all` | `unit`, both Home Assistant lanes, and `release`. |

Public-safety and runner-orchestration tests execute once in `unit`; they do not
depend on Core and are excluded from the two Home Assistant lanes.

For example, replace `all` with `current` in the Bash command or use
`-Mode current` in PowerShell. The `native` Bash backend is used by CI. It
requires Python 3.14 with pip and venv, Git, Go for Actionlint, and Docker for
the `release` mode. Each Python lane creates and removes its own temporary
environment, including the sequential minimum and current lanes in `all native`.
Actionlint uses a separate temporary environment with the runner's pinned
ShellCheck version, so workflow-shell analysis does not depend on a caller's
ShellCheck installation. The selected environments run without a login shell
overriding their executable paths.

## Work on one change

The supported test environments are deliberately paired:

| Lane | Home Assistant Core | `pytest-homeassistant-custom-component` | Core requirements |
| --- | --- | --- | --- |
| Minimum | `2026.8.0` | `0.13.354` | [`requirements-ha-test.txt`](../requirements-ha-test.txt) |
| Current | `2026.9.2` | `0.13.365` | [`requirements-ha-current.txt`](../requirements-ha-current.txt) |

For a focused edit, create a Python 3.14 environment on Linux or inside WSL. This
example installs the current pair in the same order as the runner:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install "pytest-homeassistant-custom-component==0.13.365"
python -m pip install --upgrade -r requirements-ha-current.txt
python -m pip install "ruff==0.16.1" "mypy==2.3.0"
python -m pip check
python -m pytest tests/test_commands.py tests/test_command_lifecycle.py -q
```

Use the minimum harness and requirements file from the table to investigate a
minimum-version issue. Keep the two environments separate, or use the container
runner to check both. Installing only the harness does not establish the intended
Core version; the requirements installation and `pip check` are part of the lane.

Choose regression coverage by the contract being changed:

| Change | Start with | Check the failure boundary |
| --- | --- | --- |
| Values, units, fallback, or precision | [`test_models.py`](../tests/test_models.py), [`test_numeric_validity.py`](../tests/test_numeric_validity.py), [`test_temperature_quality.py`](../tests/test_temperature_quality.py) | Malformed and impossible values, source ownership, serialization tolerance, and recovery from rejected temperature evidence. |
| Equipment-stage projection | [`test_sensor.py`](../tests/test_sensor.py) | Bounded native enum values, mixed or unknown equipment signals, subordinate fan activity, and complete translations. |
| Read policies and action/stage coherence | [`test_operating_state.py`](../tests/test_operating_state.py) | Paired mappings, source disagreement, unavailable action, fallback, and unchanged command authority. |
| Datapoint composition and configuration | [`test_datapoints.py`](../tests/test_datapoints.py), [`test_datapoint_config.py`](../tests/test_datapoint_config.py) | Physical identity, semantic distinctions, metadata timing, source recovery, stable IDs, and concurrent configuration changes. |
| Native weather aliases and daily forecasts | [`test_weather_source.py`](../tests/test_weather_source.py), [`test_weather.py`](../tests/test_weather.py) | Station drift, provider age, unit conversion, changes during awaited reads, source subscriptions, and unload. |
| Mapping validation or source identity | [`test_configuration_source_contracts.py`](../tests/test_configuration_source_contracts.py), [`test_homekit_action_roles.py`](../tests/test_homekit_action_roles.py), [`test_source_device_identity.py`](../tests/test_source_device_identity.py) | Live metadata, missing saved references, device association, role drift, and recovery. |
| Writer routing or command state | [`test_commands.py`](../tests/test_commands.py), [`test_command_lifecycle.py`](../tests/test_command_lifecycle.py) | Exact target and call count, observations before acceptance, superseded revisions, cancellation, and unload. |
| Core APIs, configuration flows, or entity behavior | [`test_runtime_core_api.py`](../tests/test_runtime_core_api.py), [`test_integration_ha.py`](../tests/test_integration_ha.py), [`test_setup_lifecycle.py`](../tests/test_setup_lifecycle.py) | Native registry, state, service, setup, reload, repair, and serialized entity behavior. |
| Validation tooling or distribution content | [`test_public_safety.py`](../tests/test_public_safety.py), [`test_parallel_validation.py`](../tests/test_parallel_validation.py) | Working-tree versus staged bytes, complete original history, both support lanes, failure propagation, and cleanup. |

The Home Assistant fixtures use real Core registries, state machines, and service
dispatch with controlled source entities and writers. They do not connect to a
thermostat or an Ecobee account. Add a regression at the boundary where the defect
appears; for timing and lifecycle changes, include the interrupted or late-event
case as well as successful completion.

Check formatting and types during development:

```bash
python -m ruff format custom_components tests scripts
python -m ruff check custom_components tests scripts
python -m mypy --strict custom_components/ecobee_unified
python -m pytest tests -q
```

Use pytest for the complete suite: unittest discovery alone does not collect the
module-level async Home Assistant tests. Focused tests speed up iteration; run the applicable candidate checks before handing off a code change.

### Maintain the native help alongside behavior

[`translations/en.json`](../custom_components/ecobee_unified/translations/en.json)
owns the English forms, entity labels, Repairs, and translated action errors.
Keep this single runtime translation owner; do not add a `strings.json` mirror.
[`services.yaml`](../custom_components/ecobee_unified/services.yaml) owns action
editor names, descriptions, and selectors, while Python owns executable input
validation. Preserve stable keys and placeholders when changing explanations.
The current date/time schema-format errors in `climate.py` are direct Python
messages and are an explicit exception to the translation owner. The existing
public-content tests check the runtime language file and help completeness.

## Know what the gate proves

[`Validate`](../.github/workflows/validate.yaml) runs on pull requests, pushes to
`main`, and manual dispatch. A manual full dispatch requires success from all
five jobs: static/unit validation, minimum Core, current Core, Hassfest, and HACS.
The local `all` command includes Hassfest but does **not** run the HACS action;
local success alone does not satisfy that CI gate. Neither route proves physical
device behavior, authorizes a deployment, or publishes a release.

[`check_public_safety.py`](../scripts/check_public_safety.py) checks three
different surfaces: current tracked and nonignored untracked files, an archive
of Git-index bytes, and available original Git history. In a native checkout,
that archive contains the original staged bytes. Container mode instead archives
the synthetic index created from its working-tree snapshot; it does not validate
an original staged version that differs from the working tree. Validate the exact
committed candidate before publication. The guard checks content
and names for private paths, addresses, hostnames, credential-like strings,
non-example email addresses, and unreviewed binary content. A clean working tree
scan cannot substitute for the history or staged-archive scan. Ignored files are
outside its working-tree scope; keep private fixtures and deployment evidence
out of distributable source. The guard is a bounded pattern check, so review
new public material as well.

Installation acceptance and consumer migration are covered in the
[operating guide](user-guide.md#verify-an-installation-before-moving-its-consumers).

## Maintain the support contract

Change each Core and harness pair together, verify dependency consistency, and rerun
the changed support lane. Reuse unchanged sibling-lane evidence. A minimum-version change also affects [`hacs.json`](../hacs.json).
Keep the runner, CI job labels, and assertions in `test_public_safety.py` aligned;
review the external assumptions in [Upstream contracts](upstream-contracts.md).
Tool versions and container digests are owned by
[`verify-release-local.sh`](../scripts/verify-release-local.sh). Dependabot updates
GitHub Actions weekly with a seven-day cooldown; it does not update the coupled
Python support lanes.

Record candidate-specific results with the exact commit in the pull request or
release record. Preserve past release evidence in Git history and GitHub
releases; this guide describes how to reproduce checks rather than maintaining
a rolling record of test counts or deployment receipts.
