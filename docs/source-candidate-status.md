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
| Repository and HA workflows terminal green | Local subset green; hosted gate closed | Forty-five local tests, strict mypy, Ruff, compile, actionlint, dependency closure, and a 38-file working-tree/tracked-archive/history privacy scan pass. Linux pytest, Hassfest, and HACS jobs are defined but cannot be claimed terminal green before an authorized public repository run. |
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

## Home registry handoff

The final product lifecycle stage is `active_unreleased`. The repository has no
configured remote, so `repository.public_url` remains `null`.

Exact support owners for portfolio schema v2:

| Field | Final value |
|---|---|
| `policy` | `installed_only` |
| `hacs_metadata` | `hacs.json` |
| `core_requirements` | `requirements-ha-test.txt` |
| `minimum_core_requirements` | `null` |
| `test_workflow` | `.github/workflows/validate.yaml` |
| `broader_support_reason` | `null` |

All controlled capability dispositions and product-relative evidence paths:

| Capability | Applicability / status | Evidence or reason |
|---|---|---|
| `typed-runtime` | required / observed | `custom_components/ecobee_unified/runtime.py`; `custom_components/ecobee_unified/__init__.py` |
| `config-entry-lifecycle` | required / observed | `custom_components/ecobee_unified/config_flow.py`; `custom_components/ecobee_unified/__init__.py`; `tests/test_runtime_core_api.py` |
| `config-entry-migrations` | required / observed | `custom_components/ecobee_unified/__init__.py`; `tests/test_runtime_core_api.py` |
| `bounded-source-boundary` | required / observed | `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/models.py`; `tests/test_runtime_core_api.py` |
| `normalized-model` | required / observed | `custom_components/ecobee_unified/models.py`; `custom_components/ecobee_unified/manager.py`; `tests/test_models.py` |
| `dynamic-discovery` | not applicable / not applicable | `docs/architecture.md`; thermostat mappings are explicitly selected rather than discovered. |
| `helper-device-linking` | required / observed | `custom_components/ecobee_unified/climate.py`; `tests/test_runtime_core_api.py` |
| `recorder-import` | not applicable / not applicable | `docs/architecture.md`; historical import remains owned by Beestat Statistics. |
| `diagnostics-privacy` | required / observed | `custom_components/ecobee_unified/diagnostics.py`; `scripts/check_public_safety.py`; `tests/test_public_safety.py`; `tests/test_runtime_core_api.py` |
| `repairs` | required / observed | `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/strings.json`; `tests/test_runtime_core_api.py` |
| `reauth` | not applicable / not applicable | `docs/architecture.md`; the helper consumes Home Assistant-owned sources and stores no upstream credential. |
| `account-continuity` | not applicable / not applicable | `docs/architecture.md`; the helper owns mappings rather than an authenticated upstream account. |
| `single-writer-actions` | required / observed | `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/climate.py`; `tests/test_commands.py`; `tests/test_runtime_core_api.py` |
| `response-producing-actions` | not applicable / not applicable | `docs/architecture.md`; the integration routes climate commands and does not own delivered content. |
| `capability-route` | not applicable / not applicable | `docs/architecture.md`; the integration exposes no unauthenticated subscription or capability route. |
| `health-projection` | required / observed | `custom_components/ecobee_unified/models.py`; `custom_components/ecobee_unified/climate.py`; `custom_components/ecobee_unified/diagnostics.py`; `tests/test_models.py`; `tests/test_runtime_core_api.py` |
| `temporary-artifacts` | not applicable / not applicable | `docs/architecture.md`; the integration runtime produces no temporary files or attachments. |
| `dependency-closure` | required / observed | `.github/workflows/validate.yaml`; `requirements-ha-test.txt`; `tests/test_public_safety.py` |
| `installed-core-test` | required / observed | `hacs.json`; `requirements-ha-test.txt`; `.github/workflows/validate.yaml`; `tests/test_runtime_core_api.py`; `docs/upstream-contracts.md` |

A read-only in-memory audit of this exact proposed registry declaration passes
all nine implemented-product checks with zero failures, warnings, or
unavailable results. Home remains the owner of applying and committing the
registry transition.

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
- **Shared contract absorbed during implementation:** elapsed-time-dependent
  availability, fallback, or degradation needs bounded coordinator- or
  lifecycle-owned reevaluation plus a no-new-source-event test.
- **Shared contract absorbed during implementation:** dependency-light tiers
  must collect genuinely dependency-light tests; modules that import Home
  Assistant belong in the exact installed-Core HA lane.
- **Existing shared clauses were sufficient:** the late awaited-failure revision
  race is covered by command lifecycle rules, and temporarily missing saved
  selections are covered by config-entry lifecycle rules. They required product
  fixes and tests, not new capability IDs or another shared-contract change.

## Closed gates

Still closed: public repository/remote creation, push/publication, manifest
release-version advancement, tag, GitHub Release, HACS install/update, Home
Assistant config check, reload/restart, private instance mapping, shadow/live
validation, consumer migration, outbound effects, rollback, and Home
control-plane edits.
