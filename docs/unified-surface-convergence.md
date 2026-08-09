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
| Precise current temperature | Ecobee Unified climate | Explicit same-device HomeKit sensor | Read-only |
| Preset/current mode | Ecobee Unified climate | HomeKit Controller | Explicit HomeKit select |
| Clear hold/resume | Ecobee Unified climate action and device button | HomeKit Controller | Explicit HomeKit button |
| Minimum fan runtime | Ecobee Unified number | Ecobee integration | Ecobee action |
| Equipment stage | Ecobee Unified sensor | Ecobee integration | Read-only |
| AQI, CO2, VOC | Optional Ecobee Unified sensors | Ecobee integration | Read-only |
| Thermostat-display notification | Optional Ecobee Unified notification | Ecobee integration | Explicit Ecobee notification entity |
| Schedule and next transition | Beestat entities on the device | Beestat integration | Read-only |
| Filter, alerts, maintenance | Beestat entities on the device | Beestat integration | Beestat/local helper policy |
| Historical series | Home Assistant statistics | Beestat importer and Recorder | Import workflow only |

Temperature, humidity, occupancy, motion, weather, battery, schedule, and
transition entities are not copied merely to make the Unified integration
appear to own them. The one intentional temperature refinement is projected
inside the canonical climate from an explicitly mapped, capability-valid local
sensor. Device colocation, first-class entity naming/category policy,
dashboards, and consumer migration provide the singular experience.

## Implemented Batches

1. **Product contract:** requirements, architecture, decisions, implementation,
   validation, status, and public README now define a canonical thermostat
   device surface with specialized backend ownership.
2. **Dynamic linking:** every Unified entity follows HomeKit source-device
   move/detach/removal/restoration in place. The separately owned Beestat batch
   adds equivalent cached-runtime registry reconciliation with ownership proof
   and unload cleanup.
3. **Canonical climate completion:** optional HomeKit Current Mode becomes
   climate preset support; optional Clear Hold becomes the local resume path
   through both the climate action and a discoverable Unified device button.
   An optional same-device HomeKit temperature sensor preserves honest local
   decimals in the serialized climate state. Each action has exactly one writer
   and revision-scoped observation.
4. **Cloud-only projections:** minimum fan runtime, bounded equipment stage,
   explicitly mapped AQI/CO2/VOC, and an optional thermostat-display
   notification facade are sibling platforms. Duplicate local and weather
   entities are excluded.
5. **Surface hygiene:** primary Beestat schedule/filter/alert/maintenance
   entities remain normal; freshness and intermediate forecast detail is
   diagnostic; advanced global counters remain disabled by default.
6. **Recorder/presentation:** exact continuously advancing ages remain in
   diagnostics rather than climate state attributes, while active-sensor detail
   and command operation/status remain live but unrecorded. Climate also no
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

## Vendor-action completion

Vacation creation/deletion, Smart Home/Away and Follow Me policy, and comfort
sensor participation are now bounded opt-in Unified climate actions. They
target only the explicitly mapped Ecobee climate, validate the public Core
service contract, and issue one call. Source state cannot prove their complete
effect, so successful dispatch is honestly reported as `submitted`.

Microphone and daylight-saving administration, derived room metrics, and
automatic write failover remain deferred. They do not currently justify routine
Unified-surface ownership.

Private shadow deployment, consumer migration, dashboard/exposure changes,
backend visibility cleanup, release, HACS, restart/reload, and live validation
remain separate gates.
