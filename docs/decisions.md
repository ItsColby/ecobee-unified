# Decision Log

## Accepted

| ID | Decision | Reason |
|---|---|---|
| D-001 | Build a custom helper integration. | Native Home Assistant grouping and template helpers cannot produce one fully functional climate entity with deterministic multi-backend field and command ownership. |
| D-002 | Domain is `ecobee_unified`; display name is Ecobee Unified. | Clear purpose and no obvious collision found during the design review; recheck before publication. |
| D-003 | Do not call the Ecobee or Beestat APIs. | Existing integrations already own authentication, transport, throttling, and data acquisition. Reuse avoids another fragile owner. |
| D-004 | HomeKit owns standard climate control and normal live climate state. | It is local and event-driven, while the cloud integration adds detail on a slower cadence. |
| D-005 | Ecobee owns vendor-specific detail/actions. | It exposes holds/program mode, equipment detail, fan minimum, active sensors, vacations, and Ecobee policy actions that HomeKit does not. |
| D-006 | Beestat-derived entities are optional read-only enrichment. | Their value is schedule/history context, not live control or current-state authority. |
| D-007 | One deterministic source per semantic; no averaging and no freshest-wins. | Equivalent-looking source fields can differ in meaning, aggregation, calibration, and cadence. |
| D-008 | Read fallback is allowed; automatic write fallback is initially disabled. | Read continuity is useful. Retrying a command through another path risks duplicate or conflicting holds. |
| D-009 | Link the unified climate to the existing physical HomeKit device. | This gives a native single-device presentation without creating a counterfeit hardware identity or co-owning the source device. |
| D-010 | Keep raw backends enabled through migration and rollback. | They remain the acquisition owners and provide immediate recovery. Normal UI duplication is solved through canonical consumers and visibility, not deletion. |
| D-011 | Use shadow entity IDs during rollout. | Reusing existing IDs would mix Recorder semantics and weaken rollback. |
| D-012 | Public source and private deployment evidence have separate owners. | This keeps the integration publishable without leaking household topology or runtime IDs. |
| D-013 | Home Assistant Core 2026.8 is the sole initial support and CI lane. | It matches the maintained target and current upstream contracts. A second legacy lane would add duplicate machinery without a maintained compatibility promise; widen only with explicit support evidence. |

## Deferred Until Evidence Exists

| Topic | Default |
|---|---|
| Automatic write failover | Off. Add only with an idempotency design and proven need. |
| Proxy air-quality entities | Keep native Ecobee entities; add only for a genuinely new canonical semantic. |
| Derived room-temperature metrics | Post-MVP, after explicit room mapping and consumer definitions. |
| Config subentries | Do not add unless current Home Assistant UX/lifecycle requirements make them materially better. |
| Public HACS catalog listing | Not planned; a public repository plus custom-repository install is sufficient unless later value is demonstrated. |
| Reclaiming legacy entity IDs | Do not do during initial migration; consider only after a stable soak and explicit Recorder/rollback decision. |

## Open Implementation Checks

These are verification tasks, not unresolved product choices:

- Helper-device linking, config-entry lifecycle, optional-source behavior, and
  public Ecobee action semantics were resolved for Core 2026.8.0 in
  `upstream-contracts.md` before implementation.
- Measure realistic source-staleness and command-confirmation thresholds in the
  private shadow deployment.
- Recheck the integration name/domain and publication destination before any
  public repository is created.
