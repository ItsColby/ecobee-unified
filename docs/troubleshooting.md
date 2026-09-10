# Troubleshooting

Start with the mapped source entities and the Unified climate's `source_health`,
`selected_sources`, `problem_reasons`, and `advisories` attributes. A working
Unified readout does not prove every writer is available. The Source degraded
binary sensor summarizes normalized problems; optional Clear Hold and
notification faults also need their entity availability and Repairs checked.

## Setup or reconfiguration fails

| Symptom | Check and remedy |
|---|---|
| Already configured | Open the existing Ecobee Unified entry and use Reconfigure |
| Physical identity mismatch | Select the two climate entities for the same thermostat; matching names or areas do not establish identity |
| Physical identity unproven | Check that the source devices expose the supported thermostat serial/identifier; repair their native integration or report the unsupported identity shape |
| Optional source rejected | Confirm the integration, entity domain, same-device association, and role-specific metadata in the [setup guide](setup.md) |
| Current Mode rejected | Select the actual HomeKit Current Mode select with supported Home/Sleep/Away options, not an administrative selector |
| Clear Hold rejected | Confirm the actual button role; eligibility checks cannot positively identify every Clear Hold button |
| Duplicate mapping or source | Give mappings unique names and do not reuse required or optional sources |
| Configuration changed | Another session saved first; reopen the flow and review the current saved configuration |
| Cannot remove the last mapping | Keep one mapping, or remove the Unified integration entry after repointing its consumers |

Renaming an existing source preserves its registry reference. Deleting and
recreating it may not. A mapping retains a missing reference rather than
choosing a similar name; use Reconfigure to select a replacement deliberately.
Never repair a mapping by editing Home Assistant's `.storage` files.

## State is present but controls are missing

Standard HVAC controls require the mapped HomeKit writer and its advertised
capabilities. Ecobee can supply permitted read fallback without becoming the
standard command writer. Repair the HomeKit connection in its native integration
if local controls disappear.

Preset selection needs the optional Current Mode select. An `unknown` current
option is an advisory if the select is otherwise available with valid options;
it can still accept an advertised choice. Resume program instead needs the
independently mapped Clear Hold button. Neither capability substitutes for the
other.

If the source device association no longer proves the HomeKit and Ecobee
entities are the same thermostat, Unified blocks Ecobee readings, actions,
notifications, and metadata fusion. Local HomeKit state/control remains usable
when its own source contract is valid. Restore the source identity or explicitly
correct the mapping; recovery is reevaluated from source and registry changes.

## Ecobee detail is stale or unavailable

`missing` means the configured source could not be resolved; `unavailable` and
`unknown` reflect different source states. `stale` applies when an otherwise
usable Ecobee source exceeds the configured report-age threshold. Check the
Ecobee integration's connection and authentication first. Unified owns no
credential and cannot reauthenticate it.

The threshold uses Home Assistant source-report timing, not proof of a new
physical measurement or a successful cloud transaction. An unchanged reported
value can be fresh. HomeKit push/event silence is diagnostic age and does not
alone disable local control. Increasing a threshold hides neither missing data
nor identity faults and does not refresh upstream data.

Unsupported live sensor units or device classes invalidate that optional
projection. Explicit live metadata takes precedence over registry defaults;
Unified does not silently relabel a reading. An absent equipment report is not
idle; idle requires a usable empty report.

## Temperature detail disappears

An optional HomeKit temperature sensor contributes finer detail only while its
finite, unit-compatible reading agrees with the mapped HomeKit climate's
serialization envelope. Otherwise Unified uses the eligible local climate
reading, then its permitted Ecobee read fallback, with provenance and degradation.
It does not average, smooth, or choose the newest of competing readings.

Related HomeKit updates have a 250 ms settling window. After a confirmed
disagreement, `homekit_temperature_recovery_pending` means the precise sensor
must report a different finite value that also agrees with the climate before
its detail can return. A repeated value, an availability cycle, a rename, or a
unit conversion is not new measurement evidence.

This is a consistency check, not a calibration or physical-accuracy test. Quiet
agreeing sources remain eligible; a faulty climate comparator can reject a good
sensor. Reload/restart clears the remembered rejected value, and matching stale
values at startup cannot be detected without new evidence. Restarting to clear
the indicator does not establish recovery. Investigate the upstream readings;
Unified performs no source reload, thermostat write, or history correction as
part of this guard.

## An action did not visibly take effect

Check the [command result definitions](usage.md#understand-command-results).
`submitted` means the source call returned successfully without a supported
confirmation projection. `unconfirmed` means matching evidence did not arrive
before the deadline, or cancellation/unload interrupted tracking. Neither status
proves that the action failed.

Inspect the relevant source state or thermostat before deciding whether to
retry. Unified never retries automatically. A newer tracked command replaces
the previous summary, and notifications do not appear there. An action already
dispatched during a reload may still take effect, even if its caller was
cancelled or its final status is no longer available.

For rejected inputs, use the native action editor's help: temperatures follow
the selected writer's units and bounds; vacation dates/times are paired; comfort
sensor IDs must belong to the mapped Ecobee entry. A device in that entry is not
necessarily a participant in the intended thermostat's comfort profile.

## Diagnostics and support

Download diagnostics from the Ecobee Unified integration entry. The export uses
bounded capability, source-health, age, degradation, and command summaries. It
omits mapping names, entity/device/config-entry IDs, raw source values, source
payloads, message text, and credentials. Live entity attributes can still contain
comfort-sensor names; screenshots and upstream logs have their own privacy
boundaries.

For an [issue report](https://github.com/ItsColby/ecobee-unified/issues), include
your Core and Unified versions, the affected capability, expected and observed
behavior, relevant generic reason codes, and steps that reproduce the problem.
State whether you observed the source state or the physical thermostat and
whether a write was actually tested. Review every attachment before sharing;
redact identifiers and household details from screenshots or upstream evidence.

Automated validation establishes source behavior under test. It does not prove
your thermostat firmware, connection, command delivery, or household automation
behavior; the [validation guide](validation-plan.md) separates these checks.
