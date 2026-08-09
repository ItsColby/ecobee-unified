# Historical Source-Candidate Checkpoint

Date: 2026-08-08

This file preserves the pre-publication boundary for the first validated
Ecobee Unified source candidate. It is historical evidence, not the current
product, release, HACS, installation, or live-state owner.

## Checkpoint

The candidate had established the product contracts that remain owned by the
current repository:

- one typed config entry with multiple explicit, identity-proven thermostat
  mappings;
- HomeKit ownership of standard climate state and control;
- Ecobee ownership of bounded vendor-only detail and actions;
- Beestat ownership of schedule, filter, alert, history, import, and Recorder
  semantics;
- deterministic field selection, exactly one writer per command, no averaging,
  no freshest-source selection, and no automatic write failover;
- native config, reconfigure, and options flows using stable registry-backed
  references;
- capability-aware degradation, bounded diagnostics, Repairs, stable entities,
  helper-device linking, and clean reload/unload behavior;
- quiet HomeKit observation age treated as diagnostic rather than
  unavailability; and
- public-safe source, fixtures, diagnostics, history, CI, and release material.

The candidate validation covered the declared Home Assistant Core 2026.8.0
distribution floor and the maintained 2026.8.1 patch, plus the repository's
unit/static, Home Assistant, Hassfest, HACS, privacy, and release-gate surfaces.
The detailed implementation and validation receipt remains available in Git
history.

## Current Owners

- Product behavior and invariants: `README.md`, `docs/architecture.md`,
  `docs/requirements.md`, and `docs/decisions.md`.
- Validation contract: `docs/validation-plan.md`, tests, and the current
  validation workflow.
- Current source: the repository's protected default branch.
- Shipped code: immutable GitHub tags and releases.
- Installed/runtime state: HACS and the owning Home Assistant instance.

At this historical checkpoint, publication, release, HACS installation,
Home Assistant restart, private mapping, live validation, consumer migration,
outbound effects, and rollback were still separate closed gates. Later Git,
release, and runtime evidence supersedes that dated gate state.
