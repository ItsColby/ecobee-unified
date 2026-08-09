# Upstream Opportunities

These observations are separate from the Ecobee Unified product contract. They
record possible Home Assistant Core improvements without selecting, scheduling,
or authorizing upstream work. Ecobee Unified does not depend on them.

| Opportunity | Current product mitigation | Status |
|---|---|---|
| Preserve the Ecobee HomeKit accessory's available current-temperature precision consistently across the HomeKit climate and same-service temperature-sensor projections, including updates. | Unified uses an explicitly mapped same-device sensor only while its finite, unit-normalized value agrees with the local climate's serialization envelope; otherwise it degrades to the climate reading. | Undecided upstream opportunity; no Core change requested. |
| Expose the HomeKit thermostat service's native target-temperature characteristic step through `HomeKitClimateEntity.target_temperature_step`, as the separate HomeKit Heater/Cooler entity already does. | Unified uses the explicitly mapped, physically proven Ecobee climate's step only as static presentation metadata when the HomeKit thermostat state omits it; HomeKit retains units, bounds, state, and every standard write. | Undecided upstream opportunity; no Core change requested. |
| Expose the HomeKit thermostat writer's native target-humidity step through the standard climate `target_humidity_step` property. | Unified leaves the step unset because the supported HomeKit entity contract exposes bounds and capability but not the writer's humidity granularity. | Undecided upstream opportunity; no Core change requested. |
| In Ecobee climate auto mode, use `runtime.desiredHeat` when the caller omits the heat setpoint. Core 2026.8.1's `set_auto_temp_hold` currently defaults both omitted paths from `desiredCool`. | Unified sends standard temperature/range writes only through HomeKit and therefore does not exercise this Ecobee path. | Candidate Core defect for separate verification and maintainer disposition; no issue or patch authorized here. |

Before any future upstream action, reproduce against current Core `main`, add a
focused regression test in the upstream owner, and follow Home Assistant's
contribution process. Product release, live deployment, and consumer migration
must not wait on these optional opportunities.
