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
| F-07 | Degrade only affected capabilities when a configured source is missing, stale, unavailable, or invalid; preserve supported registry renames and recover when a source is restored. Recover a stale cadence-backed source from its next unchanged report without refreshing healthy reports. |
| F-08 | Link every unified entity to the selected physical thermostat device using the supported helper pattern, following source-device move/detach/removal/restoration without recreating stable identity. |
| F-09 | Expose compact live provenance, source health, configured comfort sensors, and command-confirmation status; expose a native per-mapping degradation problem entity whose state represents actionable degradation while retaining bounded advisory details; keep exact continuously advancing source and command ages in bounded diagnostics. |
| F-10 | Provide redacted diagnostics that explain mappings, capabilities, selection, freshness, advisory/actionable degradation, and recent command state. |
| F-11 | Support reload, removal, setup retry, config-entry migration, and clean unload. Stop admitting commands and reject queued dispatches on unload; late source-call completion must not recreate manager state, listeners, or deadlines. |
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
| F-28 | Reject unrepresentable, non-finite and proven quantity-invalid values without throwing during normalization; distinguish a present invalid field from an absent optional field, retain source transport health, and never clip or manufacture replacement measurements. |

## Non-functional Requirements

- No secrets and no direct network access.
- No dependency on private implementation objects of source integrations.
- No direct `.storage` access.
- No name-guess remapping.
- No unbounded/high-churn Recorder attributes.
- No automatic write failover.
- No duplicate command caused by retries, confirmation, or source changes.
- Serialize calls within one running manager; unloading or cancelling a wait
  cannot undo an already dispatched source action or prove its physical outcome.
  Never dispatch queued commands from a stopped manager or retry uncertain work.
- No household-specific values in source, tests, diagnostics fixtures, docs,
  Git history, release notes, or CI logs.
- No raw backend response bodies or arbitrary backend exception text in logs,
  diagnostics, entity state, or exception chains.
- Startup order and source reloads must not require a Home Assistant restart.
- The integration must remain useful when one optional source is unavailable.
- Maintain dependency-closed lanes for the Core 2026.8.0 distribution floor and
  Core 2026.9.1 current stable release using their matching published harnesses.
  Further support changes require explicit version owners and passing evidence.

## Acceptance

Acceptance requires the functional requirements above and the complete
[validation matrix](validation-plan.md), including:

1. Two or more generic mappings coexist in one entry with correct Core 2026.8
   device linkage, documented field ownership/fallback, and passing reload,
   rename, source loss/recovery, and removal cases.
2. Every standard, preset, and Clear Hold action makes exactly one HomeKit call;
   minimum-fan, vacation, occupancy-policy, sensor-participation, and notification
   actions make exactly one mapped Ecobee call. There is no retry or failover,
   and successful Clear Hold and other unprojectable effects remain submitted.
3. Operation-owned observation starts before dispatch, including unchanged
   reports, but confirmation requires writer success. Late reports, results,
   and timeouts cannot mutate newer revisions; unload rejects queued effects.
4. Physical-identity and optional-sensor semantic drift block affected Ecobee
   capabilities before effects and recover from supported registry/state
   evidence without recreating the config entry.
5. Useful diagnostics redact credentials, account identifiers, household data,
   and raw backend responses; all repository and HA test/quality workflows
   finish green.
6. [Shadow acceptance](validation-plan.md#local-shadow-acceptance) passes the
   comparison and safety criteria before existing consumers migrate. There is
   no mandatory elapsed-time minimum.

## Explicit Non-goals

- Re-exporting all source sensors rather than the justified cloud-only subset.
- Creating weather, occupancy, motion, battery, or history duplicates.
- Direct Ecobee authentication or Beestat API access.
- Predictive HVAC control or automatic schedule changes.
- Microphone and daylight-saving administration without a demonstrated routine
  user outcome.
- Automatic command failover.
- Reclaiming the entity IDs of existing climate entities during first install.
- Public catalog submission.
