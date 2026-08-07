# Decision Log

## Accepted

| ID | Decision | Reason |
|---|---|---|
| D-001 | Build a custom helper integration. | Native Home Assistant grouping and template helpers cannot produce one fully functional climate entity with deterministic multi-backend field and command ownership. |
| D-002 | Domain is `ecobee_unified`; display name is Ecobee Unified. | Clear purpose and no obvious collision found during the design review; recheck before publication. |
| D-003 | Do not call the Ecobee or Beestat APIs. | Existing integrations already own authentication, transport, throttling, and data acquisition. Reuse avoids another fragile owner. |
| D-004 | HomeKit owns standard climate control and normal live climate state. | It is local and event-driven, while the cloud integration adds detail on a slower cadence. |
| D-005 | Ecobee owns vendor-specific detail/actions. | It exposes holds/program mode, equipment detail, fan minimum, active sensors, vacations, and Ecobee policy actions that HomeKit does not. |
| D-006 | Beestat contributes independently owned sibling entities on the same HomeKit device; Ecobee Unified does not consume Beestat source entities. | Beestat's value is schedule/history/filter/alert context, while its transport, entities, import, and Recorder ownership remain separate from unified live climate state and control. |
| D-007 | One deterministic source per semantic; no averaging and no freshest-wins. | Equivalent-looking source fields can differ in meaning, aggregation, calibration, and cadence. |
| D-008 | Read fallback is allowed; automatic write fallback is initially disabled. | Read continuity is useful. Retrying a command through another path risks duplicate or conflicting holds. |
| D-009 | Link every unified entity to the existing physical HomeKit device. | This gives a native single-device presentation without creating a counterfeit hardware identity or co-owning the source device. |
| D-010 | Keep raw backends enabled through migration and rollback. | They remain the acquisition owners and provide immediate recovery. Normal UI duplication is solved through canonical consumers and visibility, not deletion. |
| D-011 | Use shadow entity IDs during rollout. | Reusing existing IDs would mix Recorder semantics and weaken rollback. |
| D-012 | Public source and private deployment evidence have separate owners. | This keeps the integration publishable without leaking household topology or runtime IDs. |
| D-013 | Home Assistant Core 2026.8 is the sole initial support and CI lane. | It matches the maintained target and current upstream contracts. A second legacy lane would add duplicate machinery without a maintained compatibility promise; widen only with explicit support evidence. |
| D-014 | The product is a canonical thermostat device surface, not only a canonical climate. | Home Assistant's one-entry-per-device rule supports helper entities on the foreign device while preserving source transport ownership. |
| D-015 | HomeKit Current Mode and Clear Hold are the canonical preset/resume writers when explicitly mapped; Unified exposes resume as both a climate action and a native device button. | Core 2026.8 exposes supported local select/button entities; the Unified facade avoids routine raw-entity targeting while preserving one local writer. |
| D-016 | Expose minimum fan runtime, equipment stage, and optional AQI/CO2/VOC as first-class sibling entities. | They add vendor-only semantics without duplicating HomeKit temperature, humidity, occupancy, or weather. |
| D-017 | Do not copy Beestat schedule/transition into unified climate attributes. | Beestat already owns first-class entities and Recorder/history; colocation supplies one user-facing surface without moving transport or storage ownership. |
| D-018 | HomeKit observation age is diagnostic, not health, because its push/event contract has no heartbeat. | Quiet healthy thermostats must not oscillate into cloud fallback merely because no value changed. Actual unavailable/unknown/missing state still degrades and recovers normally. |
| D-019 | Target humidity is a standard HomeKit-owned climate capability. | The HomeKit writer advertises bounds, receives the only write, and supplies confirmation; Ecobee does not become a fallback writer. |
| D-020 | Ecobee may fill only an omitted target-temperature presentation step after same-device and writer-granularity proof. | This reconciles the local writer's actual granularity with the unified UI without borrowing cloud precision, bounds, units, or read freshness. |
| D-021 | Expose only vacations, occupancy policy, and comfort-sensor participation as opt-in Unified Ecobee actions. | These complete useful thermostat administration without requiring users to target raw Ecobee entities. The mapped service target is injected exactly once; unprojected effects are reported as submitted rather than falsely confirmed. Microphone and daylight-saving settings remain excluded. |
| D-022 | Permit an explicitly mapped same-device HomeKit temperature sensor to supply precise unified current temperature and advertise tenths precision while that source or the decimal Ecobee fallback is selected. | The HomeKit climate adapter can round its projected value even when a local accessory sensor retains honest decimals. Capability, unit, finite-value, and device-association checks plus source-dependent climate serialization preserve honest precision without inventing decimals for the whole-degree HomeKit climate fallback. |
| D-023 | Expose an optional Unified notification entity backed only by the mapped Ecobee notification writer. | Thermostat-display messages are a useful non-duplicate Ecobee capability. A single facade removes routine raw-entity targeting while preserving Ecobee transport ownership and exactly-one-write behavior. |

## Deferred Until Evidence Exists

| Topic | Default |
|---|---|
| Automatic write failover | Off. Add only with an idempotency design and proven need. |
| Additional cloud projections | Add only when they are non-duplicate, bounded, capability-proven, and have a clear device-surface role. |
| Derived room-temperature metrics | Post-MVP, after explicit room mapping and consumer definitions. |
| Config subentries | Do not add unless current Home Assistant UX/lifecycle requirements make them materially better. |
| Public HACS catalog listing | Not planned; a public repository plus custom-repository install is sufficient unless later value is demonstrated. |
| Reclaiming legacy entity IDs | Do not do during initial migration; consider only after a stable soak and explicit Recorder/rollback decision. |

## Open Implementation Checks

These are verification tasks, not unresolved product choices:

- Helper-device linking, config-entry lifecycle, optional-source behavior, and
  public Ecobee action semantics were resolved for installed Core 2026.8.1 in
  `upstream-contracts.md` before implementation.
- Measure realistic Ecobee cadence-staleness and command-confirmation thresholds
  in the private shadow deployment.
- Recheck the integration name/domain and publication destination before any
  public repository is created.
