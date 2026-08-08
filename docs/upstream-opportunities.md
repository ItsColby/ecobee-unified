# Upstream Opportunities

These observations are separate from the Ecobee Unified product contract. They
record possible Home Assistant Core improvements without selecting, scheduling,
or authorizing upstream work. The Unified candidate does not depend on them.

| Opportunity | Current product mitigation | Status |
|---|---|---|
| Preserve the Ecobee HomeKit accessory's available current-temperature precision in the HomeKit climate projection, or expose enough native precision metadata for Core to do so consistently. | Unified explicitly maps a same-device HomeKit temperature sensor and advertises tenths precision while projecting its finite, unit-normalized value through the canonical climate. | Undecided upstream opportunity; no Core change requested. |
| Expose the HomeKit thermostat writer's native target-temperature step through `target_temp_step`. | Unified permits a narrowly proven same-device Ecobee metadata fusion only when the HomeKit adapter omits the step; HomeKit retains units, bounds, and the sole writer. | Undecided upstream opportunity; no Core change requested. |
| Expose the HomeKit thermostat writer's native target-humidity step through the standard climate `target_humidity_step` property. | Unified leaves the step unset because the supported HomeKit entity contract exposes bounds and capability but not the writer's humidity granularity. | Undecided upstream opportunity; no Core change requested. |
| In Ecobee climate auto mode, use `runtime.desiredHeat` when the caller omits the heat setpoint. Core 2026.8.1's `set_auto_temp_hold` currently defaults both omitted paths from `desiredCool`. | Unified sends standard temperature/range writes only through HomeKit and therefore does not exercise this Ecobee path. | Candidate Core defect for separate verification and maintainer disposition; no issue or patch authorized here. |

Before any future upstream action, reproduce against current Core `main`, add a
focused regression test in the upstream owner, and follow Home Assistant's
contribution process. Product release, live deployment, and consumer migration
must not wait on these optional opportunities.
