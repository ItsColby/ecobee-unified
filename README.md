# Ecobee Unified

Ecobee Unified is an unreleased Home Assistant helper integration that presents
one canonical user-facing device surface for each explicitly mapped physical
thermostat. It combines supported Home Assistant entity state without becoming
another Ecobee or Beestat API client.

The locally validated and committed candidate source targets Home Assistant
Core 2026.8.0. A local candidate commit is not remote Git integration,
publication, release, deployment, or HACS availability; none of those later
states is claimed here.

The complete presentation-versus-transport boundary and batch disposition is
documented in [Unified Surface Convergence](docs/unified-surface-convergence.md).

## Ownership and behavior

- HomeKit Controller owns standard climate state/control, optional current-mode
  presets, and the optional local clear-hold action.
- Ecobee owns vendor detail and the minimum-fan-runtime cloud action.
- Beestat Statistics retains first-class schedule, transition, filter, alert,
  history-import, and Recorder ownership while linking its own enrichment
  entities to the same physical device.
- Each semantic has one deterministic source and documented read fallback.
  Values are never averaged and a newer timestamp never changes ownership.
- Read fallback never changes the command writer. A timeout never retries a
  command through another backend.

The integration stores entity-registry IDs rather than names, so registry
renames survive. Missing selections are preserved and require explicit
reconfiguration; replacements are never guessed.

## Entity surface

Each mapping creates one climate, one minimum-fan-runtime number, one bounded
equipment-stage sensor, and optional explicitly mapped AQI/CO2/VOC sensors,
all linked to the selected HomeKit device using the Core 2026.8 helper pattern.
Temperature, humidity, occupancy, weather, schedule, and transition are not
duplicated. Entity properties project one immutable normalized snapshot and
perform no I/O. Compact climate attributes expose field provenance, source
age/health, degradation, bounded Ecobee context, and revision-guarded command confirmation. Volatile
source-age, active-sensor, and command-confirmation attributes remain visible
live but are excluded from Recorder.

Detailed diagnostics are allow-listed and omit mapping names, entity IDs,
device IDs, config-entry IDs, and source values. Public-safety validation scans
the working tree, tracked archive, commit metadata, every historical filename,
and every reachable bounded Git blob.

## Configuration

The initial flow collects one or more mappings in a single config entry. Each
mapping requires a HomeKit Controller climate and an Ecobee climate. The
HomeKit current-mode select/clear-hold button and Ecobee AQI/CO2/VOC sensors are
optional explicit same-device selections. Reconfiguration supports explicit
add, edit, and remove operations. Editing physical association or command
routing requires a second confirmation.

Options expose documented freshness thresholds and the command-confirmation
window. Freshness affects health and read fallback only.

## Actions

Climate preset selection calls the mapped HomeKit Current Mode select exactly
once and only for an advertised option.

`ecobee_unified.resume_program` targets a unified climate entity and presses the
mapped local HomeKit Clear Hold button exactly once.

The minimum-fan-runtime number accepts 0 through 60 minutes and calls the
mapped Ecobee `set_fan_min_on_time` action exactly once.

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
- No historical series, Beestat schedule/transition, raw diagnostics,
  arbitrary backend errors, or room-sensor duplicates are re-exported.
- Installation, restart, live validation, consumer migration, publication,
  and release are separate gates.
