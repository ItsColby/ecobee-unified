# Ecobee Unified

Ecobee Unified presents one Home Assistant thermostat device surface for each
explicitly mapped physical thermostat. HomeKit Controller owns local climate
state and standard control; Ecobee supplies vendor detail and actions. Beestat
keeps its independently owned schedule, transition, filter, alert, and history
entities on the same device. Unified adds no credentials or direct API client.

The distribution minimum is Home Assistant Core 2026.8.0. A second test lane
covers Core 2026.9.1; both use matching published harnesses and verify final
dependency compatibility. Source, immutable releases, HACS installation, and
live deployment remain separately verifiable states.

## Configuration

Map the existing HomeKit Controller and Ecobee climate entities for each
thermostat. Their supported device serial and identifier must prove they refer
to the same physical thermostat. The native setup flow collects one or more
mappings in a single config entry, classified as a hub on the Integrations
dashboard. To manage it, open the existing Ecobee Unified entry under
**Settings > Devices & services**; adding another entry reports that it is
already configured.

Use the entry's **Reconfigure** action to add, edit, or remove thermostat
mappings. Use **Configure** to change the Ecobee source-staleness and command-
confirmation timing thresholds. Both default to 30 minutes and accept whole,
selector-aligned seconds. Every field has an inline description in Home
Assistant's native editor.

Optional explicit selections are:

- HomeKit current-temperature sensor, Current Mode select, and Clear Hold
  button on the mapped HomeKit thermostat device.
- Ecobee AQI, CO2, VOC, and thermostat-display notification entities on the
  mapped Ecobee thermostat device. Sensor classes and units must match their
  roles; one optional source cannot fill multiple roles.

Current Mode must advertise only Home, Sleep, or Away options. Administrative
and diagnostic actions are rejected. Clear Hold has no definitive role marker
in the supported registry: selecting an otherwise eligible button still relies
on the user identifying the correct action. These contracts are rechecked during
runtime, source recovery, and dispatch. Explicit live sensor units/classes take
precedence over registry defaults; unsupported metadata degrades the sensor
instead of relabeling its value.

Mappings store registry IDs, so renames survive. Missing sources are preserved
without guessing replacements. Changing a physical association or command
writer requires confirmation; a concurrent configuration change stops the stale
session without overwriting newer data. Later loss of cross-backend identity
proof preserves local HomeKit state/control while blocking all Ecobee reads,
actions, notifications, and metadata fusion until identity recovers.

## Entities and daily use

Each mapping links the following Unified entities to the HomeKit-owned device:

| Entity | Availability and role |
|---|---|
| Unified climate | Standard HVAC mode, temperature/range, target humidity, fan, and on/off control; presets when Current Mode is mapped. Controls follow the writer's advertised capabilities and bounds. |
| Source degraded problem entity | Enabled diagnostic entity for actionable faults, with bounded advisory detail. |
| Resume program button | Optional, backed by the mapped local Clear Hold action. |
| Minimum fan runtime number | Ecobee fan minutes per hour, from 0 through 60 in five-minute increments. |
| Equipment stage sensor | Bounded Ecobee equipment state; an empty healthy report means idle. |
| AQI, CO2, VOC sensors | Optional explicitly mapped Ecobee estimates. |
| Thermostat notification entity | Optional explicitly mapped Ecobee display-message writer. |

An optional precise HomeKit sensor refines the climate's current temperature
without creating another temperature entity. Humidity, occupancy, weather,
schedule, transition, and historical series are not duplicated. Beestat and
other source-owned context can remain beside the Unified climate; temperature
graphs may continue to plot the explicitly mapped precise sensor.

After release, installation, and consumer migration, dashboards, widgets,
Assist, automations, and scripts should target the Unified controls. Raw sources
stay enabled for acquisition, diagnostics, and rollback. Hide them from routine
presentation only after their consumers have migrated; Unified does not take
over transport, credentials, config entries, or Recorder ownership.

The climate and diagnostic entities share one normalized snapshot. Compact
attributes expose provenance, source health, and command status. Actionable
`problem_reasons` and bounded `advisories` remain separate while the legacy
`degradation`/`reasons` union stays compatible. An unknown Current Mode is only
an advisory when its select remains writable with valid options. Actual writer
loss removes preset control. A present `unknown` source remains distinct from
an unavailable or missing source.

Detailed diagnostics omit mapping names, entity/device/config-entry IDs, and
source values. Exact advancing ages are calculated there rather than stored in
climate state; active-sensor detail and command status remain live but
unrecorded. See [architecture](docs/architecture.md#entity-surface) for the full
projection and Recorder contract.

## Actions

Every operation uses one mapped writer, with no retries or automatic write
failover. Read fallback never changes command ownership.

| Action | Effect and reported outcome |
|---|---|
| Standard climate controls | One HomeKit climate call, with operation-specific source confirmation after writer success. |
| Preset selection | One mapped HomeKit Current Mode selection. An unreadable current option does not disable an available select with valid options. |
| `ecobee_unified.resume_program` or Resume program button | One mapped HomeKit Clear Hold dispatch, independently of Current Mode. Success is `submitted`, not `confirmed`: supported source state cannot prove that the hold cleared. HomeKit owns any internal protocol writes. |
| Minimum fan runtime | One mapped Ecobee `set_fan_min_on_time` call; off-step values fail before I/O. |
| Thermostat notification | One non-empty message to the mapped Ecobee notification entity; unsupported titles are ignored as by the source. |
| `ecobee_unified.create_vacation`, `delete_vacation`, `set_occupancy_modes`, `set_sensors_used_in_climate` | Bounded actions targeting a Unified climate, which injects its mapped Ecobee writer. Unprojectable effects are reported as `submitted`, not `confirmed`. |

The native action editor describes inputs and bounds. Vacation temperatures use
the mapped writer's unit and bounds; comfort sensors must belong to its Ecobee
config entry. Built-in Home/Away/Sleep profile names are translated at the
writer boundary, and an omitted profile uses the current Ecobee climate mode.

Unloading rejects queued commands before dispatch. An already dispatched action
may still take effect; unloading or cancelling its caller cannot undo it or
prove its outcome. Late completion cannot revive the stopped manager's state,
listeners, or timers. Confirmation timeouts never issue a second write. See
[command policy](docs/architecture.md#command-policy) for ordering, observation,
and quantization rules.

## Temperature and source limits

The precise HomeKit temperature is eligible only while finite, unit-compatible,
and consistent with the local climate's serialization envelope. Otherwise the
climate uses its documented local, then Ecobee, read fallback with explicit
provenance and degradation. Eligible precision is retained without averaging,
smoothing, comfort-range clipping, or selecting whichever source reports last.

Routine paired temperature updates settle for 250 ms to avoid a false transient
disagreement. After a confirmed rejection, the precise source must provide a
different finite value that agrees with the local climate before precision
returns. Repeated reports, availability cycles, renames, or unit conversion do
not prove recovery. `homekit_temperature_recovery_pending` explains that state.

This guard detects observed inconsistency, not physical accuracy. Quiet agreeing
sources remain valid; a faulty climate comparator can suppress valid precise
data. Only the last rejected value is remembered, and reload/restart clears that
memory. Matching stale values at startup cannot be identified without new
evidence. The guard performs no source reloads, thermostat commands, or history
edits. The [architecture](docs/architecture.md#updates-and-availability) defines
the complete observation and recovery contract.

HomeKit push/event silence is diagnostic age, not unavailability. Ecobee's
cadence-backed freshness limit can disable vendor reads/actions, but never
changes the selected writer. The 30-minute freshness and confirmation defaults
come from read-only cadence evidence; command-specific live validation remains
separate. Target-humidity granularity stays unset because the supported HomeKit
writer contract exposes no step. Microphone and daylight-saving administration
remain with their native owners.

## Validation and documentation

Before publishing a release candidate, run:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-release-local.ps1
```

The runner uses `Ubuntu-24.04` WSL2 and rootless Podman with pinned images for
unit/static, minimum/current Home Assistant, and Hassfest checks. Hosted jobs
use the same product script in native mode. HACS checks the pushed repository
through GitHub's API and remains a separate hosted delivery gate; no local
GitHub credential is needed. Local containers reuse pip downloads and wheels in
the Podman volume `ecobee-unified-validation-pip`; each lane still installs its
dependencies into a fresh container and reruns every check. The disposable cache
contains neither installed environments nor validation results and can be
removed with `podman volume rm ecobee-unified-validation-pip` when no local
validation is running. Container pip installs defer dependency bytecode until
imports, and mypy checks run without writing a cache. The explicit product
`compileall` check and every test remain enabled.
After unit/static checks pass, the local `all` container
runner overlaps the minimum/current HA lanes against a read-only snapshot. It
waits for both results before cleanup and runs Hassfest only when both pass;
native execution remains sequential. The [validation plan](docs/validation-plan.md)
owns coverage, public-payload checks, shadow acceptance, and migration proof.
Live acceptance has no mandatory elapsed-time minimum; unobserved command paths
remain explicit limitations. Publication, installation, restart, and live
consumer changes require their own validation.

[Architecture](docs/architecture.md) defines behavior and failure boundaries;
[requirements](docs/requirements.md) define acceptance, and
[decisions](docs/decisions.md) retain rationale. Dated upstream facts and primary
sources are in [upstream contracts](docs/upstream-contracts.md). The
[historical checkpoint](docs/source-candidate-status.md) preserves the first
source candidate's validation boundary. [Upstream opportunities](docs/upstream-opportunities.md)
remain undecided and are not product dependencies.
