# Development

Run commands below from the repository root.

## Validation lanes

The [Validate workflow](../.github/workflows/validate.yaml) runs on pull
requests, pushes to `main`, and manual dispatch. Its **Release gate** requires
success from every job:

| Job | What it runs |
| --- | --- |
| Unit tests and static validation | `pre-commit run --all-files`: Ruff, ShellCheck, actionlint, zizmor, JSON and whitespace hygiene, and Gitleaks over the current tree and Git history. |
| Home Assistant integration tests | One job per support lane: the Core pin in [`requirements-ha-test.txt`](../requirements-ha-test.txt) (minimum) or [`requirements-ha-current.txt`](../requirements-ha-current.txt) (current) with its matching harness, `pip check`, strict mypy, and the complete pytest suite. |
| Hassfest and HACS | The official Home Assistant and HACS validation actions. |

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
environment for each support lane and install it the way its workflow job does:
the harness and mypy pins first, then the lane's requirements file, then
`python -m pip check`. Installing only the harness does not establish the
intended Core version.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
# install the lane's pins as its workflow job does, then:
python -m pip check
python -m mypy custom_components/ecobee_unified
python -m pytest tests
```

Use pytest for the complete suite: unittest discovery alone does not collect the
module-level async Home Assistant tests. Focused tests speed up iteration.

Choose regression coverage by the contract being changed:

| Change | Start with | Check the failure boundary |
| --- | --- | --- |
| Values, units, fallback, or precision | [`test_models.py`](../tests/test_models.py), [`test_numeric_validity.py`](../tests/test_numeric_validity.py), [`test_temperature_quality.py`](../tests/test_temperature_quality.py) | Malformed and impossible values, source ownership, serialization tolerance, and recovery from rejected temperature evidence. |
| Equipment-stage projection | [`test_sensor.py`](../tests/test_sensor.py) | Bounded native enum values, mixed or unknown equipment signals, subordinate fan activity, and complete translations. |
| Read policies and action/stage coherence | [`test_operating_state.py`](../tests/test_operating_state.py) | Paired mappings, source disagreement, unavailable action, fallback, and unchanged command authority. |
| Datapoint composition and configuration | [`test_datapoints.py`](../tests/test_datapoints.py), [`test_datapoint_config.py`](../tests/test_datapoint_config.py) | Physical identity, semantic distinctions, metadata timing, source recovery, stable IDs, and concurrent configuration changes. |
| Native weather aliases and daily forecasts | [`test_weather_source.py`](../tests/test_weather_source.py), [`test_weather.py`](../tests/test_weather.py) | Station drift, provider age, unit conversion, changes during awaited reads, source subscriptions, and unload. |
| Daily historical families and native report | [`test_historical.py`](../tests/test_historical.py), [`test_history_source.py`](../tests/test_history_source.py), [`test_historical_config.py`](../tests/test_historical_config.py), [`test_history_service.py`](../tests/test_history_service.py) | Calendar bounds, native metadata and identity drift, method acceptance, missing/provisional values, admin context, source changes during reads, and the real named-script response. |
| Mapping validation or source identity | [`test_configuration_source_contracts.py`](../tests/test_configuration_source_contracts.py), [`test_homekit_action_roles.py`](../tests/test_homekit_action_roles.py), [`test_source_device_identity.py`](../tests/test_source_device_identity.py) | Live metadata, missing saved references, device association, role drift, and recovery. |
| Writer routing or command state | [`test_commands.py`](../tests/test_commands.py), [`test_command_lifecycle.py`](../tests/test_command_lifecycle.py) | Exact target and call count, observations before acceptance, superseded revisions, cancellation, and unload. |
| Core APIs, configuration flows, or entity behavior | [`test_runtime_core_api.py`](../tests/test_runtime_core_api.py), [`test_integration_ha.py`](../tests/test_integration_ha.py), [`test_setup_lifecycle.py`](../tests/test_setup_lifecycle.py) | Native registry, state, service, setup, reload, repair, and serialized entity behavior. |
| Public metadata and help text | [`test_metadata.py`](../tests/test_metadata.py) | Distribution minimum, complete runtime translations, and nonblank field descriptions. |

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
messages and are an explicit exception to the translation owner. [`test_metadata.py`](../tests/test_metadata.py) checks the runtime language
file and help completeness.

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
[`hacs.json`](../hacs.json). Keep the workflow job labels and distribution
minimum aligned, and review the external assumptions in
[Upstream contracts](upstream-contracts.md). Dependabot updates GitHub Actions
weekly with a seven-day cooldown; it does not update the coupled Python support
lanes.

Record candidate-specific results with the exact commit in the pull request or
release record. Preserve past release evidence in Git history and GitHub
releases; this guide describes how to reproduce checks rather than maintaining
a rolling record of test counts or deployment receipts.
