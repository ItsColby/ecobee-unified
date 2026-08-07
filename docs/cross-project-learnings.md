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
`.storage`, schedule comfort changes, or deliver notifications.

HomeKit remains the standard climate-state and control owner. The Ecobee
integration remains the vendor-detail and vendor-action owner. Optional
Beestat-derived entities remain schedule/history context only. The unified
integration owns mapping, deterministic field selection, degradation, command
routing, command confirmation, and the canonical presentation surface.

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
- Version persisted-contract migrations and preserve mapping identity, unique
  IDs, source/writer policy, recoverable selections, and Recorder semantics.

## Normalization and Degradation

- Build one immutable or treated-as-immutable per-mapping snapshot containing
  normalized capabilities, values, provenance, source ages, degradation, and
  pending-command state. Climate, diagnostics, and diagnostic entities project
  that same snapshot; entity properties perform no I/O.
- Select by documented semantics, never by newest timestamp, name similarity,
  or averaging. An unsupported field remains absent.
- HomeKit loss may activate only the documented Ecobee read fallback and must
  disable HomeKit-owned writes. Ecobee loss removes vendor detail/actions.
  Beestat loss removes schedule/history context. Loss of every valid source for
  a required climate semantic makes the unified climate unavailable.
- Subscribe only to explicitly mapped entities, bound retained history and
  diagnostic examples, and keep normal source processing active while command
  confirmation is pending.

## Devices and Entities

- Link the unified climate to the selected physical thermostat device using
  Home Assistant's supported helper-device pattern without returning foreign
  identifiers/connections or claiming source-device ownership.
- Keep stable unique IDs across reload, rename, recovery, and migration. Do not
  auto-discover thermostats or duplicate entities when a source reloads.
- Keep Recorder attributes compact and stable. Put detailed mapping,
  capability, source-age, field-selection, and command evidence in bounded,
  redacted diagnostics.
- Add a diagnostic entity only when it has a durable state semantic and a
  demonstrated automation or UI consumer.

## Commands, Recovery, and Repairs

- Validate the mapped entry/entity, capability, writer availability, units,
  and bounds before issuing exactly one service call.
- Standard climate commands use HomeKit only. Vendor-specific actions use the
  explicitly documented Ecobee path. Read fallback never implies write
  failover, and a timeout never causes a second-backend retry.
- Give each pending command a monotonically increasing revision. After every
  await or source event, update confirmation only if that revision is still
  current so a late cloud observation cannot confirm, fail, or clear a newer
  command.
- Use Repairs only for persistent, actionable mapping faults such as a removed
  required entity, an invalid domain, or an internally inconsistent mapping.
  Clear the issue promptly on recovery. Transient source loss, cloud lag, and
  one unconfirmed command remain state or diagnostic evidence.

## Privacy and Validation Consequences

- Public source, fixtures, documentation, diagnostics, logs, CI, history, and
  release artifacts contain no household-specific identifiers, credentials,
  private paths, raw diagnostics, or arbitrary backend response/error text.
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
