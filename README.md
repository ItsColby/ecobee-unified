# Ecobee Unified

Ecobee Unified is an unreleased Home Assistant helper integration that presents
one canonical user-facing device surface for each explicitly mapped physical
thermostat. It combines supported Home Assistant entity state without becoming
another Ecobee or Beestat API client.

The candidate keeps Home Assistant Core 2026.8.0 as its distribution minimum
and dependency-closed harness lane. Bounded direct validation also targets the
installed Core 2026.8.1 patch, whose compatible published test harness is still
pending. A local candidate commit is not remote Git integration, publication,
release, deployment, or HACS availability; none of those later states is
claimed here.

The complete presentation-versus-transport boundary and batch disposition is
documented in [Unified Surface Convergence](docs/unified-surface-convergence.md).
Separate, undecided Home Assistant Core improvement ideas are recorded in
[Upstream Opportunities](docs/upstream-opportunities.md); the product does not
depend on them.

## Ownership and behavior

- HomeKit Controller owns standard climate state/control, optional precise
  same-device current temperature, current-mode presets, and the optional local
  clear-hold action.
- Ecobee owns vendor detail, thermostat-display notifications, and the
  minimum-fan-runtime cloud action.
- Bounded Unified actions route vacation, occupancy-policy, and comfort-sensor
  participation changes to the mapped Ecobee climate without exposing raw
  backend targets.
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
Registry listeners react only to mapped source/device changes and owned helper
relinking. Removing a mapping or optional projection removes only that config
entry's now-orphaned Unified registry entities on reload.
The supported HomeKit device serial and Ecobee device identifier must also
match. If that proof later disappears, local HomeKit state and control remain
usable while every Ecobee read, action, notification, and metadata fusion fails
closed until the registry pairing recovers.

## Entity surface

Each mapping creates one climate, one minimum-fan-runtime number, one bounded
equipment-stage sensor, optional explicitly mapped AQI/CO2/VOC sensors, and an
optional thermostat-display notification entity, all linked to the selected
HomeKit device using the Core 2026.8 helper pattern. A mapped same-device
HomeKit temperature sensor can supply the climate's precise current-temperature
value without creating another temperature entity; it must advertise a
temperature device class and compatible unit. Current-humidity, occupancy,
weather, schedule, and transition entities are not duplicated. The canonical
climate also exposes the mapped HomeKit target-humidity capability. Entity
properties project one immutable normalized snapshot and perform no I/O.
Compact climate attributes expose field provenance, source age/health,
degradation, bounded Ecobee context, and revision-guarded command confirmation.
Volatile source-age, active-sensor, and command-confirmation attributes remain
visible live but are excluded from Recorder.

Detailed diagnostics are allow-listed and omit mapping names, entity IDs,
device IDs, config-entry IDs, and source values. Public-safety validation scans
the working tree, an archive built from exact stage-0 Git blobs, commit
metadata, every historical filename, and every reachable bounded Git blob.

## Configuration

The initial flow collects one or more mappings in a single config entry. Each
mapping requires a HomeKit Controller climate and an Ecobee climate. HomeKit
current-temperature sensor/current-mode select/clear-hold button and Ecobee
AQI/CO2/VOC sensors/notification entity are optional explicit same-device
selections. Selectors are filtered to the owning integration before backend
validation. AQI, CO2, and VOC selections must also advertise the matching sensor
device class and unit, and one optional source cannot fill multiple semantic
roles. Reconfiguration supports explicit add, edit, and remove operations.
Editing physical association or command routing requires a second confirmation.

When the explicit HomeKit temperature sensor is selected, the Unified climate
also advertises tenths precision so Home Assistant preserves the source's honest
fractional value in climate state instead of rounding it back to whole degrees.

Options expose the cadence-backed Ecobee freshness threshold and the
command-confirmation window. Both default to 30 minutes, calibrated above the
observed cloud-reporting tail while retaining a bounded silent-wedge and effect
deadline. HomeKit push/event silence remains diagnostic age; only actual source
unavailability changes HomeKit health or read ownership.

## Actions

Climate preset selection calls the mapped HomeKit Current Mode select exactly
once and only for an advertised option.

`ecobee_unified.resume_program` targets a unified climate entity and presses the
mapped local HomeKit Clear Hold button exactly once.

The same operation is available as an optional **Resume program** button on the
Unified thermostat device, so dashboards do not need to expose or target the
raw HomeKit button. It is available only while both the clear-hold writer and
the current-mode observation source needed for confirmation are usable.

The minimum-fan-runtime number accepts 0 through 60 minutes and calls the
mapped Ecobee `set_fan_min_on_time` action exactly once. Values must align to
the writer's advertised five-minute step; off-step requests fail before I/O.

The optional notification entity sends one non-empty message through the mapped
Ecobee notification entity. It never calls the Ecobee API directly, retries,
or fails over to another writer; unsupported titles are ignored consistently
with the source entity.

`ecobee_unified.create_vacation`, `delete_vacation`,
`set_occupancy_modes`, and `set_sensors_used_in_climate` target a Unified
climate entity and inject its explicitly mapped Ecobee climate writer. Inputs
are bounded and capability-checked before exactly one call; comfort sensors
must belong to the mapped Ecobee config entry. Because public
source state cannot prove the complete resulting vacation or policy,
successful dispatch is reported as `submitted`, not `confirmed`.

Standard HVAC mode, temperature/range, target-humidity, fan, turn-on, and
turn-off operations call only the selected HomeKit climate. Temperature unit
and safety bounds remain writer-owned. When the mapped HomeKit adapter omits
its proven native step, the matching Ecobee climate may supply only that static
same-device presentation metadata; it never replaces the primary reading.

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

The formal minimum lane installs the compatible Home Assistant harness first,
Core 2026.8.0 second, product-owned typing tools last, and then proves the final
environment before tests. Installed Core 2026.8.1 receives bounded direct API
validation until the real harness publishes a dependency-compatible release:

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

- The 30-minute Ecobee cadence-health and confirmation defaults were calibrated
  from read-only live reporting evidence and still require command-specific
  shadow validation before a release.
- No automatic write failover exists.
- Ecobee microphone and daylight-saving administration remain outside the
  routine Unified surface.
- Raw source entities remain required and recoverable.
- No historical series, Beestat schedule/transition, raw diagnostics,
  arbitrary backend errors, or room-sensor duplicates are re-exported.
- Installation, restart, live validation, consumer migration, publication,
  and release are separate gates.
