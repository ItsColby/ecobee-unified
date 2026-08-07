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
  provenance, source age/health, capability-aware degradation, explicit local
  preset plus vendor-only projections, and recent command status. Source freshness uses Core's
  `last_reported` semantics and lifecycle-owned stale-boundary reevaluation.
- One climate, minimum-fan number, bounded equipment-stage sensor, and optional
  AQI/CO2/VOC sensors project the snapshot without property I/O and link to the
  selected HomeKit thermostat device. Registry reconciliation follows source
  move/detach/removal/restoration in place without config-entry reload.
- Standard climate, preset, and clear-hold commands call only their explicitly
  mapped HomeKit entities. The minimum-fan number calls only Ecobee
  `set_fan_min_on_time`. No path retries or fails over to a second writer.
- Beestat retains first-class schedule/transition/filter/alert entities plus
  history import and Recorder ownership; Ecobee Unified does not duplicate
  schedule/transition or vendor sibling state in climate attributes.
- Confirmation observes normalized Ecobee state, uses monotonically increasing
  revisions, and guards observation, timeout, and failure updates against
  superseded commands. A fresh matching unchanged Ecobee report can confirm
  only the current pending revision.
- Diagnostics are allow-listed and mapping-indexed. Repairs cover removed
  required registry sources and missing HomeKit device links, and clear when
  valid references recover.
- The repository includes generic fixtures/tests, strict typing and Ruff
  policy, exact Core requirements, complete reachable-blob public-safety and
  history scanning, Hassfest and HACS jobs, least workflow permissions, bounded
  timeouts/concurrency, non-persistent checkout credentials, and a terminal
  release gate. Volatile source-age, active-sensor, and command-confirmation
  attributes are excluded from Recorder.

## MVP acceptance disposition

| Item | Disposition | Evidence |
|---|---|---|
| Multiple mappings in one entry | Proven locally | Real Core flow manager creates two registry-ID mappings in one entry. |
| Deterministic standard fields and fallback | Proven locally | Table-driven pure tests cover every standard field, unequal values, missing fields, stale/unavailable sources, no averaging, and semantic current temperature. |
| Canonical device surface without duplicate semantics | Proven locally | Config/model/entity tests cover preset capability, local clear hold, minimum fan number, bounded equipment stage, optional AQI/CO2/VOC, and linking for all created platforms; schedule/transition remain first-class Beestat entities. |
| Exactly one HomeKit call per standard command | Proven locally | Real Core service registry observes one call and no Ecobee call; full climate method matrix is in the Linux HA test suite. |
| Exactly one local/vendor call per specialized action | Proven locally | Real Core service registry proves one HomeKit select, one HomeKit clear-hold button press, and one Ecobee `set_fan_min_on_time` call. |
| Observation never retries | Proven locally | Command tracker and service-registry tests prove confirmation is read-only and timeouts issue no service call. |
| Reload, rename, loss/recovery, removal | Proven locally | Real Core config-entry and registry/state tests prove unload/reload with stable ID, registry rename, capability loss/recovery, source removal/restoration, preserved missing selection, and Repair deletion after restoration. |
| Correct Core 2026.8 device linkage | Proven locally | Real Core device/entity registry tests verify initial `device_entry`, in-place move/detach/remove/restore relinking, stable config/entity identity, and no foreign `device_info`. |
| Useful, privacy-redacted diagnostics | Proven locally | Real Core diagnostics test plus working-tree/archive and commit-metadata/filename/reachable-blob public-safety scans. |
| Recorder/presentation hygiene | Proven locally | Climate tests and source inspection prove volatile ages are unrecorded and schedule/transition, equipment stage, and minimum-fan state are absent from climate attributes. |
| Repository and HA workflows terminal green | Local subset green; hosted gate closed | Fifty-three local tests, strict mypy, Ruff, compile, actionlint, dependency closure, and a 43-file working-tree/38-file tracked-archive plus complete reachable-history privacy scan pass. Linux pytest, Hassfest, and HACS jobs are defined but cannot be claimed terminal green before an authorized public repository run. |
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

The historical Home-owned handoff was integrated and privately pushed at Home
commit `492e00102b76f2f5dd743fcc9ee58bcc34877374`. At that receipt, the registry
classified Ecobee Unified as `active_unreleased` with the installed-Core
support owners and all 19 capability dispositions below; the then-current
portfolio auditor passed 27 checks and the published skill passed source/runtime
parity. That is preserved as historical provenance, not a current portfolio
green claim. The later shared-contract convergence identified a Beestat
`helper-device-linking` gap. This convergence batch implements its product
remediation in the separately owned Beestat repository; the Home coordinator
still owns registry disposition, so the historical portfolio-green statement
is not promoted here.

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
| `helper-device-linking` | required / observed | `custom_components/ecobee_unified/entity.py`; `custom_components/ecobee_unified/manager.py`; `tests/test_runtime_core_api.py` |
| `recorder-import` | not applicable / not applicable | `docs/architecture.md`; historical import remains owned by Beestat Statistics. |
| `diagnostics-privacy` | required / observed | `custom_components/ecobee_unified/diagnostics.py`; `scripts/check_public_safety.py`; `tests/test_public_safety.py`; `tests/test_runtime_core_api.py` |
| `repairs` | required / observed | `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/strings.json`; `tests/test_runtime_core_api.py` |
| `reauth` | not applicable / not applicable | `docs/architecture.md`; the helper consumes Home Assistant-owned sources and stores no upstream credential. |
| `account-continuity` | not applicable / not applicable | `docs/architecture.md`; the helper owns mappings rather than an authenticated upstream account. |
| `single-writer-actions` | required / observed | `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/climate.py`; `custom_components/ecobee_unified/number.py`; `tests/test_commands.py`; `tests/test_runtime_core_api.py` |
| `response-producing-actions` | not applicable / not applicable | `docs/architecture.md`; the integration routes climate commands and does not own delivered content. |
| `capability-route` | not applicable / not applicable | `docs/architecture.md`; the integration exposes no unauthenticated subscription or capability route. |
| `health-projection` | required / observed | `custom_components/ecobee_unified/models.py`; `custom_components/ecobee_unified/climate.py`; `custom_components/ecobee_unified/diagnostics.py`; `tests/test_models.py`; `tests/test_runtime_core_api.py` |
| `temporary-artifacts` | not applicable / not applicable | `docs/architecture.md`; the integration runtime produces no temporary files or attachments. |
| `dependency-closure` | required / observed | `.github/workflows/validate.yaml`; `requirements-ha-test.txt`; `tests/test_public_safety.py` |
| `installed-core-test` | required / observed | `hacs.json`; `requirements-ha-test.txt`; `.github/workflows/validate.yaml`; `tests/test_runtime_core_api.py`; `docs/upstream-contracts.md` |

A read-only in-memory audit of this exact proposed registry declaration passes
all nine implemented-product checks with zero failures, warnings, or
unavailable results. Home subsequently applied that exact declaration in the
integrated control-plane commit identified above.

The current owner-runtime portfolio audit at Home commit
`a06c8548fea8c7599e64b822c6c4493cf32460d1` reports 27 passes and one failure,
with zero warnings or unavailable checks. All nine Ecobee checks pass; the sole
portfolio failure is the separately owned Beestat `helper-device-linking` gap.

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
- **Conditional shared pattern, existing capability:** a helper attached to a
  foreign source device must react when that source moves, detaches, disappears,
  or returns. Ecobee now reconciles the entity registry and uses the supported
  in-place registry path without recreating or reloading the config entry; the
  Home coordinator owns portfolio promotion under `helper-device-linking`.
- **Product-specific Core consequence:** health cadence uses `last_reported`,
  unchanged Ecobee reports can confirm only a pending current revision, and
  elapsed-time degradation still has a no-new-event boundary test.
- **Product-specific Recorder consequence:** volatile source-age,
  active-sensor, and command-confirmation attributes are visible live but
  unrecorded; diagnostics retain bounded redacted evidence.
- **Conditional shared public-safety pattern:** history scans cover commit
  metadata, every historical filename, and every reachable bounded blob so
  removed or binary content is not hidden from review.
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
validation, consumer migration, outbound effects, and rollback.
