# Installation and setup

## Prepare the source integrations

Ecobee Unified requires Home Assistant Core 2026.8.0 or later and an existing
HomeKit climate and Ecobee climate for each thermostat. Configure and verify
those integrations first:

- [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/)
  supplies the local connection. Its integration domain is `homekit_controller`;
  HomeKit Bridge is a different integration.
- [ecobee](https://www.home-assistant.io/integrations/ecobee/) supplies the vendor
  connection. Its own setup and reauthentication flow owns account access.

Unified has no login, API key, pairing code, or direct cloud client. It cannot
repair either source connection. Beestat is not required for setup or control.

## Install

With HACS, use its
[custom repositories control](https://hacs.xyz/docs/use/repositories/dashboard/)
to add `https://github.com/ItsColby/ecobee-unified` with type **Integration**,
then download Ecobee Unified. Follow the installation's reload/restart prompt
before adding the integration.

For a manual installation, obtain the desired
[release](https://github.com/ItsColby/ecobee-unified/releases) and copy its
`custom_components/ecobee_unified` directory into your Home Assistant
configuration's `custom_components` directory. The result must contain
`custom_components/ecobee_unified/manifest.json`. Restart Home Assistant to load
the installed code. Do not copy contributor environments or test files into the
integration directory.

Open **Settings > Devices & services > Add integration**, select **Ecobee
Unified**, and add the mappings below. Configuration is through the native flow,
not YAML. If the entry already exists, manage that entry instead of adding a
second one.

## Select one thermostat's sources

Give the mapping a unique, nonblank name of at most 64 characters. Select the
HomeKit and Ecobee climate entities for the same thermostat. Setup requires
supported registry identity: the HomeKit device's thermostat serial must agree
with the Ecobee device identifier. It does not infer identity from names, rooms,
or nearby readings. A climate source cannot be reused by another mapping.

Optional sources must belong to the same backend device as the corresponding
climate. Leave unavailable capabilities unmapped; you can add them later.

| Optional field | Select | Effect |
|---|---|---|
| HomeKit Current Mode | A HomeKit select advertising only Home, Sleep, or Away options | Adds the advertised preset choices to the Unified climate |
| HomeKit Clear Hold | The thermostat's actual Clear Hold button | Adds Resume program; independent of Current Mode |
| HomeKit current temperature | A temperature sensor on the mapped HomeKit device with supported temperature units | Supplies finer current-temperature detail when the quality checks permit it |
| Ecobee display notification | The thermostat's Ecobee notify entity | Adds a Unified display-message entity |
| Ecobee AQI | An AQI sensor, without a physical unit | Adds an AQI projection |
| Ecobee CO2 | A CO2 sensor reporting ppm | Adds a CO2 projection |
| Ecobee VOC | A VOC sensor reporting µg/m³ | Adds a VOC projection |

The source registry cannot positively identify every Clear Hold button. Check
the source entity's actual function before selecting it: known administrative
or diagnostic buttons are rejected, but an eligible button is not proof that it
clears a hold. Do not press an unfamiliar button to discover its purpose.

One optional entity cannot fill multiple roles or be reused across mappings.
Sensor metadata must match the selected role; Unified does not convert unrelated
air-quality quantities into the requested measure. A mapped temperature sensor
is subject to the [temperature recovery rules](troubleshooting.md#temperature-detail-disappears).

Select **Add another thermostat** while setting up multiple mappings. Each
mapping creates a Unified climate and related entities on the existing HomeKit
device. Unified does not merge the two upstream config entries, move source
entities, or attach Beestat entities itself.

## Change mappings

Open the entry's **Reconfigure** action to add, edit, or remove mappings. Use
**Save mapping changes** to commit the collected changes. A changed writer selection
requires explicit confirmation; removal also requires confirmation. At least
one mapping must remain. To remove the final mapping, remove the Unified
integration entry instead.

Mappings store entity-registry references, so ordinary source entity renames
survive. A removed and recreated source may have a new registry identity even
if its visible entity ID is unchanged. Review and replace that selection
explicitly. Existing unresolved references are retained when possible, allowing
you to repair one mapping without guessing replacements for another.

A second configuration session cannot overwrite changes saved after the first
session opened. If the flow reports that configuration changed, reopen it and
review the saved state. Saving mapping or option changes reloads this integration
entry; routine reconfiguration does not require a Home Assistant restart.

## Local timing options

Use **Configure** on the entry for these options. Enter whole seconds aligned to
the stated step.

| Option | Default | Allowed values | Meaning |
|---|---|---|---|
| Ecobee source-staleness threshold | 1800 seconds | 300–7200, in 60-second steps | Maximum age of a usable Ecobee source report before Unified treats that source as stale |
| Command-confirmation timeout | 1800 seconds | 300–1800, in 30-second steps | How long a successfully dispatched, observable command may wait for matching source evidence |

These are Unified settings. They do not set the upstream polling interval,
change a thermostat hold duration, trigger a refresh, or schedule a retry.
HomeKit event silence is not treated as a stale-source failure. An unchanged
source value can still have a fresh report. Keep the defaults unless your
observed source behavior justifies changing them.

## Adopt or remove the Unified surface

Check the [entity and action reference](usage.md), then update your own dashboard,
automation, script, and voice consumers as needed. Keep raw sources enabled for
acquisition and recovery. Hiding a source from a dashboard or voice assistant is
different from disabling it.

Removing Ecobee Unified removes its entry and projections; it does not replace or
uninstall HomeKit, Ecobee, or Beestat. Repoint consumers before removal. Upstream
settings previously changed by an action are not undone by removing Unified.
