# Upstream contracts

Ecobee Unified consumes Home Assistant entities and actions. The source
integrations own pairing, authentication, transport, and backend behavior.
This reference records versioned upstream contracts that affect Unified's
design; [architecture](architecture.md) owns Unified's selection, validation,
command, and recovery rules.

The repository maintains Core 2026.8.0 and 2026.9.1 test lanes. The source links
below use Core 2026.9.1 unless stated otherwise; they describe that version,
not a promise about later releases or every accessory. See
[development](development.md#compatibility-owners) before changing support.

## Devices and source identity

Since Core 2026.8, a helper must link its entities to a source device rather
than attach its config entry to a device owned by another integration. The
documented pattern assigns `device_entry` using `async_entity_id_to_device`;
copying the foreign device's identifiers/connections is not equivalent.
Unified follows this model and owns reconciliation of its helper links.
[Core device-ownership change](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/),
[helper linking guidance](https://developers.home-assistant.io/blog/2025/07/18/updated-pattern-for-helpers-linking-to-devices/).

HomeKit publishes an accessory serial number in device information. Ecobee
publishes a thermostat identifier in its device identifier pair. Those fields
provide the inputs for Unified's same-thermostat comparison; similar names or
membership in one account are not identity proof.
[HomeKit device information](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/connection.py),
[Ecobee climate device information](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/ecobee/climate.py).

Unified's manifest does not declare the source integrations as dependencies or
after-dependencies, and it imports no source integration client. Source setup
and recovery remain independent; configured registry references, states, and
registered services determine what Unified can use.
[Unified manifest](../custom_components/ecobee_unified/manifest.json),
[source contracts](../custom_components/ecobee_unified/source_contracts.py).

## Temperature and humidity

Core serializes climate temperatures into Home Assistant's configured
temperature unit and applies climate display precision. A climate state does
not expose the sensor-style `unit_of_measurement` attribute. Unified therefore
interprets climate values using the configured unit, while validating and
converting explicitly selected sensor units separately.
[Core climate serialization](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/climate/__init__.py).

The HomeKit thermostat entity reads current temperature from its current
service. A secondary characteristic sensor retains a characteristic object and
reads its value. The projections need not have equal display precision or an
identical update path. These implementation details do not prove which reported
value is correct; Unified's agreement and recovery checks do not repair source
acquisition.
[HomeKit climate](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/climate.py),
[HomeKit secondary sensors](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/sensor.py),
[characteristic entity lifecycle](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/entity.py).

The HomeKit Heater/Cooler entity exposes a target-temperature step; the separate
thermostat-service `HomeKitClimateEntity` does not implement that property.
The latter exposes target humidity and characteristic-derived bounds when
supported, but no target-humidity step. Unified retains native writer metadata,
allows only its documented same-device temperature-step exception, and leaves
unsupported humidity granularity unset.
[HomeKit climate implementations](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/climate.py),
[Unified model rules](../custom_components/ecobee_unified/models.py).

Ecobee climate reads `runtime.actualTemperature`. Ecobee defines this as the
thermostat's displayed temperature; `rawTemperature` is the dry-bulb value, and
humidex mode can make `actualTemperature` a feels-like reading. An Ecobee climate
fallback therefore does not independently corroborate dry-bulb temperature.
Unified does not substitute a similarly named raw source.
[Ecobee climate](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/ecobee/climate.py),
[Ecobee Runtime semantics](https://www.ecobee.com/home/developer/api/documentation/v1/objects/Runtime.shtml).

## Actions and observed outcomes

HomeKit exposes Ecobee Current Mode through a select with Home/Sleep/Away
semantics, and Clear Hold through a separate button. Clear Hold's Core handler
writes false and then true to its characteristic. Unified's single-dispatch
contract counts its call to the mapped Home Assistant writer; it does not
promise a single backend packet or protocol write.
[Current Mode select](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/select.py),
[Clear Hold button](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/homekit_controller/button.py).

Core Ecobee provides vendor actions for minimum fan runtime, vacations,
occupancy modes, and comfort-profile sensor participation. Vacation temperatures
are converted from Home Assistant's configured unit to Fahrenheit before the
backend call. Unified's public schemas and source validation constrain those
actions further; source API availability alone does not enable every action.
[Ecobee climate actions](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/ecobee/climate.py),
[Unified actions](../custom_components/ecobee_unified/services.yaml).

The Ecobee notification entity sends the message to the backend and ignores
the optional title. That return does not verify thermostat display delivery.
Unified's notification adapter preserves the native service success/failure
result. Notifications are outside Unified command tracking and have no
`submitted` or `confirmed` status.
[Ecobee notification entity](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/ecobee/notify.py),
[Unified notification adapter](../custom_components/ecobee_unified/notify.py).

Writer completion is not physical-effect proof. Unified defines its own
operation-specific observers and leaves unobservable effects submitted.
Likewise, stopping Unified cannot retract work already accepted by a source.
Its command lifecycle prevents queued writes and late results from reviving
the stopped manager; it does not claim source-side cancellation.
[Command lifecycle tests](../tests/test_command_lifecycle.py).

## Reports, freshness, and historical context

Core updates `State.last_reported` and emits `state_reported` when a state write
leaves state and attributes unchanged. The event carries a stable report time,
while the state object itself can be updated by later reports. Unified uses
that event time for relevant freshness and command observations; HomeKit push
silence is not treated as a missing periodic heartbeat.
[Core state reporting](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/core.py),
[Unified report handling](../custom_components/ecobee_unified/manager.py).

Ecobee climate exposes `equipment_running` from `equipmentStatus`, alongside
vendor settings and sensor participation. Unified interprets these fields in a
bounded local projection; it does not acquire a second history stream.
[Ecobee climate attributes](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/ecobee/climate.py).

Beestat, when installed, remains an independent owner of cloud history and
derived context. Unified has no Beestat client, credentials, polling loop,
Recorder import, or command fallback. Sharing a device presentation does not
transfer those responsibilities. This is Unified's product boundary, not a
statement about another integration's availability or accuracy.
[Unified runtime](../custom_components/ecobee_unified/runtime.py),
[architecture](architecture.md).
