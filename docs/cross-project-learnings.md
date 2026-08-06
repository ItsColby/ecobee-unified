# Cross-project Learnings

This document records reusable engineering contracts learned from the current
Free Library Events and Beestat Statistics integrations. They are inputs to
Ecobee Unified, not dependencies and not a reason to copy product-specific
features. Re-read current Home Assistant contracts and each source integration
before implementation because APIs and established patterns can change.

## Applicability Rule

Adopt a pattern only when it protects an Ecobee Unified invariant or materially
reduces maintenance. Do not reproduce Library feed/rendering/email machinery or
Beestat API/statistics/filter machinery. Reuse the contract, not the code.

## Configuration and Lifecycle

### Typed config-entry runtime

Both integrations attach typed runtime state to `ConfigEntry.runtime_data`.
Library needs one coordinator and uses a typed `ConfigEntry[Coordinator]` alias;
Beestat needs a client, coordinator, importer, and interval and uses a slotted
runtime dataclass. Ecobee Unified should use the smallest corresponding shape:

- a typed config-entry alias;
- a slotted runtime dataclass only if mapping manager, command tracker, or other
  independently owned runtime objects make it useful; and
- no untyped domain dictionary for entry-owned state.

### Data, options, and identity

Persist only the minimum stable mapping identity needed to reconstruct runtime.
Put changeable behavior in options. Use native config, reconfigure, and options
flows; validate replacements before saving; reload after a successful change.

Mapping changes must preserve unknown/unavailable selected entities so a
temporarily absent backend can recover. Removing a required source or changing
the physical thermostat association needs an impact summary and confirmation.
Do not silently reinterpret saved numeric IDs across a different physical
device/account boundary.

### Versioned migrations and stable continuity

Persisted-contract changes require config-entry migrations. UI changes that use
an already-supported field do not. Migrations must preserve:

- mapping identity and explicit source choices;
- stable entity unique IDs;
- source policy and command-writer policy;
- unavailable but recoverable selections; and
- the semantics of existing Recorder series.

Downgrade compatibility is valuable only when it can be maintained without a
second competing owner. Do not keep permanent mirror fields by reflex.

## Source and Runtime Model

### Explicit supported boundary

Library documents exactly which public feeds and fallbacks it supports;
Beestat maintains a checked API-surface inventory. Ecobee Unified likewise
needs a narrow source contract:

- public Home Assistant climate/entity state;
- entity/device registries and state-change events;
- Home Assistant services/actions; and
- no direct cloud API, private source runtime objects, diagnostics scraping, or
  `.storage` access.

An unsupported or absent field remains absent. Do not infer it from names or an
adjacent field that merely looks similar.

### Normalize once, project many

Library builds one deterministic normalized event model for calendar, WebCal,
and digest projections. Beestat builds one runtime model for its entity and
statistics surfaces. Ecobee Unified should build one immutable or treated-as-
immutable per-mapping snapshot containing normalized source capabilities,
values, provenance, ages, degradation, and pending-command state. Climate,
diagnostics, and any diagnostic entity project that same snapshot.

Never let individual entity properties independently reinterpret raw source
attributes. This prevents cross-surface disagreement and keeps properties free
of I/O.

### Partial success and capability-aware degradation

Library preserves usable feed results and fails only when all selected sources
fail. Beestat keeps optional mappings and historical capabilities independent.
Ecobee Unified should degrade by capability:

- HomeKit loss can activate the documented Ecobee read fallback but disables
  HomeKit-owned writes.
- Ecobee loss removes vendor detail/actions without invalidating healthy
  HomeKit climate state.
- Beestat loss removes schedule/history context only.
- Loss of every valid source for a required climate semantic makes the unified
  climate unavailable.

Keep bounded failure examples and counts for diagnostics. Do not expose an
unbounded error collection in state attributes.

### Bounded work and stale-result guards

Library bounds request concurrency, redirects, response size, adaptive
expansion, and total duration. Beestat bounds retry windows while normal refresh
continues and re-reads the current revision after awaits so an old request
cannot overwrite a newer user action.

Ecobee Unified makes no network requests, but the same principles apply:

- subscribe only to mapped entities;
- debounce/coalesce a burst only when necessary and without hiding meaningful
  state transitions;
- bound retained diagnostic/command history;
- give command confirmation a finite window;
- tag each pending command with a monotonically increasing revision/token; and
- after every await/event, update confirmation state only if that command is
  still current. A late cloud update must not confirm or fail a superseded
  command.

Normal source state processing must continue while a confirmation is pending.

## Devices and Entities

### Link without co-ownership

Beestat demonstrates the current helper pattern: assign the existing source
`DeviceEntry` to an enrichment entity, do not return foreign identifiers or
connections, and remove only proven legacy helper ownership through supported
Home Assistant APIs. Ecobee Unified must:

- link its climate entity to the selected physical thermostat device;
- never claim the source device as owned by its config entry;
- keep a fallback helper-owned device only when no valid source device exists;
- fail closed before removing any mixed/shared registry record; and
- test migration from any earlier ownership model before shipping such a
  migration.

### Stable identity and dynamic discovery

Beestat adds newly discovered entities without duplicating known unique IDs and
preserves explicit exclusions and unknown saved overrides. Ecobee Unified's
mapping set is user-selected, not cloud-discovered, so it should not create
thermostats automatically. It should still:

- track entity-registry renames;
- tolerate temporarily missing selected entities;
- avoid duplicate entities on source reload/recovery; and
- require explicit configuration for a new physical thermostat.

### Compact state; rich on-demand diagnostics

Both integrations keep private/high-volume evidence out of ordinary entity
state. Ecobee Unified should bound climate attributes to stable automation-
useful values and compact provenance. Detailed mapping, capability, source age,
field selection, and command-reconciliation evidence belongs in redacted
downloadable diagnostics.

Create a diagnostic entity only when it has a durable state semantic and an
actual automation/UI consumer. An attribute is not free: Recorder writes it
with every state change.

## Actions, Side Effects, and Recovery

### One action, one owner

Library's render action produces data while the caller owns delivery and
scheduling. Beestat keeps HomeKit/Ecobee live control outside its integration.
Ecobee Unified must similarly avoid becoming an automation engine:

- it routes an explicitly requested operation to one backend;
- it does not schedule comfort changes, infer user intent, or retry through a
  second backend;
- response-producing diagnostic actions use `SupportsResponse.ONLY`; and
- source refresh remains owned by the source integration.

### Validate before side effects

Resolve the mapped entry/entity, required capability, writer availability,
units, and requested bounds before calling a service. Use translated
`ServiceValidationError`/Home Assistant errors with safe bounded context. Do
not pass raw backend error payloads into logs, diagnostics, attributes, or
exception chains.

### Fail closed for sensitive capability changes

Library explicitly confirms rotation of a capability URL because it invalidates
clients. Beestat confirms an account replacement because identifiers and
history can cross a boundary. Ecobee Unified should require confirmation when a
mapping change switches the physical thermostat/device or changes command
ownership. Ordinary label or optional-enrichment changes should remain simple.

### Actionable Repairs

Use Repairs only for persistent conditions with a clear user action, such as a
required mapped entity being removed, a selected entity having the wrong
domain, or a mapping becoming internally inconsistent. Transient source
unavailability, ordinary cloud lag, and a single unconfirmed command belong in
state/diagnostics unless persistence makes intervention necessary. Delete an
issue promptly when its condition clears.

## Privacy and Security

The maintained integrations establish a strong public/private boundary that
Ecobee Unified should adopt from its first commit:

- generic fixture/device/entity names only;
- no private entity IDs, device/config-entry IDs, paths, diagnostics, logs,
  hostnames, IPs, tokens, or credentials in the public tree or history;
- exact-value publication scans run only from a maintainer-controlled private
  gate, never by uploading private tokens to CI;
- redacted diagnostics use allow-listed bounded output where practical;
- no raw remote bodies or arbitrary exception text; and
- GitHub Actions are SHA-pinned with least permissions, no persisted checkout
  credentials, concurrency cancellation, timeouts, actionlint/ShellCheck,
  zizmor, and dependency update coverage.

The repository privacy checker should scan tracked/public-relevant files while
ignoring generated caches and local environments. Its own tests must prove both
detection and avoidance of false confidence from ignored/binary/generated data.

## Validation and Release

Adopt the shared proven baseline, adjusted to the integration's actual source
surface:

- Python 3.14;
- focused unit tests plus Home Assistant integration tests;
- minimum supported and current deployed stable Core lanes;
- install the HA harness and exact Core requirements as separate steps;
- Linux/container or hosted execution for HA tests that import `fcntl`;
- Ruff format/lint and a proportionate strict typing contract;
- compile, JSON/translation, privacy, Hassfest, HACS, actionlint/ShellCheck,
  zizmor, and dependency/security checks;
- a final release-gate job that fails unless every required job succeeded; and
- CodeQL completion plus inspection/disposition of open alerts at the exact
  candidate commit before tagging.

Do not treat tool installation or workflow success as proof of correctness.
Report exact skipped lanes. A validated local commit is not pushed, published,
released, installed, restarted, migrated, or live-validated.

## Ecobee Unified Consequences

| Area | Required consequence |
|---|---|
| Runtime | One normalized per-mapping snapshot and typed entry runtime. |
| Configuration | Native flows, recoverable saved mappings, explicit physical-device/writer changes. |
| Source updates | Event subscriptions only; no forced refresh or network client. |
| Control | Validate first, exactly one service call, revision-guarded observation, no write fallback. |
| Devices | Link to source device without identifiers, connections, or co-ownership. |
| Diagnostics | Allow-listed/redacted, bounded source/selection/command evidence. |
| Repairs | Persistent actionable mapping faults only; clear on recovery. |
| Entities | Stable IDs, compact attributes, no automatic duplicate surface. |
| CI/release | Public-safety and HA compatibility from the first implementation commit; immutable separately gated releases. |
