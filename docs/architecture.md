# Runtime architecture

Ecobee Unified presents a mapped thermostat through one Home Assistant climate entity and a small set of related entities. It consumes the states and services of installed HomeKit Device (`homekit_controller`) and Ecobee integrations. It does not acquire thermostat data directly or take over either integration's connection. The central contract is that **the source selected for a displayed value, the source allowed to write a control, and the source allowed to confirm that write are separate decisions**.

This reference describes those decisions and their limits. The invariants below are enforced within Unified's runtime; they are not guarantees about network delivery, upstream retries, thermostat firmware, or the causal origin of a source report. The runtime boundary is visible in [the manager](../custom_components/ecobee_unified/manager.py) and exercised through [native Home Assistant integration tests](../tests/test_integration_ha.py).

## Follow one mapping through the runtime

A config entry contains one or more explicit `MappingConfig` records. Each has an independent generated `mapping_id`, a display name, two required climate references, and any selected optional sources. One `MappingManager` owns all mappings in that entry; every mapping has its own snapshot, command lock, current command revision, deadlines, and temperature-recovery evidence. Entity properties read the snapshot rather than resolving sources or calling services. This keeps every projection on the same normalization rules without making property reads perform I/O. [Models](../custom_components/ecobee_unified/models.py), [runtime container](../custom_components/ecobee_unified/runtime.py), [entity projection test](../tests/test_runtime_core_api.py).

Keeping the mappings in one hub entry makes collection-wide duplicate checks
and reconfiguration one transaction. Stable mapping IDs already distinguish the
entities, so the design does not add a separate subentry lifecycle and migration
for each thermostat. The flow saves only if the original entry data is still
current. [Manifest](../custom_components/ecobee_unified/manifest.json),
[collection save](../custom_components/ecobee_unified/config_flow.py).

The manifest declares neither source `dependencies` nor `after_dependencies`.
HomeKit and Ecobee load independently; Unified can retain unresolved saved
references and respond when their states or registry entries return. This keeps
source setup and recovery with their own integrations instead of coupling them
to Unified's entry lifecycle. [Late-source setup and recovery tests](../tests/test_runtime_core_api.py).

```text
Config entry + entity/device registries
                 |
Mapped HA state changes, selected state reports, registry events, deadlines
                 |
       MappingManager: resolve, check association, convert
                 |
       build_snapshot: select fields and record provenance
                 |
       immutable NormalizedSnapshot for this mapping
                 |
       climate / sensor / number / button / notify / problem entity

Entity action -> validate -> mapping command lock -> one mapped HA service call
                                                   |
                           operation-owned report -> command revision state
```

The implementation responsibilities are deliberately small:

| Module | Contract it owns |
| --- | --- |
| [`config_flow.py`](../custom_components/ecobee_unified/config_flow.py) and [`source_contracts.py`](../custom_components/ecobee_unified/source_contracts.py) | Admit explicit mappings; validate registry identity, optional roles, and sensor semantics. |
| [`manager.py`](../custom_components/ecobee_unified/manager.py) | Subscribe to relevant events, resolve references, build eligible inputs, convert precise temperature, manage deadlines and Repairs, and dispatch services. |
| [`models.py`](../custom_components/ecobee_unified/models.py) | Pure normalization, field selection, provenance, degradation, and confirmation comparison. |
| [`temperature_quality.py`](../custom_components/ecobee_unified/temperature_quality.py) | Retain rejected temperature evidence for the same source association until recovery. |
| [`commands.py`](../custom_components/ecobee_unified/commands.py) | Track the latest command revision and bounded result for each mapping. |
| [`entity.py`](../custom_components/ecobee_unified/entity.py) and platform modules | Project the cached snapshot and expose validated action entry points. |
| [`__init__.py`](../custom_components/ecobee_unified/__init__.py) and [`diagnostics.py`](../custom_components/ecobee_unified/diagnostics.py) | Own entry lifecycle and the allow-listed diagnostic export respectively. |

## Establish identity before combining sources

The configuration flow stores entity-registry IDs, resolving them to current entity IDs when reading or dispatching. It rejects an entity from the wrong integration or domain, duplicate source selections, repeated optional sources, and mapping names equal after trimming and case folding. Renaming an entity therefore does not change the mapping or its public entity identity. There is no name-based pairing or automatic substitution. [Mapping validation](../custom_components/ecobee_unified/config_flow.py), [rename and source-role tests](../tests/test_homekit_action_roles.py).

For a new or changed climate pairing, physical identity must be proved from the HomeKit device's normalized serial number and exactly one normalized identifier in the Ecobee device's `ecobee` domain. The comparison trims whitespace and ignores case. Missing or ambiguous evidence is `unproven`; unequal identities are `mismatch`. A similar label, an entity association alone, or a guessed serial is insufficient. The manager repeats this proof when rebuilding a snapshot. Loss of proof excludes Ecobee fallback, vendor context, cloud sensor fusion, notification eligibility, and vendor writes; usable HomeKit state and local controls can continue. [Identity contract](../custom_components/ecobee_unified/source_contracts.py), [runtime enforcement](../custom_components/ecobee_unified/manager.py), [identity drift tests](../tests/test_runtime_core_api.py).

Optional selections add only the capability they can establish:

| Optional source | Required association and semantics |
| --- | --- |
| HomeKit Current Mode select | Same HomeKit device; an uncategorized, classless `select` with one to three distinct case-insensitive options drawn from `home`, `sleep`, and `away`. Its translation key must be absent or `ecobee_mode`. The advertised options, rather than the current value, establish the role. |
| HomeKit Clear Hold button | Same HomeKit device; an uncategorized, classless `button` with no translation key. These checks reject known incompatible roles such as Identify, but public metadata cannot positively prove that every otherwise eligible button clears a hold. Correct explicit selection remains necessary. |
| HomeKit precise temperature sensor | Same HomeKit device; temperature device class and a recognized temperature unit. A present readable state must be numeric. Runtime conversion and comparison against the climate add further checks. |
| Ecobee AQI, CO2, VOC sensors | Same Ecobee device. Native sensor classes are `AQI` with no unit, `CO2` with `ppm`, and `VOLATILE_ORGANIC_COMPOUNDS` with `µg/m³`. Readable values must be finite and nonnegative. |
| Ecobee notify entity | An explicitly selected `notify` entity from Ecobee on the mapped Ecobee device. |

For sensor metadata, an explicitly supplied live class or unit wins over registry defaults, including invalid null or empty values. Registry metadata fills absent keys only. Missing, `unknown`, or `unavailable` state can retain a valid metadata contract; it does not become a usable measurement. In particular, a VOC concentration in another quantity or unit is not relabeled as `µg/m³`. [Source contracts](../custom_components/ecobee_unified/source_contracts.py), [configuration contract tests](../tests/test_configuration_source_contracts.py), [HomeKit role tests](../tests/test_homekit_action_roles.py), [numeric and VOC tests](../tests/test_numeric_validity.py).

## Determine what a published value means

### Select each field independently

A `RawSource` is usable only when its effective health is `healthy` and it has a state other than `unknown` or `unavailable`. A field can still be invalid inside a usable source. The normalizer selects the first valid value allowed by the field's rule; it does not choose one winning integration for the entire thermostat. Thus a snapshot can contain HomeKit HVAC mode and an Ecobee fallback target, with a separate provenance entry for each. Disagreement between valid ordinary readings does not trigger averaging, voting, or selection by precision. [Selection and normalization](../custom_components/ecobee_unified/models.py), [field ownership tests](../tests/test_models.py).

| Published semantic | Selection rule |
| --- | --- |
| HVAC mode | Valid HomeKit climate state, then valid Ecobee climate state. |
| HVAC action, current humidity, target temperature, low/high targets, fan mode | Valid HomeKit attribute, then the corresponding Ecobee attribute independently. Targets map to `temperature`, `target_temp_low`, and `target_temp_high`; no target is reconstructed from the HVAC mode. |
| Current temperature | Eligible, agreeing explicit HomeKit temperature sensor; otherwise HomeKit climate `current_temperature`; otherwise Ecobee climate `current_temperature`. The precise path is detailed below. |
| HVAC/fan choices and ordinary feature bits | Usable HomeKit climate metadata only. Ecobee read fallback does not supply HomeKit control authority. |
| Temperature unit and setpoint bounds | Valid, ordered HomeKit climate bounds in its serialized unit. Ecobee bounds are retained separately for Ecobee vacation validation. |
| Target-temperature step | Valid positive HomeKit step; only when omitted, the narrowly permitted same-device Ecobee metadata fusion described below. |
| Target humidity and its bounds | HomeKit only, with the target-humidity feature and valid ordered percentage bounds. |
| Unified preset and choices | The explicitly mapped HomeKit select only. Ecobee `preset_mode` remains separate vendor context. |
| Vendor preset, climate mode, active comfort sensors, minimum fan runtime, equipment report | Usable Ecobee climate attributes only. Active sensors use `active_sensors`, falling back to `active_comfort_sensors` when the former is empty. |
| AQI, CO2, VOC | Their own selected, eligible Ecobee sensors; one sensor's absence does not remove another sensor's value. |

The climate is available exactly when a valid HVAC mode and current temperature can be projected. Other fields can be missing while it remains available. Conversely, a readable climate can expose no ordinary controls because its current values came from fallback. [Snapshot construction](../custom_components/ecobee_unified/models.py), [fallback and capability tests](../tests/test_models.py).

### Preserve units, reject impossible shapes, and bound derived meaning

Home Assistant serializes climate temperatures in its configured temperature unit. The manager supplies that unit to both climate inputs; it does not interpret their numbers as accessory-native temperatures. An explicit precise sensor is converted from its validated sensor unit with Home Assistant's `TemperatureConverter`, then validated again in the climate unit. Conversion does not mutate the source state. Climate display precision is tenths when the selected current-temperature provenance is the precise sensor or Ecobee, preserving fractional readings through climate serialization. [Conversion](../custom_components/ecobee_unified/manager.py), [projection precision](../custom_components/ecobee_unified/climate.py), [serialization and configured-unit tests](../tests/test_runtime_core_api.py).

Numbers must be finite; booleans, overflow, and unsupported shapes do not count as numbers. Text parsing is allowed for sensor state strings, not ordinary climate numeric attributes. Humidity is bounded to 0–100 percent. Observed fan minimum must be an integer from 0–60 minutes. Temperatures are checked against absolute zero in a known unit, with climate serialization allowances: approximately 0.05 °C or 0.5 °F for HomeKit, and 0.05 degrees in either climate unit for Ecobee. Precise measurements receive only floating-point tolerance. These are physical and representation checks, not comfort limits: an extreme but possible current temperature remains an observation, even outside the thermostat's writable setpoint range. [Numeric normalizers](../custom_components/ecobee_unified/models.py), [numeric boundary tests](../tests/test_numeric_validity.py).

The only cross-integration capability fusion is the omitted target step. It requires proven physical identity, the manager's explicit HomeKit writer-granularity compatibility assumption, matching units, and a positive Ecobee step no greater than the HomeKit bound span. Stable step metadata may come from a stale Ecobee input, but not an unavailable one. A present invalid HomeKit step is rejected rather than replaced. Missing step does not itself remove temperature control; missing or invalid writer bounds/unit removes the affected temperature feature bits. This exception fills a known adapter metadata gap without importing cloud write capability. [Step selection](../custom_components/ecobee_unified/models.py), [compatibility assumption](../custom_components/ecobee_unified/manager.py), [fusion tests](../tests/test_models.py), [invalid-step tests](../tests/test_numeric_validity.py).

Equipment stage is a bounded interpretation of the Ecobee equipment report, not a measurement of output, power, or efficiency. The normalizer splits comma-separated tokens, trims and lowercases them, and maps recognized tokens to cooling stages 1–2, heat-pump or auxiliary stages 1–3, fan, humidifying, dehumidifying, or ventilating. A fan accompanying another recognized stage is subordinate. Multiple remaining stages, or a mixture of recognized and unknown tokens, produce `multiple`; only unknown tokens produce `unknown`. An empty report means `idle`; a missing report means unavailable. [Equipment calculation](../custom_components/ecobee_unified/sensor.py), [enum tests](../tests/test_sensor.py).

Vendor text is truncated to 64 characters, and text lists retain at most eight distinct nonempty items. `active_comfort_sensors` and `configured_comfort_sensors` on the climate currently expose the same bounded source list; they do not prove present occupancy, actual participation, an average-temperature calculation, or distinct configured-versus-active sets. [Bounded projections](../custom_components/ecobee_unified/models.py), [climate attributes](../custom_components/ecobee_unified/climate.py).

## Separate loss of data from loss of a capability

Source health has five states: `healthy`, `stale`, `unknown`, `unavailable`, and `missing`. A missing registry reference or required association is `missing`; a disabled entry or absent live state is unavailable. Literal `unknown` remains distinct. Contract failures can exclude an input as unavailable even when a raw state exists. Malformed numeric fields instead retain their source health and receive field-specific invalidity reasons. This distinction prevents an impossible reading from being published without inventing a transport outage. [Input construction](../custom_components/ecobee_unified/manager.py), [per-field invalidity](../custom_components/ecobee_unified/models.py), [fault tests](../tests/test_numeric_validity.py).

Ecobee climate and air-quality inputs use `last_reported` age, defaulting to a stale threshold of 1,800 seconds. Age must exceed the threshold to become stale. A deadline checks the next possible transition; ordinary unchanged reports need not republish the thermostat. Cloud report subscriptions allow unchanged reports to recover stale inputs and confirm pending commands. HomeKit climate, preset, and precise temperature have no age-based expiry: a quiet event-driven source is not declared stale solely because it has not changed. This does not establish that an undetected frozen device is healthy. [Cadence and subscriptions](../custom_components/ecobee_unified/manager.py), [quiet-source and report tests](../tests/test_runtime_core_api.py).

An unavailable HomeKit climate removes ordinary HomeKit-backed controls. A stale or unavailable Ecobee climate removes usable fallback and vendor context. Local preset and Clear Hold availability are evaluated independently from the climate's live value; a mapped action can remain writable when its state is `unknown`, provided the registry role, association, and non-unavailable state remain valid. A valid preset select with advertised choices and an unknown current selection produces the advisory `homekit_preset_unknown`; it becomes a problem if those conditions no longer hold. [Capability projection](../custom_components/ecobee_unified/climate.py), [action availability](../custom_components/ecobee_unified/manager.py), [advisory classification](../custom_components/ecobee_unified/models.py), [independent preset tests](../tests/test_runtime_core_api.py).

The source-degraded binary sensor reports whether any nonadvisory degradation reason exists. It describes the mapping's data and capability condition, not command success. Structural defects such as removed or disabled references, lost device links, identity mismatch, or invalid optional roles create a nonpersistent mapping Repair. Temporary unreadability and semantic degradation also remain visible in snapshots; Repairs are not a duplicate of every runtime reason. State and relevant registry events re-evaluate the affected contracts and remove the Repair once those structural checks pass. [Problem entity](../custom_components/ecobee_unified/binary_sensor.py), [Repair checks](../custom_components/ecobee_unified/manager.py), [registry recovery tests](../tests/test_runtime_core_api.py).

### Precise temperature needs local evidence to recover

The optional precise sensor refines the HomeKit climate reading only when both are usable and their temperatures agree within the climate serialization envelope: `0.050001` °C or `0.500001` °F. A valid precise number without a usable local climate comparison is `unverifiable`; disagreement is `diverged`. Either condition selects the normal climate fallback chain. A cloud reading never votes the precise sensor back into use. [Agreement and selection](../custom_components/ecobee_unified/models.py), [precision boundary tests](../tests/test_models.py).

A thermostat can deliver its rounded climate reading and precise characteristic as separate events. Routine healthy updates in this pair settle for 0.25 seconds before publication, coalescing the pair for that mapping. Availability changes, relevant command observations, and registry changes are handled immediately. A mismatch first encountered through another refresh receives its own bounded confirmation deadline. A new pair cancels the older candidate; repeated unrelated refreshes do not convert a transient mismatch into confirmed evidence. This is transport settling, not a freshness policy. [Pair and mismatch deadlines](../custom_components/ecobee_unified/manager.py), [paired-event tests](../tests/test_runtime_core_api.py), [unrelated-refresh tests](../tests/test_runtime_core_api.py).

Once disagreement is confirmed after settling, recovery retains the rejected precise value and the actual climate/sensor registry-device association. It records the temperature in canonical Celsius and ignores only conversion noise within `1e-9`. That same source must report a **different** precise value that agrees with the local climate before precision can return. The old rejected value remains excluded even if the rounded climate later moves close enough to it. Unavailability, unknown comparison, rename, elapsed time, and repeated refreshes do not erase the evidence. Another confirmed divergent value replaces it. A real source-association replacement starts fresh; the evidence is mapping-local and in memory, so stopping the manager clears it. [Recovery state](../custom_components/ecobee_unified/temperature_quality.py), [manager association](../custom_components/ecobee_unified/manager.py), [recovery tests](../tests/test_temperature_quality.py), [freeze and rename tests](../tests/test_runtime_core_api.py).

During this exclusion, `homekit_temperature_recovery_pending` explains why an otherwise healthy precise source is not selected. The climate can continue using HomeKit's rounded reading or valid Ecobee fallback. The rule prevents previously demonstrated contrary evidence from disappearing merely because a value becomes plausible again; it does not detect every possible sensor freeze. [Recovery-pending projection](../custom_components/ecobee_unified/models.py), [fallback test](../tests/test_models.py).

## Interpret a command as a bounded attempt

### Route by operation, then observe the designated source

All writes enter through the manager. Ordinary climate methods validate HomeKit-advertised capabilities and choices; setpoints must be finite and within HomeKit bounds, a supplied low/high pair must be ordered, and target humidity must be an integer within the writer's valid bounds. They do not synthesize unsupported controls. Service data cannot override the mapped writer: the manager inserts the resolved target after the caller's data. [Climate validation](../custom_components/ecobee_unified/climate.py), [target enforcement](../custom_components/ecobee_unified/manager.py), [writer and capability tests](../tests/test_runtime_core_api.py).

| Unified operation | Single writer call | Eligible confirming observation |
| --- | --- | --- |
| HVAC mode, turn on/off, temperature or range, fan mode | Corresponding `climate` service on the mapped HomeKit climate | Mapped Ecobee climate report: requested mode, target fields, or fan mode. Turn on requires a valid non-off mode; turn off requires `off`. |
| Target humidity | `climate.set_humidity` on the mapped HomeKit climate | HomeKit climate target `humidity`. |
| Preset | `select.select_option` on the mapped HomeKit Current Mode select | That same select's reported option. |
| Resume program, including the Unified button | `button.press` on the mapped HomeKit Clear Hold button | None; successful service return is `submitted`. |
| Minimum fan runtime | `ecobee.set_fan_min_on_time` targeting the mapped Ecobee climate | Ecobee climate `fan_min_on_time`. |
| Create/delete vacation, occupancy modes, sensors used in climate | Corresponding `ecobee` service targeting the mapped Ecobee climate | None; these effects have no adequate confirmation field in the supported projection. Successful service return is `submitted`. |
| Thermostat notification | `notify.send_message` targeting the mapped Ecobee notify entity | None; notification is outside the command tracker and leaves its previous summary unchanged. |

The observer selection and normalized comparison are explicit; a HomeKit read projection cannot stand in for an Ecobee observation, and cloud preset context cannot confirm the local select. Source events from the wrong observer cannot confirm the current operation. [Observer routing](../custom_components/ecobee_unified/manager.py), [confirmation values](../custom_components/ecobee_unified/models.py), [command tests](../tests/test_commands.py), [local observer tests](../tests/test_command_lifecycle.py).

The vendor facade preserves narrow input contracts. Minimum fan runtime writes accept 0–60 minutes in steps of five, although observed values may be any integer in that range. Vacation names are trimmed and limited to 12 characters; heat/cool values use Ecobee writer bounds and must be ordered. Dates use `YYYY-MM-DD`, times `HH:MM:SS`, each endpoint requires both, and a fully specified end must follow its start. Vacation fan minimum permits integer minutes from 0–60. Occupancy updates require at least one boolean. Sensor participation requires 1–32 distinct device IDs owned by the mapped Ecobee config entry, with Ecobee identifiers; the profile is explicit or taken from usable Ecobee climate mode, with built-in names normalized to `Home`, `Away`, or `Sleep`. Vendor dispatch rechecks physical identity, live source eligibility, and registered service availability. [Vendor validation](../custom_components/ecobee_unified/climate.py), [writer guard](../custom_components/ecobee_unified/manager.py), [vendor action tests](../tests/test_runtime_core_api.py).

Notifications require a nonblank message, eligible explicit notify mapping, and the native notify service. They forward the message only; the platform accepts but does not forward a title. Their shared command lock gives them the same admission and stop behavior as other writes, without pretending the thermostat display provides a confirmable state. [Notify projection](../custom_components/ecobee_unified/notify.py), [notify dispatch](../custom_components/ecobee_unified/manager.py), [notification tests](../tests/test_runtime_core_api.py).

### Distinguish dispatch from confirmation

A per-mapping FIFO lock serializes service dispatch in admitted order. It is released after the source call finishes; it does not wait for state confirmation. The next tracked command can therefore supersede a still-pending confirmation. Different mappings have independent locks. A new revision replaces the previous tracked command and its timeout; this is a latest-command summary, not a durable command history. [Admission and locking](../custom_components/ecobee_unified/manager.py), [revision tracker](../custom_components/ecobee_unified/commands.py), [ordering test](../tests/test_runtime_core_api.py).

| Status | Evidence represented |
| --- | --- |
| `none` | No tracked command exists in this manager for the mapping. |
| `pending` | A revision has begun. Its writer may still be running, or may have returned successfully while an eligible matching observation is still awaited. |
| `confirmed` | The writer returned successfully and the current revision received an eligible matching observation. |
| `submitted` | An operation without a supported confirmation predicate returned successfully from its writer. |
| `unconfirmed` | The observation deadline expired, a tracked writer await was cancelled, or the manager stopped while that revision was pending. The effect remains uncertain. |
| `failed` | The tracked source call raised an exception. Unified reports a bounded translated error; the label does not prove that no partial external effect occurred. |

A matching report arriving during the awaited writer call is remembered but cannot confirm until that call succeeds. A cached value that already matches when the command begins is not itself a new confirmation event. The comparison requires all expected fields; numeric comparisons use absolute tolerance `0.11`, except temperature targets with a validated target step use half that step plus `1e-9`. Invalid or nonfinite values cannot confirm. Revision guards prevent an old callback from changing a newer tracked result. These checks establish a matching observation in the command interval, not proof that the command caused it or that the thermostat will retain it. [Tracker transitions](../custom_components/ecobee_unified/commands.py), [comparison](../custom_components/ecobee_unified/models.py), [acceptance tests](../tests/test_commands.py), [temperature comparison tests](../tests/test_models.py).

The confirmation timer starts after successful writer return when the command is still pending. It defaults to 1,800 seconds and is not a timeout around the source service call. Command age instead starts at revision creation and uses a monotonic clock. Timing out confirmation produces `unconfirmed`; it never retries, switches writers, or sends a rollback. [Tracked dispatch and deadlines](../custom_components/ecobee_unified/manager.py), [timeout test](../tests/test_runtime_core_api.py).

“One call” means one Unified invocation of one mapped Home Assistant source service for an admitted action; validation or stopped admission can result in zero calls. It is not a device-level exactly-once guarantee, a global lock across integrations, deduplication of repeated user calls, or a restriction on work the source integration performs internally. Cancellation while queued causes no dispatch. Cancellation during a tracked writer await preserves uncertainty and releases the lock without retry. Stop closes admission, rejects queued callers, cancels subscriptions and deadlines, and prevents late publication. It deliberately does not wait for or cancel an already-dispatched source effect; the caller may still receive that source call's eventual success or error. [Stop contract](../custom_components/ecobee_unified/manager.py), [command lifecycle tests](../tests/test_command_lifecycle.py).

## Keep configuration and entity identity stable

The native flow owns one config entry with a nonempty mapping collection. Reconfigure stages adds, edits, and removals, then saves the collection on Finish only if the entry data still matches the original snapshot. Writer-reference changes and mapping removals require explicit confirmation; observation-only edits and name changes do not change the mapping ID. The last mapping cannot be removed through this flow. Options independently update only cloud staleness and confirmation timing, with their own stale-edit guard: cloud age is 300–7,200 seconds in 60-second steps, confirmation is 300–1,800 seconds in 30-second steps. Changed accepted configuration reloads through native config-entry APIs. [Flows](../custom_components/ecobee_unified/config_flow.py), [reconfigure and option tests](../tests/test_runtime_core_api.py).

Editing can preserve an unchanged saved reference that is temporarily missing, and can retain an unchanged climate pairing whose identity is temporarily unproven. That allowance preserves intent; it does not validate a new source or restore runtime eligibility. An absent parent cannot establish the association of a newly selected optional source, and an explicit identity mismatch remains invalid. Accepted unknown data is preserved rather than silently erased. [Preservation boundaries](../custom_components/ecobee_unified/config_flow.py), [mapping merge](../custom_components/ecobee_unified/models.py), [missing-parent tests](../tests/test_configuration_source_contracts.py).

Schema migration accepts major version 1 through minor version 3, normalizes existing mappings without inventing identity, and removes named retired mapping fields and timing options. It preserves other entry data and options, and fails closed for unsupported future versions or an empty mapping collection. [Migration](../custom_components/ecobee_unified/__init__.py), [schema tests](../tests/test_runtime_core_api.py).

Setup installs the typed runtime, removes only this entry's Unified registry entities no longer declared by the mapping collection, starts subscriptions and snapshots, then forwards enabled platforms. A setup exception or cancellation stops the manager. Unload first asks Home Assistant to unload platforms; only success stops the manager. Each subsequent setup creates a new manager, so command history and temperature-recovery evidence do not persist across reload. Registry listeners rebuild relevant subscriptions, recheck identity, and relink Unified entities when the HomeKit source device association changes. They do not create a replacement physical device. [Entry lifecycle and owned cleanup](../custom_components/ecobee_unified/__init__.py), [device relinking](../custom_components/ecobee_unified/manager.py), [setup tests](../tests/test_setup_lifecycle.py), [device identity tests](../tests/test_source_device_identity.py).

## Expose a small, inspectable footprint

Each mapping declares a climate, minimum-fan-runtime number, equipment-stage sensor, and source-degraded problem sensor. AQI, CO2, VOC, Resume program button, and thermostat notify entities exist only when their sources are explicitly mapped. Preset support lives on the climate rather than adding another Unified select. The climate unique ID is the stable mapping ID; siblings append fixed suffixes. All link to the existing HomeKit-owned thermostat device and use native translated entity names. Cloud sensors are available when their own projected value exists; the number additionally requires usable Ecobee writer state, and action entities expose their own writer eligibility. [Entity identity](../custom_components/ecobee_unified/entity.py), [sensor inventory](../custom_components/ecobee_unified/sensor.py), [platform lifecycle](../custom_components/ecobee_unified/__init__.py), [entity tests](../tests/test_integration_ha.py).

The climate publishes bounded source health, selected-source names, degradation, vendor context, comfort-sensor lists, and a command summary containing only revision, operation, and status. It omits elapsed ages from entity attributes so the passage of time alone need not churn recorded state. Comfort-sensor lists, command confirmation, problem reasons, and advisories are marked unrecorded on the climate; the problem entity also marks its detail attributes unrecorded. Equipment stage is an enum, while AQI, CO2, and VOC are native measurement sensors with declared quantities and units. These choices bound state shape and preserve useful native history without exporting every raw attribute. They do not remove data held by the source integrations or make all Unified state private. [Climate Recorder contract](../custom_components/ecobee_unified/climate.py), [problem Recorder contract](../custom_components/ecobee_unified/binary_sensor.py), [age stability test](../tests/test_runtime_core_api.py).

Diagnostic export has a separate allow list. It labels mappings as `mapping_1`, `mapping_2`, and so on and includes schema versions, availability, capability predicates, source health and current ages, field provenance, degradation, and bounded command metadata. It omits mapping names and IDs, source entity/device identifiers, raw states and attributes, command payloads, and notification text. Source and command ages are recomputed when diagnostics are requested without publishing a new entity state. Source-service exceptions are translated without propagating arbitrary upstream error text. [Diagnostic export](../custom_components/ecobee_unified/diagnostics.py), [request-time ages](../custom_components/ecobee_unified/manager.py), [privacy tests](../tests/test_runtime_core_api.py), [safe service errors](../tests/test_runtime_core_api.py).
