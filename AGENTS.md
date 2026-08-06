# AGENTS.md

This repository owns the public-safe source and design for the Home Assistant
custom integration **Ecobee Unified** (`ecobee_unified`). It is intentionally
independent from any private Home Assistant configuration repository.

## Start Here

Read, in order:

1. `docs/architecture.md`
2. `docs/requirements.md`
3. `docs/cross-project-learnings.md`
4. `docs/decisions.md`
5. `docs/implementation-plan.md`
6. `docs/validation-plan.md`

The maintainer's private runtime mapping, consumer inventory, migration plan,
and live validation evidence are external inputs. Never copy private entity
IDs, device/config-entry IDs, household names, addresses, IP addresses,
credentials, diagnostics, or Recorder data into this repository. Tests and
documentation use generic fixtures only.

## Design Contract

- This is a Home Assistant helper integration, not another Ecobee API client.
- Consume supported Home Assistant state, registry, event, and service APIs.
- Never import another integration's private runtime objects or edit
  Home Assistant `.storage`.
- Each field has one deterministic primary source and an explicit fallback.
  Never average equivalent values and never use "latest timestamp wins."
- Each command has exactly one writer. Read fallback does not imply automatic
  write failover, and a command must never be sent through two backends.
- The raw integrations remain independent and recoverable. The unified entity
  is a presentation and policy layer, not the data owner.
- Keep Recorder attributes compact. Put high-volume evidence in diagnostics,
  not state attributes.
- Entity properties must perform no I/O.
- Build one normalized per-mapping snapshot and project every entity and
  diagnostic surface from it; do not reinterpret source attributes separately.
- Missing or stale sources degrade explicitly; they do not trigger name-based
  remapping or silent semantic substitution.
- Pending commands are revision-guarded so a late source update cannot confirm
  or fail a superseded command.

## Implementation Posture

- Use current Home Assistant developer documentation and source before material
  implementation; do not code from these design notes alone when an upstream
  contract may have changed.
- Prefer native config/options flows, entity/device selectors, config-entry
  migrations, translations, diagnostics, Repairs, and helper-device linking.
- Support partial source availability when the resulting semantics remain
  honest. Make unsupported capabilities unavailable rather than fabricating
  values.
- Preserve existing behavior during refactors and add tests for every source
  selection, fallback, command-routing, and migration contract affected.
- Keep commits coherent and the public payload privacy-reviewed.

## Quality and Release

Use Home Assistant's current Integration Quality Scale rules as an evaluation
inventory, not a certification target or automatic backlog. Adopt rules that
close concrete correctness, privacy, compatibility, recovery, diagnostic, or
maintenance risks; document deliberate deferrals.

A source commit is not a release. Before any release, align the manifest
version, immutable tag, and release; wait for all required CI to succeed; then
create the release from the validated commit. Public repository creation,
publication, HACS installation/update, Home Assistant configuration checks,
restart, migration, and live validation are separate authorization gates.
