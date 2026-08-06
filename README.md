# Ecobee Unified

Ecobee Unified is a design-stage Home Assistant custom integration that will
combine complementary data already exposed by HomeKit Controller, the native
Ecobee integration, and an optional Beestat-derived integration into one
canonical thermostat entity per physical thermostat.

It will not replace those integrations or connect to Ecobee directly. The
source integrations remain the owners of transport, authentication, devices,
and raw entities. Ecobee Unified will provide a deterministic presentation and
control-policy layer:

- local, responsive climate state and standard controls from HomeKit;
- Ecobee-specific operating detail and actions from the Ecobee integration;
- optional schedule/history-derived context from Beestat data;
- explicit source health, provenance, and degradation; and
- one canonical entity for dashboards, automations, and voice exposure.

The repository currently contains the implementation contract and validation
plan, not integration code. Start with [the architecture](docs/architecture.md)
and [requirements](docs/requirements.md).

## Non-goals

- A new Ecobee cloud client or credential store.
- A replacement for HomeKit Controller, Ecobee, or Beestat history entities.
- Duplicate weather, motion, occupancy, battery, air-quality, or historical
  entities without a new canonical semantic.
- Averaging duplicate measurements or switching sources only because one has a
  newer timestamp.
- Sending the same command through multiple backends.

## Status

Design ready for implementation. No release or installation is implied.
