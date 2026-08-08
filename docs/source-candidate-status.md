# Validated Source Candidate Status

Date: 2026-08-08

Ecobee Unified is implemented as an unreleased, public-safe source candidate
with Core 2026.8.0 as its distribution minimum and dependency-closed formal
minimum lane. Installed Core 2026.8.1 has a second exact lane using its matching
published harness. The installed-Core structural evidence gap is closed; hosted
execution and every publication, release, and live gate remain blocked. A local
candidate commit is not remote Git integration. This status does not
authorize or claim publication, release, installation, reload/restart, live
validation, consumer migration, outbound effects, or rollback.

## Implemented product surface

- One typed config-entry runtime owns multiple explicit thermostat mappings.
- Config and reconfigure flows use entity selectors, store registry IDs, reject
  invalid platforms, duplicate sources or case-insensitively duplicate mapping
  names, semantically invalid optional sensors,
  and unproven or mismatched physical-device pairings; they preserve temporarily
  missing saved references and require confirmation before physical-association
  or writer changes. Selector results are filtered to their owning HomeKit or
  Ecobee integration before backend contract checks run.
- One normalized immutable snapshot per mapping owns field selection,
  provenance, source age/health, capability-aware degradation, explicit local
  precise temperature/preset plus vendor-only projections, and recent command
  status. Quiet HomeKit push/event age remains diagnostic; cadence-backed
  Ecobee freshness uses Core's `last_reported` semantics and lifecycle-owned
  stale-boundary reevaluation. A filtered unchanged-report listener recovers
  stale cadence-backed sources without rebuilding healthy mappings and is
  removed on unload. Ecobee health and command confirmation both default to 30
  minutes based on read-only live reporting-tail evidence.
- One climate, minimum-fan number, bounded equipment-stage sensor, and optional
  AQI/CO2/VOC sensors plus thermostat-display notification project the snapshot
  without property I/O and link to the selected HomeKit thermostat device. The
  climate uses its translated sibling name rather than repeating the mapping
  name. Minimum fan runtime is explicitly a duration measured in minutes. An
  explicitly mapped same-device HomeKit temperature sensor can preserve honest
  local decimals inside the climate without creating a duplicate temperature
  entity only while it agrees with the local climate's unit-specific
  serialization envelope; divergence or unverifiable consistency degrades to
  the local climate and then the documented cloud fallback. AQI, CO2, and VOC
  projections require their exact sensor device class and unit. Registry
  reconciliation follows source move/detach/removal/
  restoration in place without config-entry reload, ignores unrelated global
  registry events, and removes only owned orphan entities after a mapping or
  optional projection is removed.
- Supported HomeKit serial and Ecobee identifier evidence proves each active
  cross-backend pairing. Identity loss preserves local HomeKit state/control but
  blocks cloud fallback, vendor actions, notification writes, and target-step
  fusion until registry evidence recovers.
- Standard climate, preset, and clear-hold commands call only their explicitly
  mapped HomeKit entities. Clear Hold is independent of preset observation and
  becomes submitted after one successful press because its source button has no
  resulting-state contract. Temperature confirmation uses only the writer's
  half-step quantization envelope. The minimum-fan number rejects non-finite,
  off-step, and out-of-range values before I/O and calls only Ecobee
  `set_fan_min_on_time`. Resolved mapped targets override any caller-provided
  `entity_id`; no path retries or fails over to a second writer.
- Bounded vacation, occupancy-policy, and comfort-sensor-participation actions
  inject only the mapped Ecobee climate and issue one call. Effects not fully
  projected in source state are reported as submitted rather than confirmed.
  Vacation temperatures must fit the mapped writer's advertised unit and
  safety bounds, and comfort-sensor devices must belong to the mapped Ecobee
  config entry before the source action receives a call.
- The optional notification facade sends a non-empty message through only the
  mapped Ecobee notification entity; missing services, unavailable state, and
  device-association drift fail before any write.
- Beestat retains first-class schedule/transition/filter/alert entities plus
  history import and Recorder ownership; Ecobee Unified does not duplicate
  schedule/transition or vendor sibling state in climate attributes.
- Confirmation observes the operation-owned normalized source, uses monotonically increasing
  revisions, and guards observation, timeout, and failure updates against
  superseded commands. A fresh matching unchanged Ecobee report can confirm
  only the current pending revision.
- Diagnostics are allow-listed and mapping-indexed. Repairs cover removed or
  user-disabled required/optional registry sources, invalid semantic contracts,
  missing device links, and physical-identity drift, and clear when valid
  evidence recovers.
- The repository includes generic fixtures/tests, strict typing and Ruff
  policy, exact Core requirements, complete reachable-blob public-safety and
  history scanning, Hassfest and HACS jobs, least workflow permissions, bounded
  timeouts/concurrency, non-persistent checkout credentials, and a terminal
  release gate. Pytest explicitly owns async HA test collection, and tracked
  source archives read exact stage-0 blobs and reject maintainer-only agent
  instructions. Volatile source
  age, active-sensor, and command-confirmation attributes are excluded from
  Recorder.

## MVP acceptance disposition

| Item | Disposition | Evidence |
|---|---|---|
| Multiple mappings in one entry | Proven locally | Real Core flow manager creates two registry-ID mappings in one entry. |
| Deterministic standard fields and fallback | Proven locally | Table-driven pure tests cover every standard field, unequal values, missing fields, stale/unavailable sources, honest primary precision, no averaging, and semantic current temperature. |
| Quiet-source health and recovery | Proven locally | Exact-Core manager tests keep quiet HomeKit push/event state healthy, keep Ecobee healthy through its 30-minute default boundary, degrade immediately after it or on actual unavailable/missing state, recover cadence sources on unchanged reports, suppress healthy-report churn, clean listeners on unload, and prevent oscillation. |
| Target humidity and temperature metadata | Proven locally | Pure and exact-Core tests cover capability/bounds gating, exactly one HomeKit humidity writer, HomeKit report confirmation, no fabricated humidity step when the supported writer contract exposes none, Celsius/Fahrenheit climate-writer units, writer-owned bounds, identity-proven same-device temperature-step fusion without freshness or precision substitution, and half-step temperature confirmation quantization. |
| Precise local current temperature | Proven locally | Pure and exact-Core tests cover explicit same-device capability validation, unit conversion, source-dependent climate-state precision, preserved decimals, Fahrenheit/Celsius serialization-envelope boundaries, quiet-source ownership, explicit divergence/unverifiable degradation, climate/cloud fallback, rename, move/detach, disappearance, and recovery without apparent-precision or freshness selection. |
| Canonical device surface without duplicate semantics | Proven locally | Config/model/entity tests cover translated climate sibling naming, physical identity, preset capability, the native Unified resume button, duration-class minimum fan number, bounded equipment stage, device-class/unit-valid AQI/CO2/VOC, notification, and linking for all created platforms; schedule/transition remain first-class Beestat entities. |
| Exactly one HomeKit call per standard command | Proven locally | Real Core service registry observes one call and no Ecobee call; adversarial payloads cannot override the mapped target, and the direct exact-Core suite covers the full climate method matrix. |
| Exactly one local/vendor call per specialized action | Proven locally | Real Core service registry proves one HomeKit select, one submitted HomeKit clear-hold press from each Unified entry point without preset dependence, and one mapped Ecobee call for minimum fan, notification, unit-aware bounded vacation, occupancy policy, and sensor participation. Unprojectable effects remain submitted. |
| Observation never retries | Proven locally | Command tracker and service-registry tests prove confirmation is read-only and timeouts issue no service call. |
| Reload, rename, loss/recovery, removal | Proven locally | Real Core config-entry and registry/state tests prove unload/reload with stable ID, owned orphan cleanup, registry rename, filtered source/helper events, physical-identity and semantic-contract drift/recovery, capability loss/recovery, disabled/detached source Repairs, source removal/restoration, preserved missing selection, and Repair deletion after restoration. |
| Correct Core 2026.8 device linkage | Proven locally | Real Core device/entity registry tests verify initial `device_entry`, in-place move/detach/remove/restore relinking, stable config/entity identity, and no foreign `device_info`. |
| Useful, privacy-redacted diagnostics | Proven locally | Real Core diagnostics test plus working-tree/exact-index-archive and commit-metadata/filename/reachable-blob public-safety scans. |
| Recorder/presentation hygiene | Proven locally | Climate tests and source inspection prove volatile ages are unrecorded and schedule/transition, equipment stage, and minimum-fan state are absent from climate attributes. |
| Repository and HA workflows terminal green | Local subset green; hosted gates blocked | One hundred four bounded tests pass directly on Core 2026.8.1. The workflow now owns separate exact Core 2026.8.0 minimum/harness 0.13.354 and Core 2026.8.1 current/harness 0.13.355 jobs; each installs product tooling last, requires final `pip check`, and runs the complete HA test surface. Strict mypy, host Ruff, compile, JSON, working-tree/exact-index-archive, and complete reachable-history privacy checks pass locally. Linux pytest, Hassfest, and HACS remain hosted-only. |
| Private shadow soak before migration | Deferred live gate | Requires separately authorized installation and at least seven days of private evidence. |
| Late observation cannot mutate newer command | Proven locally | Revision supersession tests cover observation, timeout, failure, and confirmation source selection. |

## Validation boundary

Published PyPI metadata was re-read on 2026-08-08:
`pytest-homeassistant-custom-component==0.13.354` requires exact Core
2026.8.0, while harness 0.13.355 requires exact Core 2026.8.1. The workflow
keeps those dependency-closed environments separate and requires final
`pip check` in both. The Windows host still cannot import the full Home
Assistant pytest plugin because Core imports POSIX `fcntl`; Docker is absent
and WSL has no installed distribution. Bounded real Core API tests run directly
under `unittest` without a fake harness; complete pytest execution remains
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
The Beestat canonical product branch is now locally fast-forwarded through
`1019f4567991962be6b2349b48013de8002c7391`, which also completes cached
temporal projection and exact-Core harness corrections; that local integration
is not a public push, release, install, or live validation claim.

## Home registry handoff

The current Home registry lifecycle classification is `active_unreleased`.
Home owner commit
`008a869a944f75be3b8610dfb600d191a06fde14` records the support fields
below and is clean and privately integrated with its configured remote.
The product repository still has no configured remote, so
`repository.public_url` remains `null`; the Home registry record is not remote
Git integration or product publication.

Exact support owners for portfolio schema v2:

| Field | Final value |
|---|---|
| `policy` | `explicit_broader` |
| `hacs_metadata` | `hacs.json` |
| `core_requirements` | `requirements-ha-current.txt` |
| `minimum_core_requirements` | `requirements-ha-test.txt` |
| `test_workflow` | `.github/workflows/validate.yaml` |
| `broader_support_reason` | `The product maintains its declared distribution floor and the current stable patch of the same Home Assistant monthly release.` |

All controlled capability dispositions and product-relative evidence paths:

| Capability | Applicability / status | Evidence or reason |
|---|---|---|
| `typed-runtime` | required / observed | `custom_components/ecobee_unified/runtime.py`; `custom_components/ecobee_unified/__init__.py` |
| `config-entry-lifecycle` | required / observed | `custom_components/ecobee_unified/config_flow.py`; `custom_components/ecobee_unified/__init__.py`; `tests/test_runtime_core_api.py` |
| `config-entry-migrations` | required / observed | `custom_components/ecobee_unified/__init__.py`; `tests/test_runtime_core_api.py` |
| `bounded-source-boundary` | required / observed | `custom_components/ecobee_unified/source_contracts.py`; `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/models.py`; `tests/test_runtime_core_api.py` |
| `normalized-model` | required / observed | `custom_components/ecobee_unified/models.py`; `custom_components/ecobee_unified/manager.py`; `tests/test_models.py` |
| `dynamic-discovery` | not applicable / not applicable | `docs/architecture.md`; thermostat mappings are explicitly selected rather than discovered. |
| `helper-device-linking` | required / observed | `custom_components/ecobee_unified/entity.py`; `custom_components/ecobee_unified/button.py`; `custom_components/ecobee_unified/notify.py`; `custom_components/ecobee_unified/manager.py`; `tests/test_runtime_core_api.py` |
| `recorder-import` | not applicable / not applicable | `docs/architecture.md`; historical import remains owned by Beestat Statistics. |
| `diagnostics-privacy` | required / observed | `custom_components/ecobee_unified/diagnostics.py`; `scripts/check_public_safety.py`; `tests/test_public_safety.py`; `tests/test_runtime_core_api.py` |
| `repairs` | required / observed | `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/strings.json`; `tests/test_runtime_core_api.py` |
| `reauth` | not applicable / not applicable | `docs/architecture.md`; the helper consumes Home Assistant-owned sources and stores no upstream credential. |
| `account-continuity` | not applicable / not applicable | `docs/architecture.md`; the helper owns mappings rather than an authenticated upstream account. |
| `single-writer-actions` | required / observed | `custom_components/ecobee_unified/manager.py`; `custom_components/ecobee_unified/climate.py`; `custom_components/ecobee_unified/button.py`; `custom_components/ecobee_unified/number.py`; `custom_components/ecobee_unified/notify.py`; `tests/test_commands.py`; `tests/test_runtime_core_api.py` |
| `response-producing-actions` | not applicable / not applicable | `docs/architecture.md`; the one-way NotifyEntity facade delegates delivery to the mapped Ecobee notification entity and returns no caller-owned content. |
| `capability-route` | not applicable / not applicable | `docs/architecture.md`; the integration exposes no unauthenticated subscription or capability route. |
| `health-projection` | required / observed | `custom_components/ecobee_unified/models.py`; `custom_components/ecobee_unified/climate.py`; `custom_components/ecobee_unified/button.py`; `custom_components/ecobee_unified/notify.py`; `custom_components/ecobee_unified/diagnostics.py`; `tests/test_models.py`; `tests/test_runtime_core_api.py` |
| `temporary-artifacts` | not applicable / not applicable | `docs/architecture.md`; the integration runtime produces no temporary files or attachments. |
| `dependency-closure` | required / observed | `.github/workflows/validate.yaml`; `requirements-ha-test.txt`; `requirements-ha-current.txt`; `tests/test_public_safety.py` |
| `installed-core-test` | required / observed | `hacs.json`; `requirements-ha-test.txt`; `requirements-ha-current.txt`; `.github/workflows/validate.yaml`; `tests/test_runtime_core_api.py`; `docs/upstream-contracts.md`; exact dependency-clean lanes cover Core 2026.8.0 and the maintained Core 2026.8.1 patch. |

The last full-portfolio receipt recorded here, from Home commit
`548d05540b6f0f8ee457981ef7932c5bafbe79d4`, reported 27 passes, eight fails,
and no warnings or unavailable checks. At that historical receipt, Ecobee
Unified accounted for nine passes and two installed-Core support-lane gaps;
the other six failures belonged to the separate Beestat and Free Library
product owners. It is not a current cross-portfolio green or failure claim.

The product-scoped portfolio audit for clean Ecobee commit
`c2235ff46d5f968a8b7dc2b2740aa8caaf4f0131` reports 12 passes and no
failures, warnings, or unavailable checks in candidate posture. Canonical
posture reports 11 passes, no failures or warnings, and one expected
`audit-provenance` unavailable result because this product has no remote.
The receipt does not prove product remote Git integration, publication,
release, or live-instance state.

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
- **Product-specific lifecycle refinements under existing capabilities:** use
  stable source registry references to observe removal/restoration, ignore
  unrelated registry events, resynchronize only owned helper records, remove
  only entry-owned orphan entities after reconfigure, and surface disabled or
  detached selections as recoverable Repairs.
- **Product-specific action validation under the existing single-writer
  capability:** selected comfort-sensor devices must share the mapped Ecobee
  config entry before the source service is invoked. Core retains final
  thermostat-membership enforcement.
- **Product-specific Core consequence:** cadence-backed Ecobee health uses
  `last_reported` with a read-only-live-calibrated 30-minute default, while
  quiet HomeKit age remains diagnostic; unchanged reports
  can confirm only the operation-owned pending revision, and elapsed-time cloud
  degradation still has a no-new-event boundary test.
- **Product-specific Recorder consequence:** volatile source-age,
  active-sensor, and command-confirmation attributes are visible live but
  unrecorded; diagnostics retain bounded redacted evidence.
- **Product-specific source refinement:** an explicitly mapped same-device
  HomeKit temperature sensor may preserve honest local decimals in the
  canonical climate after device-class, unit, finite-value, association, and
  climate-state serialization validation, and only while it agrees with the
  local climate inside that serialization envelope. This is not a generic
  precision or freshest-source rule.
- **Product-specific command refinements:** Clear Hold is submitted after one
  successful local press without preset dependence; vacation temperatures use
  mapped Ecobee writer units/bounds; temperature confirmation alone admits the
  writer's half-step quantization envelope.
- **Product-specific identity consequence:** explicit selection is not proof
  that HomeKit and Ecobee represent the same thermostat. Supported registry
  identity equality gates every cloud read/action and cross-interface metadata
  fusion while leaving local HomeKit control available.
- **Product-specific semantic consequence:** AQI, CO2, and VOC require exact
  device-class/unit contracts at configuration and runtime; a same-device but
  different physical quantity is unavailable rather than relabeled.
- **Product-specific action consequence:** the optional Unified notification
  entity is covered by existing single-writer and helper-linking capabilities;
  Ecobee still owns transport and delivered-content semantics, so no new shared
  capability is required.
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
