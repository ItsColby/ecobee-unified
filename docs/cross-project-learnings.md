# Shared Engineering Contract: Ecobee Unified Consequences

Portfolio-wide Home Assistant custom-integration engineering practice is owned
by the external `maintain-ha-custom-integrations` skill when it is available.
That owner centralizes verified shared patterns, applicability checks, and the
read-only portfolio audit. This repository remains authoritative for Ecobee
Unified's product behavior, architecture, tests, privacy boundary, and CI.
Versioning, GitHub/HACS release, installation, Home Assistant restart, live
validation, and rollback belong to the separate
`release-ha-custom-integrations` workflow.

This document records only the consequences of the shared contract for Ecobee
Unified. It is not a second copy of the portfolio contract. Re-read the central
contract and current upstream Home Assistant APIs before implementation, then
apply a shared pattern only when it protects an Ecobee Unified invariant.

## Product Boundary

Ecobee Unified is a helper and policy layer over Home Assistant-owned sources,
not another cloud client or an automation engine. It consumes supported state,
registry, event, and service APIs. It does not import another integration's
runtime objects, call the Ecobee or Beestat APIs, scrape diagnostics, edit
`.storage`, or schedule comfort changes. Its optional notification facade sends
through one mapped Home Assistant Ecobee entity; Ecobee remains the delivery
transport owner.

HomeKit remains the standard climate-state/control, precise local temperature,
preset, and clear-hold owner. A duplicate same-device temperature sensor refines
the local climate only while both readings agree within the climate
serialization envelope. The Ecobee integration remains the vendor-detail,
minimum-fan action, and thermostat-notification transport owner.
Beestat-derived entities remain the first-class
schedule/history/filter/alert surface and keep Recorder/import ownership. The
unified integration owns mapping, deterministic field selection, degradation,
command routing, command confirmation, and canonical climate plus
non-duplicate vendor projections on the same user-facing device.

## Runtime and Configuration

- Use a typed config-entry alias. Add a slotted runtime dataclass only when the
  mapping manager, command tracker, or other independently owned objects make
  it useful; do not create an untyped domain dictionary.
- Persist only stable mapping identity and explicit source/writer choices. Put
  changeable behavior in options and use native config, reconfigure, and
  options flows.
- Preserve temporarily missing or renamed selections so sources can recover.
  Never guess replacements from names or reinterpret saved numeric identifiers
  across a different physical device or account boundary.
- Require an impact summary and confirmation before changing the physical
  thermostat association or command writer.
- Treat an explicit pair selection as intent rather than proof. Installed Core
  exposes the HomeKit thermostat serial and Ecobee thermostat identifier through
  supported device-registry fields; require their equality for active cloud
  composition and reevaluate it on registry lifecycle events.
- Version persisted-contract migrations and preserve mapping identity, unique
  IDs, source/writer policy, recoverable selections, and Recorder semantics.

## Normalization and Degradation

- Build one immutable or treated-as-immutable per-mapping snapshot containing
  normalized capabilities, values, provenance, source ages, degradation, and
  pending-command state. Climate, number, sensor, and diagnostics project
  that same snapshot; entity properties perform no I/O.
- Select by documented semantics, never by newest timestamp, name similarity,
  or averaging. An unsupported field remains absent.
- HomeKit loss may activate only the documented Ecobee read fallback and must
  disable HomeKit-owned writes. Ecobee loss removes vendor detail/actions.
  Beestat loss affects its independently owned device entities. Loss of every valid source for
  a required climate semantic makes the unified climate unavailable.
- Subscribe only to explicitly mapped entities, bound retained history and
  diagnostic examples, and keep normal source processing active while command
  confirmation is pending.

## Devices and Entities

- Link every unified climate/number/sensor/notification to the selected physical
  thermostat device using Home Assistant's supported helper-device pattern
  without returning foreign identifiers/connections or claiming source-device
  ownership.
- Reconcile every owned helper link in place when the HomeKit source entity is
  moved, detached, removed, or restored; do not reload/recreate the config entry
  or mutate a foreign entity record.
- Keep stable unique IDs across reload, rename, recovery, and migration. Do not
  auto-discover thermostats or duplicate entities when a source reloads.
- Keep Recorder attributes compact and stable. Put detailed mapping,
  capability, source-age, field-selection, and command evidence in bounded,
  redacted diagnostics. Mark volatile source age, active-sensor detail, and
  command-confirmation projections unrecorded.
- Add a diagnostic entity only when it has a durable state semantic and a
  demonstrated automation or UI consumer.

## Commands, Recovery, and Repairs

- Validate the mapped entry/entity, capability, writer availability, units,
  and bounds before issuing exactly one service call.
- Validate AQI, CO2, and VOC device classes and units at configuration and
  runtime; a same-device but semantically different sensor must not be relabeled
  by the Unified projection.
- Standard climate, preset, and clear-hold commands use HomeKit only. Clear Hold
  becomes submitted after one successful press because its source button has no
  supported resulting-state contract; it does not depend on Current Mode. The
  minimum-fan number and notification facade use their explicitly mapped Ecobee
  paths. Read fallback never implies write failover, and a timeout never causes
  a second-backend retry.
- Give each pending command a monotonically increasing revision. After every
  await or source event, update confirmation only if that revision is still
  current so a late cloud observation cannot confirm, fail, or clear a newer
  command.
- Treat Core's unchanged state-report event as a fresh Ecobee observation while
  that mapped command is pending or its cadence-backed source is already stale.
  Use `last_reported`, not `last_updated`, for health cadence so stable values do
  not become false stale failures; use the stable timestamp carried by the event
  rather than its mutable `State` object, retain bounded timer-owned
  reevaluation when no event arrives, and suppress healthy-report snapshot
  rebuilds so the recovery listener does not create churn.
- Do not generalize cadence health to quiet push/event sources. Without a
  heartbeat contract, HomeKit observation age is diagnostic and command
  evidence; only actual source availability changes health or read ownership.
- Use Repairs only for persistent, actionable mapping faults such as a removed
  required entity, an invalid domain, or an internally inconsistent mapping.
  Clear the issue promptly on recovery. Transient source loss, cloud lag, and
  one unconfirmed command remain state or diagnostic evidence.

## Privacy and Validation Consequences

- Public source, fixtures, documentation, diagnostics, logs, CI, history, and
  release artifacts contain no household-specific identifiers, credentials,
  private paths, raw diagnostics, or arbitrary backend response/error text.
- Public-history validation inspects commit metadata, every historical
  filename, and every reachable bounded blob, including removed content and
  binary/non-UTF-8 artifacts that a patch-only scan cannot inspect.
- Diagnostics are allow-listed and bounded. Exact private-value scans and live
  deployment evidence remain in maintainer-controlled private owners.
- The initial support contract is one exact Home Assistant Core 2026.8 lane,
  matching the maintainer's installed runtime. Widen it only for an explicit
  broader-support requirement with passing evidence.
- Install the Home Assistant harness and exact Core requirements separately,
  let the harness own its compatible pytest dependency, run `pip check` after
  the final dependency install and before HA tests, and use Linux/hosted
  execution for the Core test surface.
- The initial repository baseline includes focused unit and HA tests, Ruff,
  proportionate strict typing, compile/JSON/translation/privacy checks,
  Hassfest, HACS validation, actionlint/ShellCheck, bounded workflows,
  credential-free checkout, and a terminal release gate.
- Immutable action pins are part of the implemented public-source baseline.
  CodeQL, zizmor, generic security scanners, and additional dependency
  automation remain conditional on concrete risk or maintenance value; the
  design-only repository did not need CI before implementation.

## Applicability Exclusions

Do not copy Library feed expansion, WebCal, digest, email, capability-route, or
temporary-image machinery. Do not copy Beestat API, statistics-import,
forecast, filter, dynamic-discovery, reauthentication, or account-continuity
machinery. Reuse only an applicable engineering contract and implement it
inside Ecobee Unified's own architecture and test harness.
