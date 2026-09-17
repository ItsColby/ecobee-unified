# Ecobee Unified

Use a single thermostat control in Home Assistant while keeping HomeKit's local
connection and Ecobee's vendor features. Ecobee Unified connects existing source
entities to a shared thermostat view; it does not establish either connection.

A mapping creates a Unified climate on the HomeKit thermostat device. It also
provides equipment stage, minimum fan runtime, and source-health information.
You can opt into Current Mode presets, Clear Hold, a finer temperature source,
air-quality readings, and display messages when the source entities exist.
You can also centralize equivalent room-sensor and contextual readings through
explicit datapoint mappings, with ordered sources, optional fallback, and source
and observation-time information. Thermostat read preferences are configurable
per field and per mapping.
Native Ecobee weather aliases can share one weather entity, including daily
forecasts, while keeping their station, units and report timing explicit.
Configured historical families provide an on-demand daily report from existing
Recorder statistics, with explicit source policy, aggregation methods and
coverage. A [native script](examples/ecobee_daily_history_report.yaml) returns
the same response for use by other scripts.

## Is it suitable for my installation?

You need Home Assistant Core **2026.8.0 or later**, plus a working HomeKit Device
climate and Ecobee climate for each thermostat. Their registry identities must
match the same physical thermostat. Unified accepts no account credentials and
cannot replace an unavailable source integration.

HomeKit is the writer for standard controls. Ecobee is the writer for the
supported vendor settings and actions. Read fallback can keep information
visible, but it never changes that assignment. A successful service call is not
always enough to confirm its effect.

Beestat is optional and independent: Unified can select its existing Home
Assistant entities as read-only datapoint sources. Beestat keeps acquisition,
history, statistics imports and forecasts. Battery Notes can supply an
equivalent battery reading on the same sensor; a mirror is not an independent
measurement. Household schedules and cross-service automations stay in your
Home Assistant configuration.

## Start here

The [operating guide](docs/user-guide.md) takes you through installation, source
selection, everyday controls, action results, and problem diagnosis. It also
explains which settings affect Unified itself and which actions affect a
thermostat.

For implementation and maintenance:

- [Runtime contract](docs/architecture.md): how inputs become entities and how
  commands, faults, recovery, and privacy work.
- [Contributor guide](docs/development.md): supported test environments,
  executable checks, and the limits of their evidence.
- [Upstream reference](docs/upstream-contracts.md): external APIs and source
  behavior on which this implementation relies.

Downloads and original release notes are available on
[GitHub Releases](https://github.com/ItsColby/ecobee-unified/releases).
Report a reproducible problem through the
[issue tracker](https://github.com/ItsColby/ecobee-unified/issues), following the
[diagnostic-sharing guidance](docs/user-guide.md#collect-a-useful-problem-report).

The project uses the [MIT License](LICENSE).
