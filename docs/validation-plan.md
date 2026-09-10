# Validation

This document defines acceptance for Ecobee Unified changes. It is a coverage
map, not a record of passing runs. [Development](development.md) provides the
commands and exact support lanes; [architecture](architecture.md) defines the
runtime behavior those checks protect.

## Choose the checks

| Change | Required evidence |
|---|---|
| Documentation or help text | Compare claims with source, check links and examples, and run affected public-content and translation checks. A documentation change does not require thermostat control or a Home Assistant restart. |
| Source selection, entities, configuration, or commands | Add or update regression coverage for the changed contract, run the affected tests during development, then run both supported Home Assistant lanes and the repository checks. |
| Core or harness support | Verify the upstream contract, align HACS and requirement owners, and pass dependency closure, strict typing, and the complete test suite separately in each lane. |
| Validation scripts or workflows | Exercise orchestration regressions and the affected execution path, including failures and cleanup; run actionlint, ShellCheck, and workflow security checks. |
| Release candidate | Validate the exact published content and applicable hosted checks, then keep installation and live acceptance separate from source results. |

Never count a skipped or uncollected test as coverage. Record the candidate
revision, any working-tree changes, Python/Core/harness versions, commands,
results, and material omissions with the run evidence. Do not replace previous
results when later runs use different source or environments.

## Automated coverage

The complete `pytest tests -q` suite includes all `test_*.py` modules below.
The files, fixtures, and assertions are the detailed executable coverage owner;
these groups explain the acceptance questions they answer.

### Configuration, identity, and lifecycle

Sources: [native integration tests](../tests/test_integration_ha.py),
[Core API tests](../tests/test_runtime_core_api.py),
[source contracts](../tests/test_configuration_source_contracts.py),
[device identity](../tests/test_source_device_identity.py), and
[setup lifecycle](../tests/test_setup_lifecycle.py).

- One config entry can manage multiple explicit mappings. Create, reconfigure,
  options, removal, reload, and unload preserve their distinct responsibilities.
  Duplicate names/sources, incorrect integrations/domains, circular sources,
  and unproven cross-backend identity are rejected.
- HomeKit serial and Ecobee thermostat identity must establish the same physical
  thermostat. A child device is not a substitute for that proof. Optional
  selections must have the expected source owner, device relationship, and
  sensor/action semantics.
- Reconfiguration confirms physical-device or command-writer changes, preserves
  accepted additive entry data, and rejects stale concurrent flows. Saved
  missing sources can be retained or removed; a missing parent cannot establish
  a new source association. Timing options validate exact whole-second steps
  and serialize through Home Assistant's frontend contract.
- Supported schema migration normalizes stored data; future schemas fail
  without rewriting them. Source renames, device moves, detachments, removals,
  disables, and restoration reconcile helper links without changing stable
  Unified identities. Cleanup removes only the entry's orphaned entities.
- Sources may appear after Unified starts. Unrelated registry events do not
  rebuild mappings. Failed or cancelled setup, platform forwarding, unload,
  and removal release listeners, timers, manager state, and setup ownership;
  late callbacks cannot revive the stopped manager.

### Field selection and temperature quality

Sources: [normalized models](../tests/test_models.py),
[numeric validity](../tests/test_numeric_validity.py),
[temperature recovery](../tests/test_temperature_quality.py),
[source contracts](../tests/test_configuration_source_contracts.py), and
[Core API tests](../tests/test_runtime_core_api.py).

- Every field has a deterministic owner. Exercise primary success, valid
  read fallback, absent/malformed values, stale fallback, total loss, and
  disagreement. Newer timestamps or additional decimal places never select a
  source by themselves. Command metadata remains writer-owned.
- Reject boolean, non-finite, overflowing, and physically impossible numeric
  input without a snapshot failure. Preserve valid extremes and distinguish
  absent optional fields, present-invalid fields, and source transport health.
  Invalid steps cannot widen confirmation tolerance or borrow fallback metadata.
- Precise temperature requires an explicit same-device source, correct live
  class/unit metadata, conversion to the climate unit, and agreement with the
  local climate's serialization envelope. Cover Celsius, Fahrenheit, Kelvin
  sensor conversion, absolute-zero rounding, and the guarded step exception.
- Paired HomeKit updates settle per mapping without publishing transient
  disagreement. Persistent mismatch, timer reset/cancellation, and immediate
  command, availability, and recovery paths retain their own semantics.
- After confirmed disagreement, climate convergence alone cannot rehabilitate
  an unchanged rejected precise reading. Cover changed-and-agreeing recovery,
  another rejected reading, equivalent converted values, formatting-only
  reports, rename and association continuity, source replacement, mapping
  isolation, and the observation reset on manager recreation.
- AQI, CO2, and VOC validate source ownership, device class, units, and bounded
  values. Live metadata takes precedence over registry defaults. Configuration,
  runtime selection, degradation, and Repairs agree during drift and recovery.

### Commands and effects

Sources: [command tracker](../tests/test_commands.py),
[command lifecycle](../tests/test_command_lifecycle.py),
[HomeKit action roles](../tests/test_homekit_action_roles.py),
[Core API tests](../tests/test_runtime_core_api.py), and
[native integration tests](../tests/test_integration_ha.py).

- Each accepted operation dispatches once to its designated mapped writer.
  Validate capability, source availability/association, action role, registered
  service, and input before I/O. Caller data cannot replace that writer target.
  Failure never triggers automatic retry or write failover.
- Standard climate control and target humidity use HomeKit; preset uses the
  mapped Current Mode select; both Resume program entry points use the mapped
  Clear Hold button. Administrative/diagnostic selects and buttons are invalid.
  An unknown current preset can retain valid bounded writer options; unavailable
  or misassociated writers cannot.
- Minimum fan runtime accepts 0-60 minutes in exact five-minute increments.
  Vendor vacation, occupancy, sensor-participation, and notification operations
  use the mapped Ecobee source. Cover temperature units/bounds, date/time pairs,
  names, comfort-profile translation, same-entry sensor devices, empty messages,
  and invalid or unavailable services. Notifications preserve the native call
  result without adding a tracked command status or delivery confirmation.
- Writer return and observed effect are separate. Confirmation uses only the
  operation's designated observer, including unchanged fresh reports, and only
  after successful writer completion. Submitted-only effects cannot become
  confirmed. Temperature tolerance uses half the accepted writer step; other
  numeric confirmation retains the stricter tolerance.
- Preserve dispatch order and revision ownership across overlapping commands,
  early observations, timeout, failure, cancellation, and supersession. Unload
  rejects queued/new writes and fences late results, without pretending that an
  already dispatched source operation has been undone.

### Presentation, health, and privacy

Sources: [equipment stage](../tests/test_sensor.py),
[normalized models](../tests/test_models.py),
[Core API tests](../tests/test_runtime_core_api.py),
[native integration tests](../tests/test_integration_ha.py), and
[public-content checks](../tests/test_public_safety.py).

- Entity properties project snapshots without I/O. Helper entities link to the
  HomeKit device without copying foreign device identifiers. Stable unique IDs,
  translated sibling names, entity categories, optional capability projection,
  and the default-enabled Source degraded problem sensor remain consistent.
- Quiet HomeKit push sources do not expire solely because time passes. Ecobee
  cadence uses report freshness, including unchanged reports and expiry without
  another state change. Recovery clears relevant faults and Repairs.
- Keep unconfigured, unknown, missing, disabled, detached, stale, and invalid
  states distinct. Unknown preset state with a usable writer is advisory; a
  simultaneous actionable fault still activates problem semantics.
- Exact source and command ages advance in diagnostics, without age-only climate
  state changes. Active-sensor detail and command status remain excluded from
  recorded attributes. Do not duplicate history or vendor entities in climate
  attributes. Equipment idle and unknown equipment values retain bounded enums.
- Diagnostics and errors use allow-listed, bounded fields without names,
  identifiers, source values, raw backend bodies, or raw exceptions. Translated
  configuration, entity, and action help lives in `translations/en.json`;
  forms, actions, and enum values have usable descriptions and translations.

### Repository and runner integrity

Sources: [public-content checks](../tests/test_public_safety.py) and
[parallel runner tests](../tests/test_parallel_validation.py).

Cover exact support pins, real async pytest collection, runtime translations,
public payload discovery, staged archive bytes, removed historical content,
commit metadata, reference/path names, binary review, linked worktrees, detached
HEAD, dirty candidates, and unavailable/shallow history. Runner tests require
both container support lanes to finish before cleanup, propagate either lane's
failure, and prevent later phases after a failed prerequisite.

The automated privacy guard has a defined pattern set; it is not a general
secret detector. Release review must also inspect newly published prose,
fixtures, logs, diagnostics, and assets for household/account details or secrets
that do not match those patterns.

## Live acceptance and consumer changes

Automated tests use synthetic sources and services. They establish product
behavior against Core APIs, not thermostat firmware behavior, cloud delivery,
physical HVAC response, or installation health.

For an installation being evaluated, compare Unified with its mapped sources
through normal local/cloud update cycles. Check current temperature/humidity,
scheduled or held modes, equipment state, availability/recovery, source
provenance, diagnostic usefulness, and Recorder/logbook churn. Control testing
should cover the authorized operations and their actual confirmation outcomes;
record unobserved paths as limitations. There is no fixed observation-duration
requirement in this plan.

Move consumers in bounded batches: inventory existing references, capture the
before state, update selected consumers, observe each meaningful path, and
verify the batch has no unintended stale references. Keep the source climates
and every other mapped source enabled: disabling one removes a Unified input or
writer. Hide sources from routine views or exposure only after checking affected
consumers and the resulting presentation. Disabling a genuinely unused entity
requires checking both ordinary consumers and Unified mappings. Installation
policy, consumer selection, and live evidence belong to the installation owner.
