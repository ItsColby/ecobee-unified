# Validation Plan

## Automated Matrix

### Configuration and Lifecycle

- create, abort, duplicate source/name, edit, remove, reload, unload;
- hub manifest classification keeps entry management on the Integrations
  dashboard, and every reconfigure menu option has a nonblank runtime English
  translation;
- multiple mappings in one entry;
- invalid domain/integration and circular mapping rejection;
- entity rename, device rename, source removal/re-add, and registry disable;
- source device move, detach, removal, and restoration with helper relinking
  for climate/number/sensor records without config-entry reload or stable-entity recreation;
- unrelated entity/device registry events do not refresh mappings, while owned
  helper registry reconciliation remains bounded and self-correcting;
- removing a mapping or optional projection deletes only that config entry's
  orphaned Unified entities and preserves retained stable IDs;
- startup before each source integration and later source setup/reload;
- config-entry version migration and rollback fixtures.
- options/mapping changes that preserve temporarily missing entity selections;
- explicit confirmation for physical-device or command-writer changes.
- concurrent reconfigure sessions and external config-entry updates fail closed
  at completion, preserve the winning mapping collection, and do not schedule a
  reload from the stale flow;
- successful reconfigure replaces mappings within the accepted complete entry
  data while preserving additive or unrecognized fields;
- matched, mismatched, and missing HomeKit-serial/Ecobee-identifier proof at
  create and reconfigure time, plus runtime identity drift/recovery without
  recreating the config entry;

### Field Selection

For every standard and vendor field, test:

- primary and fallback both available;
- primary unavailable, fallback valid;
- primary unknown/unavailable with stale fallback;
- source field absent or malformed;
- both unavailable;
- unequal values that prove no averaging/freshest-wins behavior;
- honest primary precision, writer-owned temperature units/bounds, explicit
  same-device step fusion, Celsius/Fahrenheit climate-unit enforcement, and
  rejection when proof/unit reconciliation is absent;
- explicit same-device HomeKit temperature selection, unit conversion,
  malformed/non-finite rejection, Fahrenheit/Celsius serialization-envelope
  agreement and stable boundaries, explicit divergence/unverifiable
  degradation, climate fallback, cloud fallback, quiet-source health,
  source-dependent climate-state precision/rounding, rename, move/detach,
  disappearance, and recovery;
- per-mapping trailing-edge coalescing of sequential healthy HomeKit climate
  and precise-temperature events, including no transient divergence snapshot,
  persistent mismatch after the settle window, timer reset/isolation/cleanup,
  and immediate command-confirmation, unavailable, removal, and recovery paths;
- target humidity capability, bounds, exactly one HomeKit write, HomeKit report
  confirmation, invalid input, source loss, recovery, and no fabricated
  presentation step when the supported writer contract exposes none.
- AQI, CO2, and VOC device-class/unit contracts, non-finite/negative state,
  within-mapping source reuse, semantic drift, Repair creation, and recovery.

Include fixtures where climate `current_temperature` intentionally differs from
an explicitly mapped same-device HomeKit temperature sensor and an unmapped raw
sensor. The unified climate uses only the explicit, capability-valid mapping
while it agrees with the local climate's unit-specific serialization envelope;
it never guesses a source or substitutes a value merely because it has more
decimal places or a newer timestamp.

### Commands

- each standard climate method makes exactly one HomeKit service call;
- preset and both Unified clear-hold entry points each make exactly one mapped
  HomeKit service call; Clear Hold works without the preset source, becomes
  submitted rather than confirmed, and the native button exists only for an
  explicit usable mapping;
- an `unknown` HomeKit Current Mode value leaves the Unified current preset
  unreadable while retaining bounded advertised options and exactly-one preset
  dispatch, reports `unknown` source health/degradation rather than
  `unavailable`, and remains distinct from actual unavailable, missing,
  disabled, or misassociated writers that remove the capability before I/O;
- minimum fan runtime declares duration semantics in minutes, accepts only 0-60
  in exact five-minute increments, rejects boolean/non-finite/off-step/
  out-of-range values before I/O, and makes one Ecobee call;
- thermostat-display notification makes exactly one mapped Ecobee notification
  call, rejects empty/unavailable/misassociated writers before any effect, and
  never retries or fails over;
- vacation create/delete, occupancy policy, and sensor participation each
  inject the mapped Ecobee climate and make exactly one action call;
- sensor participation translates native lowercase Home/Away/Sleep presets to
  the writer's exact comfort-profile names and resolves an omitted preset from
  the bounded current Ecobee climate-mode projection;
- caller-supplied service data cannot override any mapped HomeKit or Ecobee
  writer target;
- unprojectable vendor effects become `submitted`, never `confirmed`, and a
  late completion cannot mutate a newer command revision;
- vacation names, writer-unit temperature bounds in Celsius and Fahrenheit,
  date-time pairs, occupancy policy, source service availability, and Ecobee
  sensor device selections fail before any effect when invalid or owned by
  another Ecobee config entry;
- writer unavailable fails clearly without fallback;
- confirmation observes the operation-owned source without issuing another
  call: Ecobee for cloud-observed standard operations, HomeKit for target
  humidity, and the HomeKit select for preset; Clear Hold has no supported
  confirmation source and remains submitted;
- temperature confirmation accepts only half of the writer's target step for
  quantization while other numeric fields retain the stricter default tolerance;
- confirmation success, mismatch, timeout, reload, and source loss;
- matching report during an awaited writer followed by success or failure,
  proving that only success permits confirmation and starts timeout ownership;
- confirmation from a fresh matching report whose state and attributes are
  unchanged;
- rapid repeated commands, per-mapping writer dispatch order, and superseded
  pending state, including a delayed first call that cannot finish after and
  overwrite the second;
- late observations for an older revision cannot mutate the current command;
- service error propagation and diagnostics redaction.

### Home Assistant Contracts

- no I/O in properties;
- device linking and no foreign identifiers/connections;
- stable unique IDs and entity categories;
- translated sibling names compose with the HomeKit-owned device without
  repeating the user mapping name;
- capability-aware creation/projection of equipment stage, optional AQI/CO2/VOC,
  optional precise current temperature, and optional notification, with no
  duplicate temperature/humidity/occupancy/weather entities;
- disabled-by-default policy for diagnostic/noisy entities;
- translations and config-flow strings;
- diagnostics privacy;
- bounded diagnostics and no raw backend response/exception leakage;
- quiet HomeKit push/event sources remain healthy across elapsed-age and cloud
  stale-boundary reevaluations, while actual unavailable state degrades and
  recovery restores ownership without oscillation;
- `last_reported` freshness across unchanged cadence-backed reports, including
  healthy state through the 30-minute default boundary and stale transition
  immediately after it without another source-change event, recovery on the
  next unchanged report, suppression of healthy-report refresh churn, and
  listener cleanup on unload; every timer callback retains Core callback-job
  classification and stays on the event loop;
- the 30-minute default confirmation window and persisted option overrides;
- options accept only whole seconds aligned to their advertised selector step,
  including direct or restored flow input that does not come from the rendered
  selector UI, while the complete options form remains serializable through
  Home Assistant's config-entry REST/frontend contract;
- unconfigured optional sources are absent from health/age diagnostics while a
  configured but unresolved source reports `missing`;
- exact source and command ages advance across repeated diagnostics requests
  without a source event, remain absent from climate state attributes, and an
  age-only snapshot refresh produces no climate `state_changed` event or
  Recorder row;
- active-sensor detail and command-confirmation operation/status remain live
  but excluded from recorded attributes;
- schedule/transition and vendor control/detail are not duplicated in climate
  attributes when first-class Beestat/number/sensor entities own them;
- Repairs only for persistent actionable faults, including user-disabled or
  detached required/optional sources, with recovery deletion;
- exact Home Assistant Core 2026.8.0 minimum and 2026.8.1 maintained-current
  support/test lanes, with no lane outside that year/month unless the support
  contract is intentionally widened;
- explicit pytest asyncio ownership so every top-level HA integration test is
  collected and executed rather than silently skipped;
- matching harness/Core requirement installation and final dependency closure
  in each lane, with Linux/hosted execution for HA-specific tests when native
  Windows cannot import Core;
- Ruff format/lint, proportionate strict mypy, pytest and Home Assistant tests,
  compile/JSON/translation/privacy checks, Hassfest, HACS Action, actionlint
  with ShellCheck, explicit job timeouts/concurrency, side-effect-free checkout
  without persisted credentials, and a terminal release gate.

Immutable action pins are part of the implemented public-source baseline.
CodeQL default setup is active as a required repository check. Zizmor, generic
dependency/security scanners, and additional Dependabot coverage remain
deferred until a concrete defect class, repository risk, or publication
requirement makes them worthwhile. The design-only repository did not need CI
before implementation began.

## Privacy Gate

Scan the entire committed tree, Git history, test output, workflow logs, release
text, and packaged archive for:

- addresses, IPs, coordinates, hostnames, account/email data;
- real entity/device/config-entry IDs and household names;
- credentials, tokens, cookies, capability URLs, and raw diagnostics;
- local filesystem paths and private repository URLs.
- tracked maintainer agent instructions or configuration in the public source
  archive.

Fixtures use names such as `zone_a`, `room_sensor_a`, and synthetic IDs only.
History scanning covers commit metadata, every historical filename, and every
reachable bounded blob so removed private text or binary content cannot evade a
patch-only scan.

## Private Shadow Acceptance

Private deployment evidence has no mandatory elapsed-time minimum. It should
cover the currently observable and safely exercisable cases below, with any
unobserved command/event path retained as an explicit limitation rather than a
reason to delay unrelated consumer migration:

- source availability/age through normal cloud and local update cycles;
- scheduled transitions, temporary/permanent holds, mode changes, fan activity,
  heating/cooling/idle states, and equipment detail;
- current temperature/humidity selection with source provenance;
- at least one standard control command and confirmation per thermostat, if
  control testing is explicitly authorized;
- source reload/unavailability and recovery;
- Recorder/logbook attribute volume and state churn;
- diagnostic usefulness and absence of secret/private leakage;
- comparison against the raw entities without averaging them.

Acceptance requires no unexplained semantic swaps, no duplicate writes, no
consumer regressions, no persistent source disagreement left unclassified, and
a proven rollback to the untouched source climates.

## Migration Verification

Before each consumer batch:

1. Perform an exhaustive current reference search.
2. Capture source and unified state/attributes.
3. Change only the selected consumers.
4. Trigger or observe each consumer's meaningful path.
5. Confirm no stale reference remains in that batch.
6. Keep the old entity enabled through complete migration validation.

Final deduplication requires a zero-consumer proof for every entity proposed for
disablement and a dashboard/exposure readback showing one routine thermostat
surface.
