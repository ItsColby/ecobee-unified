# Validated Source Candidate Status

Date: 2026-08-07

Ecobee Unified is implemented as an unreleased, public-safe source candidate
with Core 2026.8.0 as its distribution minimum and dependency-closed formal
harness lane. Bounded direct validation passes against installed Core
2026.8.1, but the latest published real harness still requires Core 2026.8.0;
the installed-Core and release gates therefore remain blocked. A local
candidate commit is not remote Git integration. This status does not
authorize or claim publication, release, installation, reload/restart, live
validation, consumer migration, outbound effects, or rollback.

## Implemented product surface

- One typed config-entry runtime owns multiple explicit thermostat mappings.
- Config and reconfigure flows use entity selectors, store registry IDs, reject
  invalid platforms and duplicate sources, preserve temporarily missing saved
  references, and require confirmation before physical-association or writer
  changes.
- One normalized immutable snapshot per mapping owns field selection,
  provenance, source age/health, capability-aware degradation, explicit local
  preset plus vendor-only projections, and recent command status. Quiet HomeKit
  push/event age remains diagnostic; cadence-backed Ecobee freshness uses Core's
  `last_reported` semantics and lifecycle-owned stale-boundary reevaluation.
- One climate, minimum-fan number, bounded equipment-stage sensor, and optional
  AQI/CO2/VOC sensors project the snapshot without property I/O and link to the
  selected HomeKit thermostat device. Registry reconciliation follows source
  move/detach/removal/restoration in place without config-entry reload.
- Standard climate, preset, and clear-hold commands call only their explicitly
  mapped HomeKit entities. The minimum-fan number calls only Ecobee
  `set_fan_min_on_time`. No path retries or fails over to a second writer.
- Bounded vacation, occupancy-policy, and comfort-sensor-participation actions
  inject only the mapped Ecobee climate and issue one call. Effects not fully
  projected in source state are reported as submitted rather than confirmed.
- Beestat retains first-class schedule/transition/filter/alert entities plus
  history import and Recorder ownership; Ecobee Unified does not duplicate
  schedule/transition or vendor sibling state in climate attributes.
- Confirmation observes the operation-owned normalized source, uses monotonically increasing
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
| Deterministic standard fields and fallback | Proven locally | Table-driven pure tests cover every standard field, unequal values, missing fields, stale/unavailable sources, honest primary precision, no averaging, and semantic current temperature. |
| Quiet-source health and recovery | Proven locally | Exact-Core manager tests keep quiet HomeKit push/event state healthy across multiple elapsed/cloud boundaries, degrade only on actual unavailable/missing state, recover ownership, and prevent oscillation. |
| Target humidity and temperature metadata | Proven locally | Pure and exact-Core tests cover capability/bounds gating, exactly one HomeKit humidity writer, HomeKit report confirmation, writer-owned temperature unit/bounds, and explicit same-device step fusion without freshness or precision substitution. |
| Canonical device surface without duplicate semantics | Proven locally | Config/model/entity tests cover preset capability, local clear hold, minimum fan number, bounded equipment stage, optional AQI/CO2/VOC, and linking for all created platforms; schedule/transition remain first-class Beestat entities. |
| Exactly one HomeKit call per standard command | Proven locally | Real Core service registry observes one call and no Ecobee call; full climate method matrix is in the Linux HA test suite. |
| Exactly one local/vendor call per specialized action | Proven locally | Real Core service registry proves one HomeKit select, one HomeKit clear-hold button press, and one mapped Ecobee call for minimum fan, vacation, occupancy policy, and sensor participation. Unprojectable effects remain submitted. |
| Observation never retries | Proven locally | Command tracker and service-registry tests prove confirmation is read-only and timeouts issue no service call. |
| Reload, rename, loss/recovery, removal | Proven locally | Real Core config-entry and registry/state tests prove unload/reload with stable ID, registry rename, capability loss/recovery, source removal/restoration, preserved missing selection, and Repair deletion after restoration. |
| Correct Core 2026.8 device linkage | Proven locally | Real Core device/entity registry tests verify initial `device_entry`, in-place move/detach/remove/restore relinking, stable config/entity identity, and no foreign `device_info`. |
| Useful, privacy-redacted diagnostics | Proven locally | Real Core diagnostics test plus working-tree/archive and commit-metadata/filename/reachable-blob public-safety scans. |
| Recorder/presentation hygiene | Proven locally | Climate tests and source inspection prove volatile ages are unrecorded and schedule/transition, equipment stage, and minimum-fan state are absent from climate attributes. |
| Repository and HA workflows terminal green | Local subset green; installed-Core and hosted gates blocked | Seventy-one bounded tests pass directly on installed Core 2026.8.1, along with strict mypy, Ruff, compile, and a 44-file working-tree/44-file tracked-archive plus complete reachable-history privacy scan. The same bounded suite passes in the dependency-closed Core 2026.8.0 minimum environment, but the latest real harness cannot yet form a closed 2026.8.1 lane; Linux pytest, Hassfest, and HACS also remain hosted-only. |
| Private shadow soak before migration | Deferred live gate | Requires separately authorized installation and at least seven days of private evidence. |
| Late observation cannot mutate newer command | Proven locally | Revision supersession tests cover observation, timeout, failure, and confirmation source selection. |

## Validation boundary

The latest published harness metadata was re-read from PyPI:
`pytest-homeassistant-custom-component==0.13.354` requires
`homeassistant==2026.8.0`. Installing installed Core 2026.8.1 after that
correctly makes `python -m pip check` fail on the exact mismatch. The formal
Core 2026.8.0 workflow remains dependency-closed; it is not relabeled as the
installed-Core lane. The Windows host also cannot import the full Home
Assistant pytest plugin because Core imports POSIX `fcntl`; Docker is absent
and WSL has no installed distribution. Bounded real Core API tests run directly
under `unittest` without a fake harness; the complete pytest suite remains
Linux/CI-owned.

The historical Home-owned handoff was integrated and privately pushed at Home
commit `492e00102b76f2f5dd743fcc9ee58bcc34877374`. At that receipt, the registry
classified Ecobee Unified as `active_unreleased` with the installed-Core
support owners and all 19 capability dispositions below; the then-current
portfolio auditor passed 27 checks and the published skill passed source/runtime
parity. That is preserved as historical provenance, not a current portfolio
green claim. The later shared-contract convergence identified a Beestat
`helper-device-linking` gap. Beestat product commit
`532462a295b2b3f5c01bf474bdabab8471df9c7e` completes that remediation, and
the current Home registry records its evidence as observed. That separate
product and Home integration state does not turn either product into an
Ecobee Unified runtime dependency or establish publication/release/live state.

## Home registry handoff

The current Home registry lifecycle classification is `active_unreleased`.
The product repository still has no configured remote, so
`repository.public_url` remains `null`; the Home registry record is not remote
Git integration or product publication.

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

The current owner-runtime structural audit at Home commit
`c4970174b62a66eaf144017c6dbb3cd5baf8b5a5` reports 30 passes with zero
failures, warnings, or unavailable checks. Its candidate posture explicitly
does not prove remote Git freshness/integration, publication, release, or live
instance state.

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
  current Home registry records this under `helper-device-linking`.
- **Product-specific Core consequence:** cadence-backed Ecobee health uses
  `last_reported`, while quiet HomeKit age remains diagnostic; unchanged reports
  can confirm only the operation-owned pending revision, and elapsed-time cloud
  degradation still has a no-new-event boundary test.
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
