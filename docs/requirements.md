# Requirements

## User Outcome

Home Assistant should present each physical thermostat as one canonical
user-facing device surface, centered on one climate entity and colocated
non-duplicate controls/context, while preserving the specificity of Ecobee
cloud data, the responsiveness of HomeKit, and Beestat-owned schedule/history.
Routine users should not see multiple competing copies, but diagnostics must
make every selected source and fallback explicit.

The design favors specificity and honest semantics over the smallest possible
latency. Standard control remains useful, but control convenience must not
justify ambiguous values or dual writes.

## Functional Requirements

| ID | Requirement |
|---|---|
| F-01 | Configure multiple thermostat mappings through native config and reconfigure flows, and change timing policy through the native options flow, without credentials. |
| F-02 | Validate source domains/integrations and semantic contracts, prove the HomeKit/Ecobee climates represent the same physical thermostat, and reject circular, duplicate, ambiguously named, mismatched, or unproven new mappings. |
| F-03 | Expose one stable unified climate plus justified sibling entities on the HomeKit-owned thermostat device. |
| F-04 | Implement the deterministic field ownership and fallback table in `architecture.md`. |
| F-05 | Route every command to exactly one documented backend service/entity. |
| F-06 | Subscribe to source state changes; never perform I/O from entity properties. |
| F-07 | Degrade per capability when an optional source is absent, stale, unavailable, renamed, removed, or re-added, and recover a stale cadence-backed source from its next unchanged report without refreshing healthy reports. |
| F-08 | Link every unified entity to the selected physical thermostat device using the supported helper pattern, following source-device move/detach/removal/restoration without recreating stable identity. |
| F-09 | Expose compact live provenance, source health, configured comfort sensors, and command-confirmation status; expose a native per-mapping degradation problem entity; keep exact continuously advancing source and command ages in bounded diagnostics. |
| F-10 | Provide redacted diagnostics that explain mappings, capabilities, selection, freshness, and recent command state. |
| F-11 | Support reload, removal, setup retry, config-entry migration, and clean unload. |
| F-12 | Preserve honest primary-source precision, Home Assistant climate-writer units (Celsius or Fahrenheit), writer-owned target bounds, feature flags, and unavailable/unknown semantics. |
| F-13 | Allow a shadow deployment whose entity IDs cannot collide with existing canonical entities. |
| F-14 | Have no runtime dependency on Beestat; Beestat contributes independently owned sibling entities on the same device, while Unified fails honestly if its HomeKit/Ecobee climate semantics cannot be supplied. |
| F-15 | Normalize each mapping once and project climate, number, sensor, and diagnostic semantics from the same snapshot; diagnostics may calculate only elapsed source and command ages at request time from those same selected sources and command tracker. |
| F-16 | Serialize effect dispatch per mapping, revision-guard pending commands, and allow confirmation only after the current writer succeeds so stale observations, late failures, or out-of-order completions cannot override newer user intent. |
| F-17 | Preserve temporarily missing mappings and registry renames without guessing replacements or creating duplicates. |
| F-18 | Create Repairs only for persistent actionable mapping faults and remove them on recovery. |
| F-19 | When explicitly mapped, expose HomeKit current-mode presets and local clear-hold/resume through the Unified climate action and a native Unified button, with exactly one local writer. Keep the current preset unreadable but retain advertised bounded preset writes when Current Mode is `unknown` and its enabled, available, same-device select remains writable; actual unavailable, missing, disabled, or misassociated sources fail closed. Clear Hold depends only on its same-device writer and reports successful dispatch as submitted because no source state proves its effect. |
| F-20 | Project only justified Ecobee-only detail: minimum fan runtime, bounded equipment stage, explicitly selected device-class/unit-valid AQI/CO2/VOC sensors, and an optional thermostat-display notification facade; do not create duplicate temperature, humidity, occupancy, weather, schedule, or history entities. |
| F-21 | Treat quiet HomeKit push/event observation age as diagnostic rather than unavailability; only cadence-backed sources may become stale by elapsed time. |
| F-22 | Expose standard target humidity only through capability-advertised HomeKit bounds, one HomeKit writer, and revision-guarded HomeKit confirmation. |
| F-23 | Permit target-temperature step fusion only as an explicit same-physical-device metadata exception with proven HomeKit writer granularity and reconciled units/bounds; use half that writer step as the maximum cross-source temperature-confirmation tolerance, never as generic read freshness fallback. |
| F-24 | Expose bounded Unified-domain facades for vacation, occupancy-policy, and comfort-sensor-participation actions through the explicitly mapped Ecobee climate, with input/capability validation, exactly one write, writer-unit vacation bounds, and honest submitted-not-confirmed status where source state cannot prove the effect. |
| F-25 | When explicitly mapped, use a finite, unit-compatible, temperature-class HomeKit sensor on the same physical device as the precise primary `current_temperature` only while it agrees with the usable HomeKit climate inside Core's unit-specific serialization envelope; otherwise degrade and fall back to the HomeKit climate semantic and then the documented Ecobee read fallback without fabricating precision. |
| F-26 | When explicitly mapped, expose one Unified thermostat-display notification entity that forwards a non-empty message to exactly one same-device Ecobee notification writer and degrades safely across rename, association drift, disappearance, and recovery. |
| F-27 | Coalesce sequential healthy HomeKit climate and explicitly mapped precise-temperature events for one physical observation before snapshot publication, while keeping command observations, lifecycle faults/recovery, registry changes, and persistent divergence immediate or fail-closed. |

## Non-functional Requirements

- No secrets and no direct network access.
- No dependency on private implementation objects of source integrations.
- No direct `.storage` access.
- No name-guess remapping.
- No unbounded/high-churn Recorder attributes.
- No automatic write failover in the initial release.
- No duplicate command caused by retries, confirmation, or source changes.
- No later user command may be physically overwritten by an earlier writer
  call that completes out of order.
- No household-specific values in source, tests, diagnostics fixtures, docs,
  Git history, release notes, or CI logs.
- No raw backend response bodies or arbitrary backend exception text in logs,
  diagnostics, entity state, or exception chains.
- Startup order and source reloads must not require a Home Assistant restart.
- The integration must remain useful when one optional source is unavailable.
- Initial compatibility is Home Assistant Core 2026.8, verified against its
  public contracts. Widen support only when an additional version is an
  intentional maintained contract with its own passing evidence.

## MVP Acceptance

MVP is complete when all of the following are true:

1. Two or more generic thermostat mappings can coexist in one config entry.
2. The climate entity reports every standard field from the documented owner
   and uses only the documented fallback under tested failure conditions.
3. Standard climate commands produce exactly one HomeKit service call.
4. Preset and clear-hold operations produce exactly one HomeKit action, with
   Clear Hold reported as submitted rather than falsely confirmed;
   minimum-fan, vacation, occupancy-policy, and sensor-participation controls
   and thermostat-display notifications produce exactly one mapped Ecobee
   action, with no failover.
5. Command confirmation observes but never retries through another source.
6. Reload, rename, source loss/recovery, and removal tests pass.
7. Device linkage is correct on the supported Home Assistant Core 2026.8
   baseline.
8. Diagnostics are useful and privacy-redacted.
9. All repository and Home Assistant test/quality workflows are terminal green.
10. A private shadow deployment passes the current comparison and safety
    criteria before any existing consumer is migrated; acceptance has no
    mandatory elapsed-time minimum.
11. A report cannot confirm a command before writer success, and a late
    observation, writer result, or timeout cannot confirm, fail, clear, or
    physically overwrite a newer command.
12. Physical-identity or optional-sensor semantic drift disables only the
    affected Ecobee capabilities before any effect and recovers from supported
    registry/state evidence without recreating the config entry.

## Explicit Non-goals for MVP

- Re-exporting all source sensors rather than the justified cloud-only subset.
- Creating weather, occupancy, motion, battery, or history duplicates.
- Direct Ecobee authentication or Beestat API access.
- Predictive HVAC control or automatic schedule changes.
- Microphone and daylight-saving administration without a demonstrated routine
  user outcome.
- Automatic command failover.
- Reclaiming the entity IDs of existing climate entities during first install.
- Public catalog submission.
