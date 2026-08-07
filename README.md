# Ecobee Unified

Ecobee Unified is an unreleased Home Assistant helper integration that presents
one canonical climate entity for each explicitly mapped physical thermostat. It
combines supported Home Assistant entity state without becoming another Ecobee
or Beestat API client.

The source candidate targets Home Assistant Core 2026.8.0. It is not a release,
has not been deployed, and is not yet offered through a public repository or
HACS.

## Ownership and behavior

- HomeKit Controller owns standard climate state and every standard climate
  command.
- Ecobee owns vendor detail plus the explicit `resume_program` and
  `set_minimum_fan_runtime` actions.
- Optional Beestat Statistics entities provide read-only scheduled-profile and
  next-transition context.
- Each semantic has one deterministic source and documented read fallback.
  Values are never averaged and a newer timestamp never changes ownership.
- Read fallback never changes the command writer. A timeout never retries a
  command through another backend.

The integration stores entity-registry IDs rather than names, so registry
renames survive. Missing selections are preserved and require explicit
reconfiguration; replacements are never guessed.

## Entity surface

Each mapping creates one climate entity linked to the selected HomeKit device
using the Core 2026.8 helper-device pattern. Entity properties project one
immutable normalized snapshot and perform no I/O. Compact attributes expose
field provenance, source age/health, degradation, bounded Ecobee context,
optional schedule context, and revision-guarded command confirmation. Volatile
source-age, active-sensor, and command-confirmation attributes remain visible
live but are excluded from Recorder.

Detailed diagnostics are allow-listed and omit mapping names, entity IDs,
device IDs, config-entry IDs, and source values. Public-safety validation scans
the working tree, tracked archive, commit metadata, every historical filename,
and every reachable bounded Git blob.

## Configuration

The initial flow collects one or more mappings in a single config entry. Each
mapping requires a HomeKit Controller climate and an Ecobee climate; Beestat
context is optional. Reconfiguration supports explicit add, edit, and remove
operations. Editing physical association or command routing requires a second
confirmation.

Options expose documented freshness thresholds and the command-confirmation
window. Freshness affects health and read fallback only.

## Actions

`ecobee_unified.resume_program` targets a unified climate entity and calls the
mapped Ecobee `resume_program` action exactly once.

`ecobee_unified.set_minimum_fan_runtime` targets a unified climate entity,
accepts 0 through 60 minutes, and calls the mapped Ecobee
`set_fan_min_on_time` action exactly once.

Standard HVAC mode, temperature/range, fan, turn-on, and turn-off operations
call only the selected HomeKit climate.

## Validation

The local, dependency-light tier is:

```text
python -m unittest tests.test_public_safety
python -m compileall -q custom_components/ecobee_unified tests scripts
python -m ruff format --check custom_components tests scripts
python -m ruff check custom_components tests scripts
python scripts/check_public_safety.py
actionlint
```

The exact Core lane installs the compatible Home Assistant harness first, Core
2026.8.0 second, product-owned typing tools last, and then proves the final
environment before tests:

```text
python -m pip install "pytest-homeassistant-custom-component==0.13.354"
python -m pip install --upgrade -r requirements-ha-test.txt
python -m pip install "mypy==2.3.0"
python -m pip check
python -m mypy --strict custom_components/ecobee_unified
pytest tests -q
```

The Home Assistant test surface is Linux-owned because Core imports POSIX-only
modules. Hassfest and HACS validation are defined in CI but cannot be claimed
green until a separately authorized public repository runs them.

## Known limits

- The source-health and confirmation defaults need private shadow-deployment
  measurement before a release.
- No automatic write failover exists.
- Raw source entities remain required and recoverable.
- No historical series, raw diagnostics, arbitrary backend errors, or room
  sensor duplicates are re-exported.
- Installation, restart, live validation, consumer migration, publication,
  and release are separate gates.
