# Upstream contracts

Ecobee Unified consumes the public entities, registries, and services of Home
Assistant's `homekit_controller` and `ecobee` integrations. This reference records
the external assumptions a maintainer must recheck when changing compatibility
or source interpretation.

The minimum supported Core is **2026.8.0**; the separate current validation lane
is pinned to **2026.9.1**. “Current” names that repository lane, not an automatically
advancing version. The paired test harnesses and commands are in
[Development](development.md). Source links below are pinned to the minimum;
the corresponding [HomeKit](https://github.com/home-assistant/core/tree/2026.9.1/homeassistant/components/homekit_controller)
and [Ecobee](https://github.com/home-assistant/core/tree/2026.9.1/homeassistant/components/ecobee)
implementations in the current lane must preserve these same contracts.

## Source assumptions and their limits

| Boundary | Upstream contract | Local enforcement or interpretation |
| --- | --- | --- |
| Physical thermostat identity | HomeKit supplies the accessory serial as device `serial_number`; Ecobee identifies its thermostat device with `("ecobee", thermostat["identifier"])`. See [HomeKit connection][hk-connection] and [Ecobee climate][ec-climate]. | [`source_contracts.py`](../custom_components/ecobee_unified/source_contracts.py) requires the normalized serial and the single Ecobee identifier to match. Missing proof and a proven mismatch are different states. Names, areas, and a child device are insufficient identity evidence. |
| Entity references and device linking | Core resolves entity registry references and exposes the source device through [`async_entity_id_to_device`][device-helper]. | Saved registry IDs survive entity renames. Unified entities use the HomeKit source's `device_entry`; linking does not transfer ownership of the source device. Registry events must update the association and invalidate incompatible mappings. |
| Climate temperature semantics | [HomeKit climate][hk-climate] reads the local current-temperature characteristic in Celsius. [Ecobee climate][ec-climate] reads `runtime.actualTemperature / 10.0` in Fahrenheit. Ecobee defines `actualTemperature` as the displayed temperature, which can reflect feels-like processing; `rawTemperature` is a separate dry-bulb field. See the [Runtime object][ec-runtime]. | Conversion follows source units. The cloud fallback preserves the cloud climate's meaning. An explicitly selected local temperature sensor still needs a valid temperature class/unit, physical association, and agreement with the local climate; an upstream characteristic name alone cannot establish equivalence. |
| Climate serialization and setpoints | The [Core climate base][climate-base] converts and rounds serialized temperatures using the entity's precision: the default is tenths in Celsius and whole degrees in Fahrenheit. A single target uses `temperature`; a target range uses `target_temp_low` and `target_temp_high`. | Temperature comparison accounts for serialized precision. Tests must inspect the published HA state as well as Python entity properties. Read precision, writable step, and source units are distinct contracts. |
| HomeKit write granularity | The thermostat adapter does not publish `target_temp_step`, but the pinned writer normalizes writes to the characteristic's advertised bounds and native step. Core pins aiohomekit [4.0.0 for 2026.8.0][hk-min-manifest] and [4.0.1 for 2026.9.1][hk-current-manifest]; both implement this conversion. See [4.0.0][hk-min-conversion] and [4.0.1][hk-current-conversion]. | `HOMEKIT_WRITER_GRANULARITY_PROVEN` records this source contract. Borrowing Ecobee step metadata still requires proven physical identity, matching units, and valid writer bounds. Normalization does not prove that a requested increment is preserved unchanged or that the accessory applied the command. |
| HomeKit Current Mode | The [select implementation][hk-select] exposes `home`, `sleep`, and `away`, with translation key `ecobee_mode`. Selecting an option writes the hold-schedule characteristic; observing Current Mode uses a different characteristic. | The mapped select must be an uncategorized, classless HomeKit entity with compatible options. The validator accepts a nonempty subset of those options and permits a missing translation key. A selected value is confirmed only through the appropriate local observation after writer acceptance. |
| HomeKit Clear Hold | The [button implementation][hk-button] exposes Clear Hold without a positive public role marker. Its press toggles the characteristic, and it monitors no characteristic that confirms the effect. | Explicit selection and same-device association remain necessary. Metadata can reject known incompatible buttons, but cannot prove that any classless button is Clear Hold. Successful submission is reported as `submitted`; it cannot establish schedule resumption. |
| Air-quality quantity and unit | [Ecobee sensors][ec-sensor] expose `actualAQScore` as AQI without a unit, `actualCO2` as CO₂ in ppm, and `actualVOC` as VOC in µg/m³. The capability name `vocPPM` does not define the HA sensor's unit. Ecobee describes its [displayed VOC and CO₂ values as estimates][ec-air-quality]. | `AIR_QUALITY_SENSOR_CONTRACTS` checks class, unit, and a finite nonnegative value. Explicit live metadata takes precedence over registry defaults. Preserve these values as Ecobee estimates; the source does not establish equivalence to a regulatory outdoor AQI. |
| Observation timestamps | [`State.last_reported`][state-reported] tracks reports even when a value does not change. The pinned [event helper][event-helper] provides entity-filtered state-report subscriptions for unchanged reports alongside state-change events. | Freshness and pending-command confirmation must handle unchanged reports. Cloud report age is evidence of HA observation cadence, not the timestamp of a physical measurement. Quiet local HomeKit state alone does not prove staleness. |
| Cloud actions and notifications | [Ecobee climate][ec-climate] owns its vendor service schemas. [Ecobee notify][ec-notify] is a `NotifyEntity` that sends a message to its thermostat; the [notify framework][notify-base] exposes `notify.send_message`. | Route through the explicitly mapped, available source and recheck its registered service. Match the cloud writer's units and bounds. Notification submission has no display-delivery confirmation, and the Ecobee notify entity does not support a title. |

The [Ecobee Runtime reference][ec-runtime] explains temperature meaning, but does
not document the three air-quality runtime keys used by these Core versions.
The pinned sensor implementation is the source for their field mapping and units.

## Cancellation during global setup

In pinned Core [2026.8.0][core-setup-minimum] and
[2026.9.1][core-setup-current], `async_setup_component` retains a failed or
cancelled global setup future. Cancelling an entry while a selected global
component is still initializing can therefore prevent that component and the
entry from being set up again in the same Home Assistant instance. Unified
must release its acquired entry platforms, entities and manager subscriptions;
it does not clear Core's global setup cache. Cancellation after selected global
components finish must still allow a clean native entry reload with a fresh
manager and entities. [`test_setup_lifecycle.py`](../tests/test_setup_lifecycle.py)
separately covers both boundaries, including cleanup after the ordinary
concurrent failed retry.

## Reviewing an upstream change

Review the affected implementation at the proposed Core tag, including any
underlying library behavior on which a writer relies. Preserve the existing
support pins until the new Core/harness pair is intentionally adopted.

Use [`source_contracts.py`](../custom_components/ecobee_unified/source_contracts.py)
for accepted source metadata, [`manager.py`](../custom_components/ecobee_unified/manager.py)
for observation and dispatch, and [`models.py`](../custom_components/ecobee_unified/models.py)
for normalization and confirmation rules. A compatibility change needs a
regression at the affected native boundary: mapping identity or role drift,
serialized temperature, exact writer dispatch, or unchanged-report handling.
Run both support lanes as described in [Development](development.md).

Tagged upstream code establishes an implementation contract; the runtime still
validates the user's installed entities and advertised capabilities. These
references do not establish the behavior of every accessory model, firmware
version, or account. Keep release-specific compatibility findings with their
commit or release record.

[hk-connection]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/homekit_controller/connection.py
[hk-climate]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/homekit_controller/climate.py
[hk-select]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/homekit_controller/select.py
[hk-button]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/homekit_controller/button.py
[hk-min-manifest]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/homekit_controller/manifest.json
[hk-current-manifest]: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/homekit_controller/manifest.json
[hk-min-conversion]: https://raw.githubusercontent.com/Jc2k/aiohomekit/4.0.0/aiohomekit/model/characteristics/characteristic.py
[hk-current-conversion]: https://raw.githubusercontent.com/Jc2k/aiohomekit/4.0.1/aiohomekit/model/characteristics/characteristic.py
[ec-climate]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/ecobee/climate.py
[ec-sensor]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/ecobee/sensor.py
[ec-notify]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/ecobee/notify.py
[ec-runtime]: https://www.ecobee.com/home/developer/api/documentation/v1/objects/Runtime.shtml
[ec-air-quality]: https://www.ecobee.com/en-us/citizen/five-ways-to-improve-air-quality-in-your-home/
[climate-base]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/components/climate/__init__.py
[notify-base]: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/components/notify/__init__.py
[device-helper]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/helpers/device.py
[event-helper]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/helpers/event.py
[state-reported]: https://developers.home-assistant.io/blog/2024/03/20/state_reported_timestamp/

[core-setup-minimum]: https://raw.githubusercontent.com/home-assistant/core/2026.8.0/homeassistant/setup.py
[core-setup-current]: https://raw.githubusercontent.com/home-assistant/core/2026.9.1/homeassistant/setup.py
