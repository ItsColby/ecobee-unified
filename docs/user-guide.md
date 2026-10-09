# Operating Ecobee Unified

Unified works with entities you already have. Before configuring it, verify that
Home Assistant can read and control the thermostat through **HomeKit Device**
and can read its vendor information through **ecobee**. Pairing and account
access belong to those integrations; use their own setup or reauthentication
flows. See the official [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/)
and [ecobee](https://www.home-assistant.io/integrations/ecobee/) instructions.

## Bring a thermostat into the shared view

Install on Home Assistant Core 2026.9.4 or later. In HACS, add
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

Use the [installation acceptance checks](#verify-an-installation-before-moving-its-consumers)
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

Read preferences and fallback can combine the two range endpoints from different
sources. If the selected low target exceeds the selected high target, Unified
withholds both endpoints and reports `target_temperature_range_conflict`. Other
usable readings and controls remain available, and source provenance remains
visible. The range returns automatically when the selected values are ordered;
Unified does not switch sources to manufacture a valid pair.

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
per hour, from 0 to 60 in five-minute increments. **Equipment stage** follows
the selected current action and adds a reported stage when the cloud detail
agrees. Until the cloud's equipment detail confirms a stage (it typically lags
the local action by a few minutes), the sensor shows the plain action,
**Cooling** or **Heating**, and `detail_status` says why. Idle cannot simultaneously display a canonical cooling stage.
The separate `reported_equipment_stage` attribute preserves cloud detail,
including ambiguous reports; `detail_status` explains whether it agrees.
Report timestamps describe HA receipt, not exact equipment transitions.
This state is neither electrical metering nor a measurement of performance.

Mapped air-quality entities are opt-in. Selecting an AQI, CO2, or VOC source
creates its Unified entity disabled; enable it from the entity's settings when
a dashboard or automation needs it, and disable it again when nothing does.
Entities created by an earlier version keep their current setting. To remove
the entity and stop reading its source, clear the source under **Reconfigure >
Edit thermostat mapping** and save changes. Once enabled, they display the
source's values with their source measurement or estimate semantics. Unified
does not calculate a new AQI, infer CO2 from another quantity, or establish
independent sensor accuracy. Existing room, humidity, occupancy, and
Beestat history/context entities retain their own uses and owners.

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
replace, or remove mappings. Choose **Save changes** after staging the
edits to review the changes, then submit the review to save and reload. The review
shows additions, removals, and changed values for thermostat mappings and
multi-source datapoints, including source order and
selection policy. Leaving the flow before the final submit saves nothing.
Changes to either climate, Current Mode, Clear Hold, or the notification
writer require the confirmation checkbox. Removal also requires confirmation.
At least one mapping must remain in an entry.

Renaming an existing source preserves its registry reference. Deleting and
recreating a source can change that reference even if its visible entity ID is
the same. Missing selections are retained where possible, so repair or replace
them explicitly. The integration does not pick a similarly named substitute.
If another configuration session saves first, reopen the flow to work from its
saved state. Accepted changes reload this entry without a Home Assistant restart.

**Configure** opens two local timing settings and an optional **Configure
thermostat read preferences** choice. Select a thermostat to set HomeKit-first,
Ecobee-first, HomeKit-only or Ecobee-only reads separately for mode, current
action, display temperature, humidity, targets and fan mode. HomeKit-first is
the default. An only-source policy makes the field unavailable when that source
cannot provide it. These settings never move the command writer. The two timing
settings default to 1800 seconds:

| Setting | Allowed whole seconds | What changes |
|---|---|---|
| Ecobee stale after | 300–7200 in steps of 60 | The report age after which Ecobee climate and mapped air-quality readings become unusable to Unified |
| Command confirmation window | 300–1800 in steps of 30 | The wait for matching evidence after a confirmable source call succeeds |

The timing options do not set polling cadence, refresh a source, change a hold's
duration, or schedule another write. Choose them from observed reporting
behavior. HomeKit event silence is not a staleness timeout.

**Reload a silent precise temperature source** is off by default. When it is
on and a mapped precise temperature sensor stops answering its climate (see
[When a precise temperature stops being selected](#when-a-precise-temperature-stops-being-selected)),
Unified reloads that thermostat's HomeKit Device entry: at most once per six
hours, and never while a command is pending. Every entity of that HomeKit
pairing is briefly unavailable during the reload.

## Centralize other datapoints

Under **Reconfigure**, choose **Add multi-source datapoint**. Select the quantity, name,
output unit where applicable, and up to three ordered sources. An optional
attribute selects a public field from a source entity instead of its state.
The sources must belong to the same proven physical Ecobee thermostat or remote
sensor.
Confirm they represent the same quantity; similar values or names are
insufficient. Save the staged changes to reload the entry.

Keep measured humidity (`current_humidity`) and humidity targets (`humidity`)
in separate datapoints. Confirmation cannot override a known difference in
observation role. A saved mapping that mixes recognized contradictory roles
stays unavailable, including when a source is missing, unavailable or disabled
but its registry role remains known. Reordering or enabling fallback cannot
select through that contradiction. Existing history is retained unchanged;
create separate mappings for the observations you need.

Useful choices include paired room temperatures, humidity, occupancy, battery
readings, current comfort-profile identity and configured comfort membership.
Physical room temperature and thermostat control/display temperature are
different choices. A thermostat display may incorporate participating rooms.
Keep fast motion separate from held occupancy, and `in_use` separate from
configured profile membership. Battery Notes mirrors a battery measurement; it
does not independently confirm it. For current profile identity, use the
HomeKit Current Mode state, Ecobee `climate_mode`, or Beestat `profile_ref` where
their meanings match; Ecobee `preset_mode` can describe a hold/event instead.

Fallback follows the configured order and can be disabled. Missing, unknown,
unavailable, invalid or expired observations remain visibly qualified. A
source-age cutoff needs a reporting cadence or a deliberate silent-source guard;
leave it zero for healthy quiet event sources. A source-specific cutoff can
override the datapoint default. Neither setting refreshes an upstream source.

For **current physical temperature**, optional **Minimum accepted temperature**
and **Maximum accepted temperature** fields can exclude a source from selection
when its converted value is outside your chosen range. Enter limits in the
datapoint's output unit; either limit may be left blank, and both are disabled
by default. Endpoints are included. When both limits are set, the minimum must
be lower than the maximum. A source outside the range reports `out_of_range`;
with fallback enabled, the next eligible source supplies the reading. If none
qualifies, the output is unavailable. Values are never clipped or held.

Choose limits for the intended use: a real extreme temperature can also be
outside an accepted range. This policy does not prove a sensor is faulty,
calibrate it, or change raw readings and their history. Keep any required raw
diagnostic or safety consumers. Range checks do not apply to thermostat
control/display temperatures or interval observations. You can still
configure a proven source pair while one source is outside the range.

Editing these limits preserves the output identity and existing recorded data;
it affects future source selection. Clear both fields to disable the policy.
When changing an output unit with active limits, leave the displayed limit
numbers unchanged: the integration converts the saved limits to the new unit
at the same time. To change or remove the limits as well, do that in a separate
edit. This avoids silently interpreting an old limit in a different unit.

Interval observations require an observation timestamp attribute and interval
length. They retain their interval meaning and never replace current equipment
state. Minimum fan minutes per hour is a setting, with its own semantic choice,
not elapsed runtime. Configured-membership outputs retain the selected source's
exact labels in `members`, alongside the count and source; changing label
decoration does not prove a different physical membership.
Its `source_context` identifies the reported profile and timing basis. Beestat
room-spread membership uses its metadata-sync time; an unchanged current-profile
state cannot establish that the list is fresh. Missing metadata time remains
explicitly unknown and cannot satisfy a positive age cutoff.

Use **Edit multi-source datapoint** to change a datapoint while preserving its
meaning and stable output identity. Name, source priority, fallback, and freshness
changes retain that identity. Normal source entity renames retain their registry
references. Source reordering and compatible output-unit changes still require
equivalence confirmation; confirmation does not permit a change of meaning.
Replacing a typed source also requires proof that the old and new sources refer
to the same physical item and native observation role. Measured humidity and
target humidity are different roles. Matching units, values or device labels
cannot establish that continuity. When the native role cannot be proven, keep
the existing entity-and-attribute bindings or add a new datapoint.

| Change | Required action |
|---|---|
| Quantity type or meaning, such as physical versus control temperature or elapsed duration versus a minimum fan setting | Use **Add multi-source datapoint** to create a new identity |
| Physical item or observation role, such as measured versus target humidity | Use **Add multi-source datapoint** to create a new identity |
| Current versus fixed-interval observations, or the duration of a fixed interval | Use **Add multi-source datapoint** to create a new identity |
| Temperature output between °C, °F, and K, or duration output between s, min, h, and d | Edit the existing datapoint only when its meaning and time basis stay the same, then confirm equivalence |
| Any other output-unit change, including **Other numeric quantity** | Use **Add multi-source datapoint** to create a new identity |
| Source entity or value attribute for **Other numeric quantity** or **Text status** | Keep the same complete set of entity-and-attribute pairs when editing; pairs may be reordered. Adding, removing, or replacing a pair requires a new datapoint because these generic types do not establish a narrower meaning |

Choose **Remove multi-source datapoint** only when you intend to remove the
existing output. Editing or creating a datapoint does not automatically clear,
relabel, delete, backfill, or merge its historical statistics. History remains
with its existing owner. Before adopting a new output, review explicit
references and dynamic labels so an aggregate includes each physical probe once.

## Verify an installation before moving its consumers

For an installation being evaluated, compare Unified with the mapped sources
through their normal update cycles. Check temperature and humidity, held and
scheduled modes, equipment state, provenance, loss/recovery, and unexpected
Recorder or logbook churn. Exercise only the intended, authorized controls and
record what the source or thermostat actually did, including submission,
confirmation, and uncertainty. State which paths were not observed. There is no
fixed waiting period that substitutes for this coverage.

Move dashboard, automation, script, and voice consumers in bounded batches.
Inventory the references and preserve their previous configuration, update a
selected batch, then read back its references and observe each meaningful path.
Keep mapped sources enabled and available for recovery. Change routine exposure
only after consumer checks; removal or rollback must have a known consumer
target. Reusing an existing climate entity ID is a separate decision because it
can join histories with different semantics and affect rollback. Do not rewrite
Recorder history as a side effect of adopting the Unified surface.

Installation choices and live evidence belong to the installation owner. Keep
that evidence with its version and configuration; do not turn it into a general
promise about every installation or insert private details into product docs.

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
Reloading Unified or restarting Home Assistant clears the remembered rejection
and therefore cannot prove a repair. Unified does not control the thermostat or
revise history in response to this condition.

`homekit_temperature_silent` means something different: the sensor has stopped
updating. The rounded climate reading changed twice, the older change at least
a minute ago, while the sensor's value stayed the same. Both come from one
HomeKit characteristic, so a working sensor would have changed each time. A
frozen sensor can keep re-reporting its old value; those reports do not count. This
usually means Home Assistant's HomeKit entity stopped updating, not the
thermostat; refreshing the entity does not help, but reloading its HomeKit
Device entry does. Unified shows the rounded HomeKit reading meanwhile and
raises a Repair whose fix reloads that entry, or reloads it automatically when
**Reload a silent precise temperature source** is on. Reloading the HomeKit
entry does not clear a remembered rejection: the sensor still needs a different
agreeing value. Any new value from the sensor ends silence.

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
