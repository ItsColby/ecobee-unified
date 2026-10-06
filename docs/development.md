# Development

Run commands below from the repository root.

## Validation lanes

The [Validate workflow](../.github/workflows/validate.yaml) defines the CI jobs
and the **Release gate** that requires them,
[`.pre-commit-config.yaml`](../.pre-commit-config.yaml) the static hooks, and
[`.github/dependabot.yml`](../.github/dependabot.yml) the dependency update
policy. The [Dependabot auto-merge workflow](../.github/workflows/dependabot-auto-merge.yaml)
merges its Actions and pre-commit updates once required checks pass.

Local checks do not replace the hosted jobs. Neither route proves physical
device behavior, authorizes a deployment, or publishes a release.

## Work on one change

Install the static checks once and run them before pushing:

```bash
python -m pip install --group dev
pre-commit install
pre-commit run --all-files
```

The Home Assistant tests need Linux (or WSL) and Python 3.14. Use a separate
environment for each support lane. The `ha-minimum` and `ha-current`
[dependency groups](../pyproject.toml) pin the lane's Core version, test harness
and mypy together; installing only the harness does not establish the intended
Core version.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --group ha-current  # or ha-minimum
python -m pip check
python -m mypy
python -m pytest tests
```

Use pytest for the complete suite: unittest discovery alone does not collect the
module-level async Home Assistant tests. Focused tests speed up iteration.

The Home Assistant fixtures use real Core registries, state machines, and service
dispatch with controlled source entities and writers. They do not connect to a
thermostat or an Ecobee account. Add a regression at the boundary where the defect
appears; for timing and lifecycle changes, include the interrupted or late-event
case as well as successful completion.

### Maintain the native help alongside behavior

[`translations/en.json`](../custom_components/ecobee_unified/translations/en.json)
owns the English forms, entity labels, Repairs, and translated action errors.
Keep this single runtime translation owner; do not add a `strings.json` mirror.
[`services.yaml`](../custom_components/ecobee_unified/services.yaml) owns action
editor names, descriptions, and selectors, while Python owns executable input
validation. Preserve stable keys and placeholders when changing explanations.
The current date/time schema-format errors in `climate.py` are direct Python
messages and are an explicit exception to the translation owner. The
metadata tests check the runtime language file and help completeness.

## Keep public content safe

Gitleaks uses its default credential rules plus the repository's
[`.gitleaks.toml`](../.gitleaks.toml) rules for private paths, addresses,
hostnames, and non-example email addresses, over both the working tree and Git
history. Ignored files are outside its scope; keep private fixtures and
deployment evidence out of distributable source. It is a bounded pattern check,
so review new public material as well.

Installation acceptance and consumer migration are covered in the
[operating guide](user-guide.md#verify-an-installation-before-moving-its-consumers).

## Maintain the support contract

Change each Core and harness pair together, verify dependency consistency, and
rerun the changed support lane. A minimum-version change also affects
[`hacs.json`](../hacs.json). Review the external assumptions in
[Upstream contracts](upstream-contracts.md). [Dependabot](../.github/dependabot.yml)
does not update the coupled Python support lanes.

Record candidate-specific results with the exact commit in the pull request or
release record. Preserve past release evidence in Git history and GitHub
releases; this guide describes how to reproduce checks rather than maintaining
a rolling record of test counts or deployment receipts.

## Prepare a release

Releases are immutable [GitHub Releases](https://github.com/ItsColby/ecobee-unified/releases)
whose tag matches the manifest version.
