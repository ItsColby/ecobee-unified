# Ecobee Unified

## Local release validation

Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-release-local.ps1`
before publishing a release candidate. It uses the `Ubuntu-24.04` WSL2
distribution and rootless Podman to run the same local-tree unit,
minimum/current Home Assistant, and Hassfest validation classes as the hosted
workflow. Images are pinned by digest. HACS validation reads a pushed repository
through GitHub's API, so the hosted HACS job remains the independent public
metadata and release gate rather than receiving a local GitHub credential. The
hosted unit and Home Assistant jobs call this same script in `native` mode, so
future validation changes have one product-owned command surface.

Ecobee Unified is a Home Assistant helper integration that presents one
canonical user-facing device surface for each explicitly mapped physical
thermostat. It combines supported Home Assistant entity state without becoming
another Ecobee or Beestat API client.

The integration keeps Home Assistant Core 2026.8.0 as its distribution minimum
and dependency-closed minimum lane. A second dependency-closed lane targets the
maintained Core 2026.8.1 patch with its matching published harness. Source
state, immutable GitHub releases, HACS installation, and live deployment remain
separately verifiable lifecycle states.

The complete presentation-versus-transport boundary and batch disposition is
documented in [Unified Surface Convergence](docs/unified-surface-convergence.md).
Separate, undecided Home Assistant Core improvement ideas are recorded in
[Upstream Opportunities](docs/upstream-opportunities.md); the product does not
depend on them.

Current product documentation is intentionally small and owner-specific:
[Architecture](docs/architecture.md) owns behavior and boundaries,
[Requirements](docs/requirements.md) owns acceptance,
[Decisions](docs/decisions.md) owns durable choices, and
[Validation](docs/validation-plan.md) owns proof. The concise
[historical source-candidate checkpoint](docs/source-candidate-status.md)
preserves the pre-publication boundary without duplicating current product or
release state.

## Ownership and behavior

- HomeKit Controller owns standard climate state/control, current-mode presets,
  and the optional local clear-hold action. An optional same-device HomeKit
  temperature sensor may refine current temperature only while it agrees with
  the local climate within that climate state's unit-specific serialization
  envelope.
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
- Climate state values are interpreted in Home Assistant's configured
  temperature unit, matching Core's serialized-state contract; the source
  integrations' native units are not inferred from an absent state attribute.
- An empty Ecobee equipment report is the healthy idle state. Missing or
  unusable source state remains unavailable rather than being collapsed into
  idle.
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

## Daily-use surface

After a separately authorized release, installation, and consumer migration,
routine thermostat interaction should target the Unified climate from
dashboards, widgets, Assist, automations, and scripts. Users do not need to open
or control the raw HomeKit or Ecobee device pages during normal operation.

Temperature graphs may continue to plot the explicitly mapped precise HomeKit
sensor, while Beestat schedule, transition, filter, alert, and historical
entities can appear beside the Unified climate on the same dashboard and
physical device. Those source and enrichment entities remain independently
owned data surfaces rather than duplicate Unified controls. Raw HomeKit and
Ecobee entities stay enabled for acquisition, diagnostics, and rollback and may
be hidden from routine presentation only after every consumer has migrated.

This provides one coherent human-facing thermostat surface without merging or
duplicating the underlying transports, credentials, config entries, or
Recorder ownership.

## Entity surface

Each mapping creates one climate, one minimum-fan-runtime number, one bounded
equipment-stage sensor, optional explicitly mapped AQI/CO2/VOC sensors, and an
optional thermostat-display notification entity, all linked to the selected
HomeKit device using the Core 2026.8 helper pattern. The climate uses its
translated `Unified climate` sibling name rather than repeating the mapping
name. The minimum-fan control declares duration semantics in minutes. A mapped
same-device HomeKit temperature sensor can supply the climate's precise current
temperature without creating another temperature entity only while it remains
finite, unit-compatible, and consistent with the local climate's serialized
reading. Divergence or loss of proof degrades explicitly to the local climate,
then to the documented Ecobee read fallback. Current-humidity, occupancy,
weather, schedule, and transition entities are not duplicated. The canonical
climate also exposes the mapped HomeKit target-humidity capability. Its
presentation step remains unset because the supported HomeKit entity contract
does not expose writer granularity. Entity properties project one immutable
normalized snapshot and perform no I/O.
Compact climate attributes expose field provenance, source health, degradation,
bounded Ecobee context, active-sensor detail, and revision-guarded command
status. Exact continuously advancing source and command ages are calculated
when bounded diagnostics are requested rather than stored in climate state
attributes, preventing age-only source reports from creating duplicate Recorder
rows. Active-sensor detail and command-confirmation operation/status remain live
but unrecorded.
Source health preserves Home Assistant's `unknown` versus `unavailable`
distinction: an existing source with no readable current value is not described
as absent or unavailable, and remains unusable for reads unless a separate
writer-capability contract explicitly permits control.

Detailed diagnostics are allow-listed and omit mapping names, entity IDs,
device IDs, config-entry IDs, and source values. Public-safety validation scans
the working tree, an archive built from exact stage-0 Git blobs, commit
metadata, every historical filename, and every reachable bounded Git blob.

## Configuration

The initial flow collects one or more mappings in a single config entry. The
manifest declares Home Assistant's native single-entry contract, so trying to
add Ecobee Unified again reports that it is already configured. Open the
existing Ecobee Unified entry under **Settings > Devices & services** instead.
Use the entry's **Reconfigure** action to add, edit, or remove thermostat
mappings. Use **Configure** to change the Ecobee source-staleness and command-
confirmation timing thresholds. Each
mapping requires a HomeKit Controller climate and an Ecobee climate. HomeKit
current-temperature sensor/current-mode select/clear-hold button and Ecobee
AQI/CO2/VOC sensors/notification entity are optional explicit same-device
selections. Selectors are filtered to the owning integration before backend
validation. AQI, CO2, and VOC selections must also advertise the matching sensor
device class and unit, and one optional source cannot fill multiple semantic
roles. Reconfiguration supports explicit add, edit, and remove operations.
Editing physical association or command routing requires a second confirmation.
If the saved mappings change while a reconfiguration session is open, that
session stops without overwriting the newer configuration.

When the explicit HomeKit temperature sensor is selected, the Unified climate
advertises tenths precision only while the sensor passes the local consistency
guard. This preserves an honest fractional value without choosing a source
because it merely looks more precise or allowing a quiet duplicate sensor to
silently diverge from the thermostat climate.

HomeKit may publish the climate's whole-degree serialization and the precise
temperature characteristic a few milliseconds apart. Ecobee Unified coalesces
only routine healthy precise-sensor events and climate events whose current
temperature changed for 250 ms per mapping, preventing a false intermediate
divergence state. Other climate changes, actual unavailable/recovery events,
and command observations remain immediate; a mismatch that persists after the
window still degrades and falls back explicitly.

Options expose the cadence-backed Ecobee freshness threshold and the
command-confirmation window. Saved values must be whole, selector-aligned
seconds, so direct or restored flow input cannot be silently truncated. Both
default to 30 minutes, calibrated above the observed cloud-reporting tail while
retaining a bounded silent-wedge and effect deadline. HomeKit push/event silence
remains diagnostic age; only actual source unavailability changes HomeKit
health or read ownership. Ecobee freshness can make a cadence-backed vendor
writer unsafe to use, but it never changes the selected writer or causes write
failover.

## Actions

Climate preset selection calls the mapped HomeKit Current Mode select exactly
once and only for an advertised option. An unreadable current option does not
disable that same-device writer when the select remains enabled, available,
and advertises bounded options; Unified keeps the current preset unknown while
retaining the proven write capability. An unavailable, missing, disabled, or
misassociated select still removes preset control before any effect.

`ecobee_unified.resume_program` targets a unified climate entity and presses the
mapped local HomeKit Clear Hold button exactly once.

The same operation is available as an optional **Resume program** button on
the Unified thermostat device, so dashboards do not need to expose or target
the raw HomeKit button. It is available whenever the mapped clear-hold writer
is usable, independently of Current Mode. Because the button has no supported
resulting-state contract, a successful press is reported as `submitted`, not
falsely `confirmed`.

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
must belong to the mapped Ecobee config entry, and vacation temperatures must
fit the mapped writer's advertised unit and safety bounds. Because public source
state cannot prove the complete resulting vacation or policy, successful
dispatch is reported as `submitted`, not `confirmed`.

Standard HVAC mode, temperature/range, target-humidity, fan, turn-on, and
turn-off operations call only the selected HomeKit climate. Temperature unit
and safety bounds remain writer-owned. When the mapped HomeKit adapter omits
its proven native step, the matching Ecobee climate may supply only that static
same-device presentation metadata; it never replaces the primary reading.
Temperature-command confirmation accepts only the writer's half-step
quantization envelope, while other numeric fields retain the stricter default
tolerance.

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

Both formal lanes install their matching Home Assistant harness first, exact
Core second, product-owned typing tools last, and then prove final dependency
closure before the complete test surface.

Minimum Core lane:

```text
python -m pip install "pytest-homeassistant-custom-component==0.13.354"
python -m pip install --upgrade -r requirements-ha-test.txt
python -m pip install "mypy==2.3.0"
python -m pip check
python -m mypy --strict custom_components/ecobee_unified
pytest tests -q
```

Current maintained Core lane:

```text
python -m pip install "pytest-homeassistant-custom-component==0.13.355"
python -m pip install --upgrade -r requirements-ha-current.txt
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
  from read-only live reporting evidence. Live acceptance verifies source
  behavior and presentation before consumer migration, but it has no mandatory
  elapsed-time delay.
- No automatic write failover exists.
- Ecobee microphone and daylight-saving administration remain outside the
  routine Unified surface.
- Raw source entities remain required and recoverable.
- No historical series, Beestat schedule/transition, raw diagnostics,
  arbitrary backend errors, or room-sensor duplicates are re-exported.
- Installation, restart, live validation, consumer migration, publication,
  and release are separate gates.
