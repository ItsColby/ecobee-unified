# Upstream Contract Refresh

Verified for the initial Home Assistant Core 2026.8.0 source candidate on
2026-08-07. These are implementation inputs, not proof of live deployment.

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
5. **Compatibility lane:** Core 2026.8.0 is the sole initial support and test
   lane. There is no broader-support requirement.
6. **Source-device lifecycle:** Core 2026.8's helper lifecycle updates helper
   entity registry links when the selected source entity's device association
   changes. Ecobee Unified applies supported entity/device registry listeners
   to reconcile all owned helper records in place across moves, detachments,
   removals, and restorations, preserving stable config-entry/entity identity.
7. **Unchanged source reports:** Core advances `State.last_reported` and emits
   `EVENT_STATE_REPORTED` when an entity reports unchanged state and attributes.
   Source health therefore ages from `last_reported`, report handlers use the
   event-owned stable timestamp rather than the mutable `State` field, and
   matching Ecobee reports can confirm only the current pending command revision.

## Still deployment- or publication-gated

- Source-staleness and command-confirmation defaults remain documented,
  configurable starting values until private shadow evidence measures actual
  cadence.
- The proposed public repository metadata must be rechecked before any public
  repository is created.
- Hosted Hassfest/HACS/CI, installation, restart, live acceptance, consumer
  migration, and release remain separate gates.

## Primary sources

- [Core 2026.8 device ownership and helper-linking change](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)
- [Helper integrations linking to source devices](https://developers.home-assistant.io/blog/2025/07/18/updated-pattern-for-helpers-linking-to-devices/)
- [Core 2026.8.0 Ecobee climate source](https://github.com/home-assistant/core/blob/2026.8.0/homeassistant/components/ecobee/climate.py)
- [Core 2026.8.0 Ecobee action schema](https://github.com/home-assistant/core/blob/2026.8.0/homeassistant/components/ecobee/services.yaml)
- [Core 2026.8.0 HomeKit Controller climate source](https://github.com/home-assistant/core/blob/2026.8.0/homeassistant/components/homekit_controller/climate.py)
- [Home Assistant config flows and migrations](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant diagnostics](https://developers.home-assistant.io/docs/core/integration/diagnostics/)
- [Home Assistant Repairs](https://developers.home-assistant.io/docs/core/platform/repairs/)
- [Home Assistant integration quality rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
- [HACS integration repository requirements](https://hacs.xyz/docs/publish/integration/)
