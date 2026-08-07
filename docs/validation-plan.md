# Validation Plan

## Automated Matrix

### Configuration and Lifecycle

- create, abort, duplicate, edit, remove, reload, unload;
- multiple mappings in one entry;
- invalid domain/integration and circular mapping rejection;
- entity rename, device rename, source removal/re-add, and registry disable;
- source device move, detach, removal, and restoration with helper relinking
  for climate/number/sensor records without config-entry reload or stable-entity recreation;
- startup before each source integration and later source setup/reload;
- config-entry version migration and rollback fixtures.
- options/mapping changes that preserve temporarily missing entity selections;
- explicit confirmation for physical-device or command-writer changes.

### Field Selection

For every standard and vendor field, test:

- primary and fallback both available;
- primary unavailable, fallback valid;
- primary unknown/unavailable with stale fallback;
- source field absent or malformed;
- both unavailable;
- unequal values that prove no averaging/freshest-wins behavior;
- temperature units, precision, range targets, bounds, and feature flags.

Include a fixture where climate `current_temperature` intentionally differs
from a raw thermostat-local temperature sensor. The unified climate must use the
climate semantic, never the raw sensor by apparent similarity.

### Commands

- each standard climate method makes exactly one HomeKit service call;
- preset and clear-hold each make exactly one mapped HomeKit service call;
- minimum fan runtime makes exactly one Ecobee action call;
- writer unavailable fails clearly without fallback;
- confirmation observes Ecobee state without issuing another call;
- confirmation success, mismatch, timeout, reload, and source loss;
- confirmation from a fresh matching report whose state and attributes are
  unchanged;
- rapid repeated commands and superseded pending state;
- late observations for an older revision cannot mutate the current command;
- service error propagation and diagnostics redaction.

### Home Assistant Contracts

- no I/O in properties;
- device linking and no foreign identifiers/connections;
- stable unique IDs and entity categories;
- capability-aware creation/projection of equipment stage and optional
  AQI/CO2/VOC, with no temperature/humidity/occupancy/weather duplicates;
- disabled-by-default policy for diagnostic/noisy entities;
- translations and config-flow strings;
- diagnostics privacy;
- bounded diagnostics and no raw backend response/exception leakage;
- `last_reported` freshness across unchanged reports plus stale-boundary
  reevaluation without another source-change event;
- volatile source-age, active-sensor, and command-confirmation attributes are
  excluded from Recorder;
- schedule/transition and vendor control/detail are not duplicated in climate
  attributes when first-class Beestat/number/sensor entities own them;
- Repairs only for persistent actionable faults, with recovery deletion;
- one exact Home Assistant Core 2026.8 support/test lane, with no duplicate
  legacy lane unless the maintained support contract is widened;
- separate harness/Core requirement installation, with Linux/hosted execution
  for HA-specific tests when native Windows cannot import Core;
- Ruff format/lint, proportionate strict mypy, pytest and Home Assistant tests,
  compile/JSON/translation/privacy checks, Hassfest, HACS Action, actionlint
  with ShellCheck, explicit job timeouts/concurrency, side-effect-free checkout
  without persisted credentials, and a terminal release gate.

Immutable action pins are part of the implemented public-source baseline.
CodeQL, zizmor, generic dependency/security scanners, and additional Dependabot
coverage remain deferred until a concrete defect class, repository risk, or
publication requirement makes them worthwhile. The design-only repository did
not need CI before implementation began.

## Privacy Gate

Scan the entire committed tree, Git history, test output, workflow logs, release
text, and packaged archive for:

- addresses, IPs, coordinates, hostnames, account/email data;
- real entity/device/config-entry IDs and household names;
- credentials, tokens, cookies, capability URLs, and raw diagnostics;
- local filesystem paths and private repository URLs.

Fixtures use names such as `zone_a`, `room_sensor_a`, and synthetic IDs only.
History scanning covers commit metadata, every historical filename, and every
reachable bounded blob so removed private text or binary content cannot evade a
patch-only scan.

## Private Shadow Acceptance

Private deployment evidence should cover at least seven consecutive days and
include:

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
6. Keep the old entity enabled until the complete migration and soak finish.

Final deduplication requires a zero-consumer proof for every entity proposed for
disablement and a dashboard/exposure readback showing one routine thermostat
surface.
