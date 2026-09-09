# Upstream Contract Refresh

The original Core 2026.8.1 contract review is retained below; compatibility,
source semantics and command lifecycle were refreshed against Core 2026.9.1 on
2026-09-08. These are implementation inputs, not proof of live deployment.
[Architecture](architecture.md) owns Unified's behavior and failure policy;
this document retains the upstream facts and their sources.

## Resolved implementation checks

1. **Helper-device linking:** Core 2026.8 requires a helper entity to set its
   `device_entry` to the selected source device. Adding the helper config entry
   to a foreign device stopped working in this release. Unified uses
   `async_entity_id_to_device` without foreign identifiers or connections.
2. **Optional integration behavior:** Core processes manifest dependency and
   `after_dependency` requirement closures while loading a helper flow. Unified
   declares neither for source integrations: registry/state observation avoids
   making their independent packages setup prerequisites.
3. **Preset/resume semantics:** Core 2026.8 HomeKit Controller exposes Ecobee
   Current Mode as a supported `select` and Clear Hold as a supported `button`.
   They are independent local actions.
4. **Fan minimum semantics:** The Core Ecobee integration exposes
   `set_fan_min_on_time` with a 0-to-60-minute bound.
5. **Compatibility lanes:** Core 2026.8.0 uses harness 0.13.354 for the
   distribution-minimum lane; maintained Core 2026.9.1 uses matching harness
   0.13.364. The [validation runner](../scripts/verify-release-local.sh) owns
   installation order, dependency closure, and the complete HA test execution.
6. **Source-device lifecycle:** Core 2026.8's helper lifecycle updates helper
   registry links when the source entity's device association changes. Public
   entity/device registry listeners support in-place reconciliation across
   moves, detachments, removals, and restorations.
7. **Unchanged source reports:** Core advances mutable `State.last_reported` and
   emits `EVENT_STATE_REPORTED` for unchanged state/attributes. Handlers use the
   event-owned stable timestamp. This supports cadence-backed stale recovery
   and operation-owned confirmation without treating HomeKit push silence as
   a missing heartbeat or rebuilding every healthy mapping.
8. **HomeKit humidity and temperature metadata:** Core 2026.8 exposes target
   humidity and writer-owned bounds. Its HomeKit Heater/Cooler entity exposes
   `target_temperature_step`; the thermostat-service `HomeKitClimateEntity`
   used by the mapped Ecobee accessories does not, so those live climate states
   omit `target_temp_step`. Native metadata remains primary; the guarded
   same-device presentation exception is defined in architecture.
   Core serializes climate temperatures in Home Assistant's configured unit
   without a public `unit_of_measurement` attribute. Unified attaches that unit
   before validating metadata or temperatures. Climate writers support Celsius
   or Fahrenheit; a mapped Kelvin sensor requires conversion into that unit.
9. **Mapped vendor actions:** Core 2026.8.1 retains public Ecobee vacation
   create/delete, Smart Home/Away, Follow Me, and comfort-sensor actions. It
   introduced no relevant Ecobee or HomeKit contract change from the validated
   2026.8.0 baseline. Unified's bounded action schemas and submitted outcomes
   follow the [command policy](architecture.md#command-policy).
10. **Precise local temperature:** Climate state may expose fewer decimals than
    a same-accessory HomeKit temperature sensor. This alone is not proof of a
    better reading; explicit mapping, semantic agreement, and recovery are
    governed by [source selection](architecture.md#deterministic-field-ownership)
    and [observation policy](architecture.md#updates-and-availability).
11. **Vacation temperature units:** Core 2026.8.1 converts vacation inputs from
    Home Assistant's configured unit to the backend's Fahrenheit contract.
    Unified validates against the mapped writer's unit/bounds, without a fixed
    cross-unit range.
12. **Notification entity contract:** Core 2026.8's `NotifyEntity` exposes
    `async_send_message(message, title=None)`. Ecobee delegates to its backend
    and ignores titles; Core owns standard notification entity state semantics.
13. **Cross-backend identity:** Installed Core 2026.8.1 stores the HomeKit serial
    in `DeviceEntry.serial_number` and the Ecobee thermostat identifier in the
    device's `(ecobee, identifier)` pair. These public fields permit identity
    comparison; selection alone is not proof.
14. **Equipment idle semantics:** Ecobee exposes an empty `equipment_running`
    string when no equipment is active. It is a healthy idle report, distinct
    from absent or unusable state.
15. **Current source and lifecycle limits:** Core 2026.9.1's Ecobee climate
    consumes `actualTemperature`, the thermostat-displayed quantity, which may
    be feels-like under humidex. It is not independent dry-bulb corroboration.
    HomeKit's duplicate sensor caches its characteristic object while the
    climate reads the current service; Unified's recovery guard does not fix
    acquisition. Core entity-platform removal does not drain arbitrary custom
    manager service calls. A single Clear Hold dispatch may perform multiple
    source-owned protocol writes.

[Upstream opportunities](upstream-opportunities.md) remain separate, optional,
and unselected for upstream work.

## Primary sources

- [Core 2026.9.1 entity-platform lifecycle](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/helpers/entity_platform.py)
- [Core 2026.9.1 Ecobee climate source](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/ecobee/climate.py)
- [Ecobee Runtime temperature semantics](https://www.ecobee.com/home/developer/api/documentation/v1/objects/Runtime.shtml)
- [Core 2026.9.1 HomeKit sensor source](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/sensor.py)
- [Core 2026.9.1 HomeKit entity lifecycle](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/entity.py)
- [Core 2026.9.1 HomeKit Clear Hold](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/button.py)

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
