# Historical Source-Candidate Checkpoint

Date: 2026-08-08

The first validated Ecobee Unified source candidate established explicit,
registry-backed thermostat mappings, deterministic HomeKit/Ecobee field and
writer ownership, independent Beestat siblings, and bounded native lifecycle,
degradation, diagnostics, and privacy contracts.

Validation covered the Home Assistant Core 2026.8.0 distribution floor and the
maintained 2026.8.1 patch, plus unit/static, Home Assistant, Hassfest, HACS,
privacy, and release-gate surfaces. The detailed implementation and validation
receipt remains in Git history.

Publication, release, HACS installation, restart, private mapping, live
validation, consumer migration, outbound effects, and rollback were still
separate closed gates at this checkpoint. This historical note does not report
current product, release, installation, or runtime state.

Current behavior and invariants belong to [architecture](architecture.md),
[requirements](requirements.md), and [decisions](decisions.md); the
[validation plan](validation-plan.md) owns acceptance proof. Current source
belongs to the protected default branch, shipped code to immutable tags and
releases, and installed/runtime state to HACS and the owning Home Assistant
instance. Later evidence supersedes this dated checkpoint.
