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
- Read the central `maintain-ha-custom-integrations` engineering contract and
  re-evaluate its Ecobee-specific consequences in
  `cross-project-learnings.md` against current Home Assistant APIs.
- Record exact Core 2026.8.0 minimum and exact maintained Core 2026.8.1 test
  lanes plus any deferred quality rules. Add a lane outside that year/month
  only when support is intentionally widened.

Exit: contracts and compatibility lanes are explicit and cited in code/docs.

## Phase 1: Repository and Integration Skeleton

Create the standard custom component package, manifest, config flow, typed
runtime model, constants, translations, diagnostics, public-safety checker and
tests, exact minimum and maintained Home Assistant Core 2026.8 lanes,
Hassfest/HACS validation, Ruff format/lint, a proportionate strict mypy policy,
pytest and Home Assistant tests,
actionlint/ShellCheck, explicit job timeouts/concurrency, side-effect-free
checkout without persisted credentials, and a required terminal release-gate
job. Use CalVer versioning only when a release is actually prepared.

Do not add CI while the repository remains design-only. The implemented source
candidate uses immutable action pins required by the shared public-source
baseline. CodeQL, zizmor, generic dependency/security scanners, and additional
Dependabot coverage remain release-readiness decisions unless implementation
evidence or a concrete publication requirement justifies their signal and
maintenance cost.

Exit: the empty integration configures/unloads cleanly and all CI is green.

## Phase 2: Mapping and Source Model

- Implement one config entry with add/edit/remove mapping flows.
- Use typed mapping and snapshot models.
- Track source entity changes through supported registry/event APIs.
- Validate domains/integrations and reject loops, reused sources, and
  case-insensitively duplicate mapping names.
- Compare the supported HomeKit device serial with the Ecobee device identifier;
  reject mismatched or unproven new pairings and reevaluate the proof on
  registry lifecycle changes.
- Implement source availability separately from observation age: quiet HomeKit
  remains healthy, while cadence-backed Ecobee sources retain bounded stale
  reevaluation, filtered unchanged-report recovery, healthy-report suppression,
  and redacted diagnostics.
- Build one normalized per-mapping snapshot as the only projection input for
  climate, number, sensor, and diagnostics surfaces.

Exit: mappings survive reload and rename; loss/recovery is tested.

## Phase 3: Read-only Unified Device Surface

- Implement climate capabilities and the exact field ownership table.
- Link every unified entity to the physical helper device.
- Preserve honest primary precision and HomeKit-writer-owned unit/min/max. When
  explicitly selected, validate and unit-normalize a same-device HomeKit
  temperature sensor before using its unrounded value as the unified climate's
  current temperature. Require agreement with the usable HomeKit climate inside
  Core's unit-specific serialization envelope before advertising tenths
  precision; divergence or an unverifiable local climate follows the documented
  fallback chain instead. Otherwise retain
  the documented climate fallback chain and whole-degree presentation.
- Add the explicit same-device target-step fusion only when the HomeKit adapter
  omits its independently proven writer granularity and units/bounds reconcile.
- Add bounded provenance and degradation attributes.
- Implement no control methods yet.

Exit: the complete availability matrix and field-selection tests pass.

## Phase 4: Single-writer Control

- Route standard climate operations only to HomeKit.
- Route preset selection and clear-hold only to explicitly mapped HomeKit
  select/button entities, exposing clear-hold through both the Unified climate
  action and a native Unified resume-program button without adding a writer.
  Clear Hold does not depend on the select and becomes submitted after one
  successful button call because its complete effect is not state-confirmable.
- Route standard target humidity only to the capability-advertised HomeKit
  climate, validate its writer-owned bounds, and confirm from HomeKit reports.
- Expose minimum fan runtime as the sole Ecobee-backed number writer.
- Expose bounded Unified-domain vacation, occupancy-policy, and
  comfort-sensor-participation actions through the mapped Ecobee climate.
  Validate vacation temperatures against that writer's current unit and bounds,
  and mark successful unprojectable effects submitted rather than confirmed.
- Expose an optional thermostat-display notification entity through exactly one
  explicitly mapped same-device Ecobee notification writer.
- Implement revision-guarded pending-command observation, confirmation,
  timeout, supersession, and diagnostics. Serialize effect dispatch per mapping,
  enable confirmation and its timeout only after writer success, retain an
  in-flight matching observation across that success boundary, and keep a
  later writer failure authoritative. Temperature confirmation tolerates at
  most half the proven HomeKit target step; other numeric fields retain the
  strict fixed tolerance.
- Inject the resolved mapped target after validating caller data so no service
  payload can redirect the one writer.
- Do not retry through another backend.

Exit: every command path proves exactly one service call, including failures,
timeouts, reloads, rapid repeated requests, and late completion of an
unconfirmable vendor action.

## Phase 5: Optional Enrichment

- Add a bounded equipment-stage sensor and explicitly mapped AQI/CO2/VOC
  sensors only after device-class, unit, finite-state, and same-device
  validation; do not create duplicate HomeKit temperature/humidity/occupancy/
  weather entities. Precise mapped temperature is projected through the
  canonical climate instead.
- Rely on Beestat's first-class schedule/filter/alert entities already linked
  to the same device; do not copy their state or move Recorder ownership.
- Keep all historical, volatile-age, and high-cardinality data out of recorded
  climate attributes.

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
