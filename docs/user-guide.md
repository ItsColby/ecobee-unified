# Operating Ecobee Unified

Unified works with entities you already have. Before configuring it, verify that
Home Assistant can read and control the thermostat through **HomeKit Device**
and can read its vendor information through **ecobee**. Pairing and account
access belong to those integrations; use their own setup or reauthentication
flows. See the official [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/)
and [ecobee](https://www.home-assistant.io/integrations/ecobee/) instructions.

## Bring a thermostat into the shared view

Install on Home Assistant Core 2026.8.0 or later. In HACS, add
`https://github.com/ItsColby/ecobee-unified` as a custom repository of type
**Integration** and download it. The [HACS dashboard guide](https://hacs.xyz/docs/use/repositories/dashboard/)
explains custom repositories. Alternatively, copy `custom_components/ecobee_unified`
from a [release](https://github.com/ItsColby/ecobee-unified/releases) into your
Home Assistant configuration, preserving that directory structure. Load the
installed code with a Home Assistant restart before creating the entry.

1. Under **Settings > Devices & services**, choose **Add integration**, then
   **Ecobee Unified**. There is one entry for the entire set of thermostats.
2. Name the mapping and select its HomeKit and Ecobee climate entities. A name
   must contain 1–64 characters after trimming spaces and must be unique without
   regard to capitalization. Each climate can belong to only one mapping.
3. Select optional sources where useful, using the table below. Enable **Add
   another thermostat** if you need another mapping; otherwise finish setup.

The selected HomeKit device's serial and Ecobee device identifier must establish
that this is the same thermostat. Similar names, rooms, and readings cannot
substitute for that evidence. New mappings with missing or conflicting identity
are rejected. HomeKit Device uses the domain `homekit_controller`; HomeKit Bridge
does not supply these source entities.

| To add | Choose this optional source | Check before selecting |
|---|---|---|
| Home/Sleep/Away preset choices | HomeKit Current Mode select | It belongs to the selected HomeKit device and offers only supported choices |
| Resume program | HomeKit Clear Hold button | It is the actual Clear Hold action on that device |
| Finer current-temperature detail | HomeKit temperature sensor | Same HomeKit device, temperature class, and supported temperature unit |
| Thermostat display messages | Ecobee notification entity | Same Ecobee device as the selected climate |
| AQI | Ecobee AQI sensor | Same Ecobee device, AQI class, and no unit |
| Carbon dioxide | Ecobee CO2 sensor | Same Ecobee device, CO2 class, and ppm |
| Volatile organic compounds | Ecobee VOC sensor | Same Ecobee device, VOC class, and µg/m³ |

Each optional source can occupy only one field across all mappings. Leave a
field empty when you do not need the capability or its source is absent.

Clear Hold needs particular care: public registry metadata rejects known
incompatible buttons but cannot positively identify every Clear Hold action.
Verify the purpose of the source button without pressing an unfamiliar control.
Current Mode and Clear Hold are independent selections.

The new entities link to the existing HomeKit device. Unified leaves the
upstream config entries and their entities intact. It does not move Beestat
entities or merge the two source devices into one owned device.

## Use the thermostat

Select the **Unified climate** in a dashboard or automation when you want its
combined view and assigned controls. Home Assistant chooses its entity ID; use
the ID shown in your registry. Raw source entities must remain enabled because
Unified consumes them. Review dashboard, script, automation, and voice references
before changing which entities are visible to their users.

Use the contributor guide's [installation acceptance checks](development.md#verify-an-installation-before-moving-its-consumers)
when evaluating a new installation or moving existing consumers. They cover
source comparison, meaningful control paths, history, and recovery.

The available controls follow HomeKit's current capabilities: HVAC mode, a target
temperature or heat/cool range, fan mode, on/off, and target humidity where
supported. Presets come from the optional Current Mode select. Controls can
disappear while a fallback reading remains visible; this means the assigned
writer cannot currently supply that capability. Standard writes never move to
Ecobee to compensate.

For a single temperature target, use `temperature`. A heat/cool range uses
`target_temp_low` and `target_temp_high`. Enter values in the entity's displayed
temperature unit and within its bounds. A range's low value cannot exceed its
high value. Fan and preset choices must be advertised by the source. Humidity
requires a whole-number target within the reported bounds; Unified does not
invent a humidity increment.

For example, this selects cooling on an example Unified climate that advertises
the `cool` mode. Replace the entity ID before using it:

```yaml
action: climate.set_hvac_mode
target:
  entity_id: climate.example_thermostat
data:
  hvac_mode: cool
```

Alongside the climate, **Minimum fan runtime** sets Ecobee's minimum fan minutes
per hour, from 0 to 60 in five-minute increments. **Equipment stage** reduces
Ecobee equipment tokens to a bounded state such as idle, fan, or a heating or
cooling stage. An empty usable report means idle; absent data does not. Multiple
or unrecognized tokens can yield `multiple` or `unknown`. This state is neither
electrical metering nor a measurement of equipment performance.

Mapped air-quality entities display the source's values with their source
measurement or estimate semantics. Unified does not calculate a new AQI, infer
CO2 from another quantity, or establish independent sensor accuracy. Existing
room, humidity, occupancy, weather, and Beestat history/context entities retain
their own uses and owners.

## Make a deliberate vendor or schedule change

The five custom actions below target a **Unified climate**, which resolves the
mapped writer. Their native editor shows the complete field help. They expose
mechanisms for your choices; Unified does not decide household occupancy,
vacation dates, comfort policy, or when to send a notification.

| Task | Action | Required decisions |
|---|---|---|
| Clear a hold | `ecobee_unified.resume_program` | Requires the mapped HomeKit Clear Hold button; no extra fields. The Resume program button is another way to request the same action. |
| Define a vacation | `ecobee_unified.create_vacation` | `vacation_name`, `heat_temp`, and `cool_temp`; optional date/time endpoints and fan settings |
| Remove a vacation | `ecobee_unified.delete_vacation` | `vacation_name` |
| Change occupancy features | `ecobee_unified.set_occupancy_modes` | At least one of `auto_away` or `follow_me`, each a boolean; omitted fields are omitted from the request |
| Change comfort-profile participation | `ecobee_unified.set_sensors_used_in_climate` | `device_ids`, plus `preset_mode` when the current Ecobee climate mode is not the intended profile or cannot be read |

The four Ecobee actions use the mapped vendor climate. A vacation name is 1–12
characters; Unified does not check whether that name already exists. Vacation
temperatures use the Ecobee writer's unit and limits, with heat no higher than
cool. Supply a date and time together for each endpoint, using `YYYY-MM-DD` and
`HH:MM:SS`. If both endpoints are supplied, end must follow start. Omitted
endpoints and timezone interpretation remain with Ecobee: Unified performs no
timezone conversion. Vacation fan mode is `auto` or `on` (default `auto`), and
vacation minimum fan time accepts every whole minute from 0–60 (default 0).
That differs from the standalone number entity's five-minute increments.

Comfort-profile changes accept 1–32 distinct device IDs from the mapped
climate's Ecobee config entry. This membership check does not prove attachment
to the intended thermostat; choose its intended thermostat/sensor devices.
Omitting `preset_mode` uses the current Ecobee climate mode. A supplied name
must be nonblank and at most 64 characters. Home, Away, and Sleep are translated
to the source's built-in names; other names are forwarded.

To ask the mapped HomeKit button to clear a hold:

```yaml
action: ecobee_unified.resume_program
target:
  entity_id: climate.example_thermostat
```

For display messages, use `notify.send_message` with the **Unified notification
entity**, not the climate. Text must contain a non-whitespace character. Titles
are ignored, and a returned call does not prove that the display showed it:

```yaml
action: notify.send_message
target:
  entity_id: notify.example_thermostat_notification
data:
  message: "Please review the vacation settings."
```

### Decide whether an action needs follow-up

The climate's `command_confirmation` attribute contains the latest tracked
operation, revision, and status for that mapping. It is a single observation
record, not a command history or returned service-response object. A newer
tracked request supersedes the previous record, and reload starts a new record.

| Readout | What the integration knows |
|---|---|
| `none` | No command has been tracked by this running manager |
| `pending` | The source call or its qualifying observation remains outstanding |
| `confirmed` | The source call succeeded and the designated observer matched the requested state |
| `submitted` | The source call succeeded, but this action has no supported state confirmation |
| `failed` | The source call raised an error; a physical effect is not ruled out |
| `unconfirmed` | Observation timed out, or cancellation/unload interrupted tracking; the effect is uncertain |

Climate controls, preset selection, and minimum fan runtime have defined
observers. All five custom actions are submitted-only. In particular, a
successful Clear Hold call does not prove schedule resumption. Notifications
use the native notify result and never enter this tracker.

Inspect the source or thermostat before repeating an uncertain action. Unified
serializes each mapping's dispatches and never retries or changes writers on
its own. Stopping it cannot retract a source call already sent. For the exact
observer assignments and confirmation tolerances, see the [runtime contract](architecture.md).

## Adjust the composition or its waiting periods

Open the existing integration entry's **Reconfigure** action to add, rename,
replace, or remove mappings. Choose **Save mapping changes** after staging the
edits. Changes to either climate, Current Mode, Clear Hold, or the notification
writer require the confirmation checkbox. Removal also requires confirmation.
At least one mapping must remain in an entry.

Renaming an existing source preserves its registry reference. Deleting and
recreating a source can change that reference even if its visible entity ID is
the same. Missing selections are retained where possible, so repair or replace
them explicitly. The integration does not pick a similarly named substitute.
If another configuration session saves first, reopen the flow to work from its
saved state. Accepted changes reload this entry without a Home Assistant restart.

**Configure** opens two local timing settings. Both default to 1800 seconds:

| Setting | Allowed whole seconds | What changes |
|---|---|---|
| Ecobee stale after | 300–7200 in steps of 60 | The report age after which Ecobee climate and mapped air-quality readings become unusable to Unified |
| Command confirmation window | 300–1800 in steps of 30 | The wait for matching evidence after a confirmable source call succeeds |

These options do not set polling cadence, refresh a source, change a hold's
duration, or schedule another write. Choose them from observed reporting
behavior. HomeKit event silence is not a staleness timeout.

## Locate a problem before changing anything

Inspect the Unified climate's `selected_sources`, `source_health`,
`problem_reasons`, and `advisories`, then compare the affected mapped source.
The **Source degraded** binary sensor is on for normalized problem reasons;
an advisory alone does not activate it. Clear Hold and notify availability also
need checking on their own entities or in diagnostics. Their faults are not a
complete part of that problem sensor's coverage.

| Observation | Next check |
|---|---|
| Pairing rejected or a mapping Repair appears | Verify backend, device association, identity, and optional role. Restore the intended source or use Reconfigure. Never edit `.storage` to repair a mapping. |
| Standard controls vanish but readings remain | Inspect the HomeKit climate and advertised capabilities; permitted read fallback does not supply an alternate writer |
| Only preset or Resume program fails | Check the mapped Current Mode select or Clear Hold button respectively; they do not stand in for each other |
| Current Mode is `unknown` | A select with valid options can remain writable; its unreadable current option is an advisory |
| Vendor data becomes stale | Check Ecobee reporting/authentication. A report's age is not proof of a new physical measurement; unchanged values can still be freshly reported. |
| All Ecobee contributions are blocked | Check that source identity still proves the two climates are one thermostat. Eligible HomeKit behavior survives identity loss. |
| An optional sensor is rejected after working | Inspect live class/unit metadata and same-device association. Explicit invalid metadata is not replaced with a registry default. |
| Temperature loses decimal detail | Inspect the two HomeKit temperature projections and the recovery explanation below |

`missing`, `unavailable`, `unknown`, and `stale` have different meanings: an
unresolved reference, an unavailable source state, an unknown source state, or
an otherwise usable Ecobee source beyond its age limit. A normal source or
registry recovery reevaluates affected values and controls. Authentication
repairs belong to the source integration.

### When a precise temperature stops being selected

The optional sensor must provide a valid convertible reading that agrees with
the HomeKit climate's serialized value. Routine paired updates get 250 ms to
settle. When agreement fails, Unified can show the eligible HomeKit climate
reading, then an eligible Ecobee fallback. It neither averages readings nor
selects whichever arrived last.

A confirmed disagreement leaves a remembered rejected value.
`homekit_temperature_recovery_pending` means the sensor needs a different finite
value that agrees with the climate before its detail can return. A fresh
timestamp, unit conversion, rename, or availability cycle does not meet that
condition. The climate moving toward the unchanged rejected sensor does not
establish recovery either.

This check can detect inconsistency, not physical accuracy. A faulty comparator
can reject a good sensor; two agreeing wrong readings can remain eligible.
Reload/restart clears the remembered rejection and therefore cannot prove a
repair. Unified does not reload the sources, control the thermostat, or revise
history in response to this condition.

### Collect a useful problem report

Download diagnostics from the integration entry. The export contains anonymous
mapping positions, capabilities, selected source roles, report ages, reason
codes, and a command summary. It excludes source IDs, mapping and sensor names,
measurements, command/message payloads, credentials, and raw backend errors.
Live entity attributes and screenshots have a different boundary and may show
comfort-sensor names. `configured_comfort_sensors` is a compatibility alias of
`active_comfort_sensors`, not evidence of a separately retrieved configured roster.

Include the Core and Unified versions, relevant capability/reason codes, exact
reproduction steps, and what you observed at the source or thermostat. State
whether a write was tested; a read-only comparison cannot establish delivery.
Review attachments before posting to the [issue tracker](https://github.com/ItsColby/ecobee-unified/issues).

## Stop using Unified

Repoint consumers to the entities you intend to keep. Remove a mapping through
Reconfigure while other mappings remain; to remove the last one, remove the
integration entry. Source integrations stay configured. Removing Unified does
not undo thermostat settings changed through previous actions, uninstall
Beestat, or rewrite Recorder history.
