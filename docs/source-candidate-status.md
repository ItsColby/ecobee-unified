# Validated Source Candidate Status

Date: 2026-08-07

Ecobee Unified is implemented as an unreleased, public-safe local source
candidate for Home Assistant Core 2026.8.0. This status does not authorize or
claim publication, release, installation, reload/restart, live validation,
consumer migration, outbound effects, or rollback.

## Implemented product surface

- One typed config-entry runtime owns multiple explicit thermostat mappings.
- Config and reconfigure flows use entity selectors, store registry IDs, reject
  invalid platforms and duplicate sources, preserve temporarily missing saved
  references, and require confirmation before physical-association or writer
  changes.
- One normalized immutable snapshot per mapping owns field selection,
  provenance, source age/health, capability-aware degradation, optional
  Beestat context, and recent command status.
- One climate entity per mapping projects the snapshot without property I/O and
  links to the selected HomeKit thermostat device using Core 2026.8's
  `device_entry` helper pattern.
- Standard climate commands call only the mapped HomeKit entity. Public Ecobee
  `resume_program` and `set_fan_min_on_time` actions call only the mapped
  Ecobee entity. No path retries or fails over to a second writer.
- Confirmation observes normalized Ecobee state, uses monotonically increasing
  revisions, and guards observation, timeout, and failure updates against
  superseded commands.
- Diagnostics are allow-listed and mapping-indexed. Repairs cover removed
  required registry sources and missing HomeKit device links, and clear when
  valid references recover.
- The repository includes generic fixtures/tests, strict typing and Ruff
  policy, exact Core requirements, public-safety and history scanning, Hassfest
  and HACS jobs, least workflow permissions, bounded timeouts/concurrency,
  non-persistent checkout credentials, and a terminal release gate.

## MVP acceptance disposition

| Item | Disposition | Evidence |
|---|---|---|
| Multiple mappings in one entry | Proven locally | Real Core flow manager creates two registry-ID mappings in one entry. |
| Deterministic standard fields and fallback | Proven locally | Table-driven pure tests cover every standard field, unequal values, missing fields, stale/unavailable sources, no averaging, and semantic current temperature. |
| Exactly one HomeKit call per standard command | Proven locally | Real Core service registry observes one call and no Ecobee call; full climate method matrix is in the Linux HA test suite. |
| Exactly one Ecobee call per vendor action | Proven locally | Real Core service registry proves one `resume_program` and one `set_fan_min_on_time` call. |
| Observation never retries | Proven locally | Command tracker and service-registry tests prove confirmation is read-only and timeouts issue no service call. |
| Reload, rename, loss/recovery, removal | Proven locally | Real Core config-entry and registry/state tests prove unload/reload with stable ID, registry rename, capability loss/recovery, removal Repair creation, preserved missing selection, and Repair deletion after restoration. |
| Correct Core 2026.8 device linkage | Proven locally | Real Core device/entity registry test verifies `device_entry` linkage and no foreign `device_info`. |
| Useful, privacy-redacted diagnostics | Proven locally | Real Core diagnostics test plus full-tree/history public-safety scan. |
| Repository and HA workflows terminal green | Local subset green; hosted gate closed | Twenty-six local tests, strict mypy, Ruff, compile, JSON/YAML parsing, actionlint/ShellCheck integration, dependency closure, and a 38-file/history privacy scan pass. Linux pytest, Hassfest, and HACS jobs are defined but cannot be claimed terminal green before an authorized public repository run. |
| Private shadow soak before migration | Deferred live gate | Requires separately authorized installation and at least seven days of private evidence. |
| Late observation cannot mutate newer command | Proven locally | Revision supersession tests cover observation, timeout, failure, and confirmation source selection. |

## Validation boundary

The isolated environment was installed in final order with
`pytest-homeassistant-custom-component==0.13.354`, then
`homeassistant==2026.8.0`, then the product-owned typing tool. Final
`python -m pip check` passes. The Windows host cannot import the full Home
Assistant pytest plugin because Core imports POSIX `fcntl`; Docker is absent
and WSL has no installed distribution. The real Core API tests therefore run
without that plugin, while the complete pytest suite remains Linux/CI-owned.

The read-only portfolio auditor passes four checks and reports one expected
`lifecycle-stage-consistency` failure: its Home-owned registry still classifies
Ecobee Unified as `design_only`, while the product repository now correctly
contains an implemented manifest. `implemented-support` remains not applicable
under that stale declaration; there are no warnings or unavailable checks. The
coordinator should separately update the Home registry to `active_unreleased`,
installed-Core support metadata, and observed capability evidence, then rerun
the auditor. This task did not auto-apply the finding or edit Home.

## Learning classification

- **Portfolio invariant already present:** do not make independent sources
  transport dependencies; use supported state/registry/service interfaces.
- **Product-specific verified consequence:** even `after_dependencies` caused
  Core to process Ecobee/HomeKit package requirements during helper-flow load,
  so Ecobee Unified declares no source dependencies and relies on explicit
  late-recovering registry/state subscriptions.
- **Product-specific verified consequence:** Core 2026.8 helper-device linkage
  uses `device_entry`; foreign identifiers/connections and cross-entry device
  ownership are prohibited.
- **No shared-contract change proposed:** the observations instantiate existing
  source-boundary and helper-device-linking rules rather than changing them.

## Closed gates

Still closed: public repository/remote creation, push/publication, manifest
release-version advancement, tag, GitHub Release, HACS install/update, Home
Assistant config check, reload/restart, private instance mapping, shadow/live
validation, consumer migration, outbound effects, rollback, and Home
control-plane edits.
