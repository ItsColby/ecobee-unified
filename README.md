# Ecobee Unified

Ecobee Unified gives each mapped thermostat one Home Assistant climate entity
on its existing HomeKit device. It combines local HomeKit state and standard
controls with selected Ecobee details and vendor actions. Your existing source
integrations continue to own their connections and credentials.

## What you need

- Home Assistant Core **2026.8.0 or later**. The repository maintains exact test
  lanes for Core 2026.8.0 and 2026.9.1; this does not claim every intervening or
  future version has been tested.
- Working climate entities from **HomeKit Device** (`homekit_controller`) and
  **ecobee** (`ecobee`) for the same physical thermostat. Setup verifies their
  device identity; matching names alone are insufficient.
- Optional same-device sources for precise temperature, Current Mode, Clear
  Hold, air-quality estimates, and thermostat-display messages.

Beestat is optional. Its history and derived context remain independently owned
sibling entities; Unified neither acquires Beestat data nor imports history.

## Get started

Follow [installation and setup](docs/setup.md) to install the integration and
select your existing sources. One Ecobee Unified entry contains all thermostat
mappings under **Settings > Devices & services**.

Use the entry's **Reconfigure** action to add, edit, or remove thermostat
mappings. Use **Configure** to change the Ecobee source-staleness and command-
confirmation timing thresholds. Both settings are local to Unified and default
to 30 minutes; they do not change thermostat settings or upstream polling.

## Daily use

Use the Unified climate for supported HVAC mode, temperature, humidity, fan, and
preset controls. Additional entities provide minimum fan runtime, equipment
stage, source health, and the optional actions and sensors you map. See
[entities and actions](docs/usage.md) for the complete surface and examples.

Standard commands always go through HomeKit. Vendor commands always go through
the mapped Ecobee writer. A read fallback never selects a different writer, and
Unified never retries a write automatically. A command can be submitted without
its effect being observable or confirmed.

Keep the source integrations and entities enabled. Unified does not migrate
dashboards, automations, voice exposure, or Recorder data. Choose those changes
in your own Home Assistant configuration after checking their consumers.

## Documentation

| Read | For |
|---|---|
| [Setup](docs/setup.md) | Installation, source selection, mappings, and local options |
| [Entities and actions](docs/usage.md) | Controls, vendor actions, examples, and command results |
| [Troubleshooting](docs/troubleshooting.md) | Mapping failures, unavailable controls, temperature recovery, and diagnostics |
| [Architecture](docs/architecture.md) | Field ownership, runtime lifecycle, privacy, and failure boundaries |
| [Requirements](docs/requirements.md) | Product invariants and acceptance criteria |
| [Design decisions](docs/decisions.md) | Reasons for the product's boundaries and tradeoffs |
| [Development](docs/development.md) | Contributor setup and validation commands |
| [Validation](docs/validation-plan.md) | Automated coverage and manual acceptance limits |
| [Upstream contracts](docs/upstream-contracts.md) | Versioned source references and compatibility assumptions |

Report reproducible problems through the
[issue tracker](https://github.com/ItsColby/ecobee-unified/issues), using the
privacy guidance in the troubleshooting guide. Released versions and original
release notes are on [GitHub Releases](https://github.com/ItsColby/ecobee-unified/releases).

Licensed under the [MIT License](LICENSE).
