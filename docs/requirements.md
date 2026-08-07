# Requirements

## User Outcome

Home Assistant should present each physical thermostat as one canonical device
and climate entity while preserving the specificity of Ecobee cloud data, the
responsiveness of HomeKit, and useful Beestat-derived schedule/history context.
Routine users should not see multiple competing copies, but diagnostics must
make every selected source and fallback explicit.

The design favors specificity and honest semantics over the smallest possible
latency. Standard control remains useful, but control convenience must not
justify ambiguous values or dual writes.

## Functional Requirements

| ID | Requirement |
|---|---|
| F-01 | Configure multiple thermostat mappings through native config/options flows without credentials. |
| F-02 | Validate source domains/integrations and reject circular or semantically invalid mappings. |
| F-03 | Expose one unified climate entity per mapping with stable unique IDs. |
| F-04 | Implement the deterministic field ownership and fallback table in `architecture.md`. |
| F-05 | Route every command to exactly one documented backend service/entity. |
| F-06 | Subscribe to source state changes; never perform I/O from entity properties. |
| F-07 | Degrade per capability when an optional source is absent, stale, unavailable, renamed, removed, or re-added. |
| F-08 | Link the unified entity to the selected physical thermostat device using the supported helper pattern, following source-device move/detach/removal/restoration without recreating stable identity. |
| F-09 | Expose compact provenance, source age/health, and command-confirmation status. |
| F-10 | Provide redacted diagnostics that explain mappings, capabilities, selection, freshness, and recent command state. |
| F-11 | Support reload, removal, setup retry, config-entry migration, and clean unload. |
| F-12 | Preserve unit conversion, target bounds, feature flags, and unavailable/unknown semantics. |
| F-13 | Allow a shadow deployment whose entity IDs cannot collide with existing canonical entities. |
| F-14 | Operate without Beestat, and fail honestly if the required climate semantics cannot be supplied. |
| F-15 | Normalize each mapping once and project climate/diagnostic surfaces from the same snapshot. |
| F-16 | Revision-guard pending commands so stale observations cannot update a superseded command. |
| F-17 | Preserve temporarily missing mappings and registry renames without guessing replacements or creating duplicates. |
| F-18 | Create Repairs only for persistent actionable mapping faults and remove them on recovery. |

## Non-functional Requirements

- No secrets and no direct network access.
- No dependency on private implementation objects of source integrations.
- No direct `.storage` access.
- No name-guess remapping.
- No unbounded/high-churn Recorder attributes.
- No automatic write failover in the initial release.
- No duplicate command caused by retries, confirmation, or source changes.
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
4. Vendor operations produce exactly one Ecobee action and are never silently
   substituted for a standard command.
5. Command confirmation observes but never retries through another source.
6. Reload, rename, source loss/recovery, and removal tests pass.
7. Device linkage is correct on the supported Home Assistant Core 2026.8
   baseline.
8. Diagnostics are useful and privacy-redacted.
9. All repository and Home Assistant test/quality workflows are terminal green.
10. A private shadow deployment completes its soak and comparison criteria
    before any existing consumer is migrated.
11. A late observation cannot confirm, fail, or clear a newer pending command.

## Explicit Non-goals for MVP

- Re-exporting all source sensors.
- Creating weather, occupancy, motion, battery, or history duplicates.
- Direct Ecobee authentication or Beestat API access.
- Predictive HVAC control or automatic schedule changes.
- Automatic command failover.
- Reclaiming the entity IDs of existing climate entities during first install.
- Public catalog submission.
