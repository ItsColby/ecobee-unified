# Upstream Contract Refresh

Verified against installed Home Assistant Core 2026.8.1 on
2026-08-08. These are implementation inputs, not proof of live deployment.

## Resolved implementation checks

1. **Helper-device linking:** Core 2026.8 requires a helper entity to set its
   `device_entry` to the selected source device. Adding the helper config entry
   to a foreign device stopped working in this release. Ecobee Unified uses
   `async_entity_id_to_device` and returns no foreign identifiers or
   connections.
2. **Optional integration behavior:** Ecobee Unified declares no source
   integration as a manifest dependency or `after_dependency`. Core processes
   those requirement closures while loading the helper flow, which would make
   independent source packages an unnecessary setup prerequisite. Explicitly
   selected HomeKit and Ecobee entities are instead observed
   through the state and entity registries and recover when their owners load.
3. **Preset/resume semantics:** Core 2026.8 HomeKit Controller exposes Ecobee
   Current Mode as a supported `select` and Clear Hold as a supported `button`.
   When explicitly mapped on the same source device, Ecobee Unified uses those
   local entities as its sole preset and resume writers.
4. **Fan minimum semantics:** The Core Ecobee integration exposes
   `set_fan_min_on_time` with a 0-to-60-minute bound. The unified action routes
   from the first-class number to that writer exactly once.
5. **Compatibility lanes:** Core 2026.8.0 remains the distribution minimum and
   dependency-closed minimum lane with harness 0.13.354. Installed Core
   2026.8.1 is the maintained-current target and has its own dependency-closed
   lane with matching published harness 0.13.355. Each lane installs its exact
   harness before exact Core, installs product-owned tooling last, runs
   `pip check`, and executes the complete HA test surface. Support outside the
   Core 2026.8 year/month remains unclaimed.
6. **Source-device lifecycle:** Core 2026.8's helper lifecycle updates helper
   entity registry links when the selected source entity's device association
   changes. Ecobee Unified applies supported entity/device registry listeners
   to reconcile all owned helper records in place across moves, detachments,
   removals, and restorations, preserving stable config-entry/entity identity.
7. **Unchanged source reports:** Core advances `State.last_reported` and emits
   `EVENT_STATE_REPORTED` when an entity reports unchanged state and attributes.
   Cadence-backed Ecobee health therefore ages from `last_reported`; HomeKit
   push/event silence does not imply unavailability without a heartbeat
   contract. Report handlers use the event-owned stable timestamp rather than
   the mutable `State` field, and matching operation-owned reports can confirm
   only the current pending command revision. A filtered persistent listener
   also recovers an already-stale cadence source from its next unchanged report
   without rebuilding healthy mappings.
8. **HomeKit humidity and temperature metadata:** Core 2026.8 exposes the
   standard target-humidity feature and writer-owned humidity bounds. Its
   HomeKit Heater/Cooler entity exposes a native `target_temperature_step`, but
   the thermostat-service `HomeKitClimateEntity` used by the mapped Ecobee
   accessories does not override that property; the live mapped climate states
   therefore omit `target_temp_step`. Native writer metadata remains primary
   whenever present. A mapped same-device Ecobee step is only a guarded static
   presentation fallback for this omission, never read fallback or a precision
   substitution.
   Core serializes climate state temperatures into Home Assistant's configured
   temperature unit and does not add `unit_of_measurement` to the public climate
   state attributes. Unified therefore attaches the configured unit at its
   climate-source normalization boundary before validating writer metadata,
   precise-sensor agreement, bounds, steps, or vacation input. Home Assistant
   climate writers expose Celsius or Fahrenheit units; Kelvin remains valid for
   an explicitly mapped temperature sensor only through conversion into the
   writer's supported unit.
9. **Mapped vendor actions:** Core 2026.8.1 retains public Ecobee actions for
   vacation create/delete, Smart Home/Away and Follow Me policy, and comfort
   sensor participation. Unified mirrors their bounded public schemas, injects
   only its explicitly mapped Ecobee climate entity, and reports successful
   unprojectable effects as submitted rather than falsely confirmed. Core
   2026.8.1 introduced no relevant Ecobee or HomeKit contract change from the
   previously validated 2026.8.0 patch baseline.
10. **Precise local temperature projection:** Home Assistant climate state may
    present fewer decimal places than a same-accessory HomeKit temperature
    sensor. Unified may consume that sensor only through an explicit mapping
    with temperature device class, compatible unit, finite state, and matching
    HomeKit device, and only while it agrees with the local climate within the
    climate state's unit-specific serialization envelope. Divergence or missing
    local proof degrades explicitly. This is a local semantic refinement inside
    the canonical climate, not a freshest-value or apparent-precision heuristic.
11. **Vacation temperature units:** Core 2026.8.1 converts the public Ecobee
    vacation action from Home Assistant's configured temperature unit to the
    backend's Fahrenheit contract. Unified therefore validates caller values in
    the mapped Ecobee climate writer's advertised unit and bounds before one
    service call; it does not impose a hard-coded cross-unit range.
12. **Notification entity contract:** Core 2026.8's `NotifyEntity` exposes
    `async_send_message(message, title=None)` and the Ecobee implementation
    delegates messages to its owned backend while ignoring titles. Unified
    therefore forwards one non-empty message to one explicitly mapped Ecobee
    notification entity and lets Core own notification entity state semantics.
13. **Cross-backend physical identity:** Installed Core 2026.8.1 stores the
    HomeKit accessory serial in `DeviceEntry.serial_number` and the Ecobee
    thermostat identifier in the Ecobee device's `(ecobee, identifier)` pair.
    Unified compares those supported registry fields at configuration and after
    registry events. Explicit selection alone is not identity proof; an
    unproven or mismatched pairing disables all Ecobee composition while local
    HomeKit control remains available.
14. **Ecobee equipment idle semantics:** The Ecobee climate exposes an empty
    `equipment_running` string while no equipment is active. Unified preserves
    that healthy empty report through normalization so its bounded equipment
    sensor projects `idle`; absent or unusable source state still projects
    unavailable.

Potential improvements to the source integrations are recorded separately in
`upstream-opportunities.md`; none is required for this product and none has
been selected for upstream work.

Publication, release, installation, restart, live acceptance, consumer
migration, and rollback remain separately verified lifecycle states. Current
source and shipped state belong to Git and immutable releases; private runtime
state belongs to the owning Home Assistant deployment record.

## Primary sources

- [Core 2026.8 device ownership and helper-linking change](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)
- [Helper integrations linking to source devices](https://developers.home-assistant.io/blog/2025/07/18/updated-pattern-for-helpers-linking-to-devices/)
- [Core 2026.8.1 Ecobee climate source](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/ecobee/climate.py)
- [Core 2026.8.1 Ecobee action schema](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/ecobee/services.yaml)
- [Core 2026.8.1 HomeKit Controller climate source](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/homekit_controller/climate.py)
- [Core 2026.8.1 HomeKit Controller device identity source](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/homekit_controller/connection.py)
- [Core 2026.8.1 notify entity source](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/notify/__init__.py)
- [Core 2026.8.1 Ecobee notify source](https://github.com/home-assistant/core/blob/2026.8.1/homeassistant/components/ecobee/notify.py)
- [Home Assistant notify entity developer contract](https://developers.home-assistant.io/docs/core/entity/notify/)
- [Home Assistant config flows and migrations](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant diagnostics](https://developers.home-assistant.io/docs/core/integration/diagnostics/)
- [Home Assistant Repairs](https://developers.home-assistant.io/docs/core/platform/repairs/)
- [Home Assistant integration quality rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
- [HACS integration repository requirements](https://hacs.xyz/docs/publish/integration/)
