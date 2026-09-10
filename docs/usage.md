# Entities and actions

## Entity reference

Each mapping places Unified entities on the mapped HomeKit thermostat device.
Home Assistant assigns entity IDs; use the IDs in your own entity registry.

| Entity | Created when | Source and meaning |
|---|---|---|
| Climate | Always | One thermostat surface; standard controls use the mapped HomeKit climate, and preset selection uses the optional Current Mode select |
| Source degraded binary sensor | Always | Diagnostic problem indicator for actionable normalized source faults; advisories are separate attributes |
| Minimum fan runtime number | Always | Ecobee minimum fan minutes per hour; accepts 0–60 in steps of 5 when its writer and current value are usable |
| Equipment stage sensor | Always | A bounded interpretation of Ecobee equipment tokens; a usable empty report means idle, missing data does not |
| Resume program button | Clear Hold is mapped | Dispatches the mapped local Clear Hold action |
| AQI, CO2, VOC sensors | Each corresponding source is mapped | Projections of the selected Ecobee estimates, not independent measurements |
| Thermostat notification entity | An Ecobee notify source is mapped | Forwards a display message to that source entity |

The climate advertises the HomeKit writer's supported controls, modes, bounds,
and units. It does not make an unsupported control available by routing it to
Ecobee. Current temperature and selected read-only fields have explicit fallback
rules; see [field ownership](architecture.md). Read availability is not write
availability.

The integration adds no duplicate humidity, occupancy, weather, schedule,
transition, or historical-series entities. Existing source and Beestat entities
can remain in the same dashboard or device presentation. Unified's equipment
stage is a calculation over vendor tokens; it is not electrical metering or
proof of actual equipment performance.

## Standard controls

Use Home Assistant's climate controls and actions with the Unified climate as
the target. Temperature uses the writer's units and bounds. For a single target
use `temperature`; for a heat/cool range use `target_temp_low` and
`target_temp_high`. HVAC modes, fan options, and presets must be advertised by
the selected writer. Target humidity requires the HomeKit capability and valid
bounds; Unified does not invent a humidity step.

This example changes a single target in the temperature unit shown by the
entity. Replace the example entity ID and choose a value appropriate for your
thermostat and mode before running it:

```yaml
action: climate.set_temperature
target:
  entity_id: climate.example_thermostat
data:
  temperature: 21
```

The optional HomeKit Current Mode select owns the climate's preset choices.
Ecobee's `ecobee_preset_mode` and `ecobee_climate_mode` attributes are separate
vendor context and need not match the local preset representation.

## Additional actions

All five `ecobee_unified` actions target a **Unified climate entity**. The
integration resolves the mapped source; do not pass a raw upstream climate as
their target. The native action editor contains the complete field help in
[services.yaml](../custom_components/ecobee_unified/services.yaml).

| Action | Inputs and limits | Writer |
|---|---|---|
| `ecobee_unified.resume_program` | No extra fields; also exposed as the Resume program button | Mapped HomeKit Clear Hold button |
| `ecobee_unified.create_vacation` | Name of 1–12 characters; heat/cool values in the mapped Ecobee climate's unit and bounds, with heat ≤ cool; optional paired start/end date and time fields; fan `auto` or `on`; integer fan minutes 0–60 | Mapped Ecobee climate |
| `ecobee_unified.delete_vacation` | Vacation name of 1–12 characters | Mapped Ecobee climate |
| `ecobee_unified.set_occupancy_modes` | At least one of boolean `auto_away` and `follow_me`; omitted fields remain omitted | Mapped Ecobee climate |
| `ecobee_unified.set_sensors_used_in_climate` | 1–32 distinct Ecobee device IDs from the mapped climate's Ecobee config entry; optional comfort-profile name | Mapped Ecobee climate |

Vacation dates use `YYYY-MM-DD` and times use `HH:MM:SS`. Supply a date and time
together for each endpoint; when both endpoints are present, end must follow
start. Omitted endpoints and timezone interpretation belong to the Ecobee
integration. Unified forwards the supplied strings without timezone conversion
and does not check vacation-name uniqueness. Vacation fan minutes allow any
integer in range; the separate Minimum fan runtime number uses five-minute
steps.

Comfort-sensor selection checks Ecobee config-entry membership, not whether each
selected sensor is attached to this particular thermostat. Select the intended
devices carefully. Omitting `preset_mode` uses the current Ecobee climate mode;
if that mode is unreadable, provide a name explicitly. Home, Away, and Sleep are
normalized to the source's built-in names. Other nonblank names up to 64
characters are forwarded for upstream handling.

To request Resume program:

```yaml
action: ecobee_unified.resume_program
target:
  entity_id: climate.example_thermostat
```

For a thermostat-display message, target the Unified notification entity with
the standard notify action. A message must contain non-whitespace text; titles
are not forwarded:

```yaml
action: notify.send_message
target:
  entity_id: notify.example_thermostat_notification
data:
  message: "Please check the thermostat schedule."
```

Unified exposes explicit actions; it does not decide vacation dates, occupancy
policy, comfort-sensor participation, notification recipients, or household
schedules. Automations spanning those decisions belong in your Home Assistant
configuration. Source features outside this surface remain with their native
integration.

## Understand command results

Unified sends each requested operation through one selected writer, serializing
dispatches for that mapping. It never automatically retries or switches writers.
The climate's `command_confirmation` attribute retains only the most recent
tracked command's revision, operation, and status; it is not an action log or a
service response payload.

| Status | Meaning |
|---|---|
| `none` | No tracked command in this manager's current lifetime |
| `pending` | Dispatch or matching source observation is still pending |
| `submitted` | The writer call succeeded, but this operation has no supported confirmation projection |
| `confirmed` | The writer call succeeded and qualifying source evidence matched the requested outcome |
| `unconfirmed` | Matching evidence did not arrive before the deadline, or cancellation/unload interrupted tracking; the effect remains uncertain |
| `failed` | The tracked writer call failed; this is not proof that an upstream side effect was impossible |

Standard climate commands, preset selection, and minimum fan runtime can be
confirmed from operation-specific source observations. All five additional
actions above are submitted-only. Clear Hold's successful call cannot prove
that the hold cleared. Notifications use native call success/failure and do
not update the command tracker or prove display delivery.

A confirmation timeout issues no second write. Inspect the source before
retrying an uncertain action. Reloading or unloading cannot undo an already
dispatched action; command-tracker state is not persisted across reloads.

## Inspect source context

The climate exposes `selected_sources`, `source_health`, `problem_reasons`, and
`advisories`. These describe its selected input and normalized health, not
physical sensor accuracy. The legacy `degradation` attribute remains the union
of problem and advisory reasons. The Source degraded entity is on only for
problem reasons, not every advisory or every optional writer fault.

`active_comfort_sensors` is bounded Ecobee active-sensor detail.
`configured_comfort_sensors` is a compatibility alias of that same data, not a
separately retrieved configured roster. Neither alias establishes historical
sensor participation. See [troubleshooting](troubleshooting.md) for recovery and
[architecture](architecture.md) for the exact Recorder and diagnostics boundary.
