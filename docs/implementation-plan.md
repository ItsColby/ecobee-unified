# Implementation Plan

This plan is ordered to prove ownership and failure behavior before expanding
the entity surface. Do not install into a live Home Assistant instance during
implementation unless the separate deployment gate is explicitly opened.

## Phase 0: Upstream Contract Refresh

- Read current Home Assistant climate, config flow/options, entity registry,
  helper-device linking, diagnostics, Repairs, translations, and quality rules.
- Inspect the current HomeKit Controller and Ecobee climate implementations and
  action schemas.
- Inspect the supported public entity-linking pattern used by the Beestat-derived
  integration, without importing its runtime internals.
- Re-evaluate every contract in `cross-project-learnings.md` against current
  Home Assistant APIs and record any deliberate non-applicability.
- Record Home Assistant Core 2026.8 as the single initial support/test lane and
  any deferred quality rules. Add another Core lane only when support is
  intentionally widened.

Exit: contracts and compatibility lanes are explicit and cited in code/docs.

## Phase 1: Repository and Integration Skeleton

Create the standard custom component package, manifest, config flow, typed
runtime model, constants, translations, diagnostics, public-safety checker and
tests, one exact Home Assistant Core 2026.8 lane, Hassfest/HACS validation, Ruff format/lint, a
proportionate strict mypy policy, pytest and Home Assistant tests,
actionlint/ShellCheck, explicit job timeouts/concurrency, side-effect-free
checkout without persisted credentials, and a required terminal release-gate
job. Use CalVer versioning only when a release is actually prepared.

Do not add CI while the repository remains design-only. CodeQL, zizmor,
generic dependency/security scanners, action SHA pinning, and additional
Dependabot coverage are deferred unless implementation evidence or a concrete
publication requirement justifies their signal and maintenance cost.

Exit: the empty integration configures/unloads cleanly and all CI is green.

## Phase 2: Mapping and Source Model

- Implement one config entry with add/edit/remove mapping flows.
- Use typed mapping and snapshot models.
- Track source entity changes through supported registry/event APIs.
- Validate domains/integrations and reject loops/duplicates.
- Implement source health, age, availability, and redacted diagnostics.
- Build one normalized per-mapping snapshot as the only projection input for
  climate and diagnostics.

Exit: mappings survive reload and rename; loss/recovery is tested.

## Phase 3: Read-only Unified Climate

- Implement climate capabilities and the exact field ownership table.
- Link the entity to the physical helper device.
- Preserve source units, precision, min/max, features, and unknown states.
- Add bounded provenance and degradation attributes.
- Implement no control methods yet.

Exit: the complete availability matrix and field-selection tests pass.

## Phase 4: Single-writer Control

- Route standard climate operations only to HomeKit.
- Add explicit vendor operations only where Home Assistant UX remains clear.
- Implement revision-guarded pending-command observation, confirmation,
  timeout, supersession, and diagnostics.
- Do not retry through another backend.

Exit: every command path proves exactly one service call, including failures,
timeouts, reloads, and rapid repeated requests.

## Phase 5: Optional Enrichment

- Add scheduled-profile/next-transition context from selected Beestat-derived
  entities.
- Add a diagnostic problem entity or equipment-stage sensor only if it has a
  defined consumer and stable Recorder semantics.
- Keep all historical or high-cardinality data out of climate attributes.

Exit: the integration remains fully usable without the optional source.

## Phase 6: Private Shadow Deployment

The private runtime owner supplies exact mappings, consumer inventory, backup,
installation authorization, and live acceptance. Install with distinct shadow
entity IDs. Observe at least seven days across mode, schedule, occupancy,
equipment, source-refresh, and command events before migration.

Exit: comparison evidence meets the private acceptance criteria and rollback is
demonstrated without disabling raw sources.

## Phase 7: Consumer Migration and Deduplication

- Migrate consumers in reversible batches from old canonical climates to the
  unified climates.
- Preserve old template/statistics helpers until Recorder and automation
  semantics are explicitly replaced.
- After soak, hide backend duplicates from routine dashboards/search/exposure;
  disable only proven unconsumed redundant entities.
- Keep necessary backend entities enabled for acquisition and diagnostics.

Exit: all consumers use the canonical entity, no stale references remain, the
normal UI shows one thermostat surface, and rollback remains documented.

## Phase 8: Release

Publication is separately gated. Privacy-scan the complete tree and Git history,
align manifest/tag/release version, wait for terminal CI, and create the
immutable release only from the validated commit. HACS installation, Home
Assistant checks, restart, and live validation remain separate operations.
