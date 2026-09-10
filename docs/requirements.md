# Product requirements

Ecobee Unified must make a physical thermostat practical to use through one
canonical climate and its justified sibling entities while keeping source
meaning, control ownership, and uncertainty visible. These requirements define
the maintained scope. They are acceptance criteria, not a claim that a
particular release or installation has passed validation.

## Configuration and presentation

- Support multiple explicitly mapped thermostats in one native config entry.
  Use reconfigure for mappings and options for timing; require no new source
  credentials.
- Prove each new HomeKit/Ecobee pairing through supported physical-device
  identity. Validate source roles and optional device associations, reject
  duplicate sources and names, and preserve temporarily missing saved intent
  without guessing replacements.
- Keep mapping identity stable through edits, source renames, reloads, and
  device relinking. Detect concurrent flow changes before saving. Remove only
  entities owned by the entry when a mapping or projection is removed.
- Link the climate, diagnostic problem entity, vendor-only projections, and
  explicitly configured action entities to the existing HomeKit thermostat
  device without taking over its hardware identity.
- Preserve source entities and allow users to compare or migrate consumers
  without reusing existing climate IDs automatically.

## Data and control

- Apply the [field and command ownership tables](architecture.md) consistently
  across entity state, capabilities, actions, and diagnostics. Read fallback
  must never enable an alternate writer.
- Normalize one snapshot per mapping. Entity properties must not perform I/O
  or reinterpret raw data independently.
- Preserve writer-owned feature flags, units, bounds, and options. Limit target
  step sharing to the documented identity-verified metadata exception.
- Reject invalid numeric fields without overflow failures, clipping,
  invented measurements, or household comfort limits. Keep source transport
  health distinct from field validity.
- Use an explicit precise-temperature sensor only when its source contract and
  climate agreement pass. Settle paired updates, retain confirmed rejection
  evidence for the manager lifetime, and require a changed agreeing physical
  value before precision recovers.
- Provide bounded vendor context and justified entities for equipment stage,
  minimum fan runtime, optional air quality, and optional notification delivery.
  Keep the existing comfort-sensor compatibility aliases accurate.
- Expose presets and resume through their explicit HomeKit writers. Keep a
  safe Current Mode writer usable when only its current value is unknown;
  require compatible bounded options and source association.
- Expose explicit vacation, occupancy-mode, and sensor-participation actions
  through the mapped Ecobee writer. These mechanisms must not create automatic
  household policy.

## Degradation and lifecycle

- Preserve useful unaffected capabilities across missing, disabled, unknown,
  unavailable, stale, invalid, and misassociated sources. Become unavailable
  when the required climate semantics cannot be supplied.
- Treat HomeKit age as diagnostic. Apply elapsed-time staleness only to mapped
  Ecobee climate and air-quality sources, including recovery from unchanged
  reports. Avoid healthy-report and age-only entity churn.
- Share actionable and advisory classifications across the climate, problem
  entity, and diagnostics. Keep separate writer availability visible for action
  entities. Create and clear mapping Repairs from their supported conditions.
- Dispatch one mapped source-service call per accepted request without
  automatic retry or failover. Serialize dispatch per mapping within a running
  manager and prevent caller data from redirecting targets.
- Retain only the current tracked command revision. Observe before dispatch,
  require successful writer completion before confirmation, and make timeout
  or cancellation uncertainty explicit. Preserve submitted-only actions and
  native notification semantics where state cannot prove the effect.
- On setup failure or setup cancellation, and on successful unload, close command admission,
  reject queued writes, release listeners and deadlines, and fence late
  completion. Do not claim to cancel an effect already sent to a source.
- Recover supported source and registry changes without requiring a Home
  Assistant restart. Preserve supported config migration and reload behavior.

## Privacy and integration boundaries

- Use Home Assistant's supported state, registry, event, and service interfaces.
  Do not import source integrations' private runtime objects, write `.storage`,
  scrape diagnostics, or acquire data with another network client.
- Build downloadable diagnostics from a bounded allowlist without source IDs,
  household names, measurements, command payloads, secrets, or raw backend
  errors. Keep public fixtures and documentation generic.
- Keep Recorder attributes bounded and leave exact advancing ages in
  request-time diagnostics. Preserve the documented local-state and diagnostic
  privacy distinction.
- Retain Beestat as an independent history/context owner with no runtime
  dependency from Unified. Keep household policy, cross-service automation, and
  deployment evidence in the user's Home Assistant configuration.
- Do not duplicate room temperature, humidity, occupancy, battery, weather,
  schedules, alerts, filters, or historical series as additional Unified
  entities. Do not add predictive HVAC control, automatic schedule changes,
  direct source authentication, or automatic write failover.

## Acceptance and evidence

The [validation plan](validation-plan.md) owns executable checks and deployment
comparison criteria. Acceptance must cover multiple mappings; identity and
role validation; rename, source loss and recovery; numeric and temperature
quality; command ordering and confirmation; reload and unload; entity/Recorder
contracts; diagnostic privacy; and the supported Home Assistant dependency
lanes. A source review or passing unit suite alone does not establish physical
command delivery, household performance, or successful consumer migration.

Compatibility versions and commands belong to the maintained validation and
dependency owners. Report the actual candidate, checks, and results when making
a release claim. Preserve historical release evidence rather than converting
these requirements into an invented retrospective result.
