# Unified Surface Convergence

## Recommendation

Ecobee Unified is the canonical user-facing thermostat device surface, not a
new transport owner and not merely a replacement climate entity. It places one
unified climate and the justified non-duplicate vendor controls/details on the
HomeKit-owned physical device. Beestat places its schedule, filter, alert, and
history context on that same device while retaining its own API, import, and
Recorder responsibilities.

This achieves a singular routine Home Assistant surface without merging
credentials, cloud clients, source devices, config entries, or historical
storage. Backend entities remain enabled for acquisition, diagnostics, and
rollback; consumer migration and later visibility cleanup remain private live
gates.

## Presentation and Ownership

| User-facing semantic | Presentation owner | Transport/storage owner | Writer |
|---|---|---|---|
| Standard climate state/control | Ecobee Unified climate | HomeKit Controller | HomeKit climate |
| Preset/current mode | Ecobee Unified climate | HomeKit Controller | Explicit HomeKit select |
| Clear hold/resume | Ecobee Unified climate action | HomeKit Controller | Explicit HomeKit button |
| Minimum fan runtime | Ecobee Unified number | Ecobee integration | Ecobee action |
| Equipment stage | Ecobee Unified sensor | Ecobee integration | Read-only |
| AQI, CO2, VOC | Optional Ecobee Unified sensors | Ecobee integration | Read-only |
| Schedule and next transition | Beestat entities on the device | Beestat integration | Read-only |
| Filter, alerts, maintenance | Beestat entities on the device | Beestat integration | Beestat/local helper policy |
| Historical series | Home Assistant statistics | Beestat importer and Recorder | Import workflow only |

Temperature, humidity, occupancy, motion, weather, battery, schedule, and
transition are not copied merely to make the Unified integration appear to own
them. Device colocation, first-class entity naming/category policy, dashboards,
and consumer migration provide the singular experience.

## Implemented Batches

1. **Product contract:** requirements, architecture, decisions, implementation,
   validation, status, and public README now define a canonical thermostat
   device surface with specialized backend ownership.
2. **Dynamic linking:** every Unified entity follows HomeKit source-device
   move/detach/removal/restoration in place. The separately owned Beestat batch
   adds equivalent cached-runtime registry reconciliation with ownership proof
   and unload cleanup.
3. **Canonical climate completion:** optional HomeKit Current Mode becomes
   climate preset support; optional Clear Hold becomes the local resume path.
   Each action has exactly one writer and revision-scoped observation.
4. **Cloud-only projections:** minimum fan runtime, bounded equipment stage,
   and explicitly mapped AQI/CO2/VOC are sibling platforms. Duplicate local and
   weather semantics are excluded.
5. **Surface hygiene:** primary Beestat schedule/filter/alert/maintenance
   entities remain normal; freshness and intermediate forecast detail is
   diagnostic; advanced global counters remain disabled by default.
6. **Recorder/presentation:** volatile ages remain unrecorded, and climate no
   longer duplicates schedule/transition, equipment-stage, or minimum-fan state
   that has a first-class entity owner.

## Home Control-plane Disposition

- **Product boundary:** the canonical wording is “canonical user-facing Ecobee
  thermostat device and climate aggregation” over Home Assistant-owned HomeKit
  and Ecobee source entities, excluding source authentication/transport, raw
  device ownership, Beestat schedule/filter/alert/history/import and Recorder
  ownership, and duplicate backend entities.
- **Capabilities:** no new capability ID is justified. `helper-device-linking`,
  `normalized-model`, `single-writer-actions`, `health-projection`, migrations,
  and installed-Core evidence remain applicable; their evidence paths expand to
  the sibling entity modules and lifecycle tests.
- **Beestat ownership:** singular presentation does not require moving Beestat
  transport or Recorder ownership. Beestat remains independently releasable and
  recoverable while enriching the same physical device.
- **Registry mutation:** Ecobee and Beestat product repositories own source and
  tests. The Home coordinator alone owns portfolio wording/status/evidence
  updates after reviewing the committed product proofs.

## Deferred Recommendations

Do not add vacation creation/deletion, occupancy policy, sensor participation,
microphone, daylight-saving policy, notifications, derived room metrics, or
automatic write failover merely for breadth. Each is an independently
approvable later batch requiring a non-duplicate user outcome, explicit source
capability, bounded state/confirmation semantics, and a proven consumer.

Private shadow deployment, consumer migration, dashboard/exposure changes,
backend visibility cleanup, release, HACS, restart/reload, and live validation
remain separate gates.
