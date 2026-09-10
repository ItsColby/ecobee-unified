# Design decisions

This document explains the choices behind the current product. Exact runtime
rules belong to [architecture](architecture.md); required outcomes belong to
[requirements](requirements.md). It is not a release history or a record of
validation runs.

## A native integration with existing source owners

Native grouping and template helpers do not provide the complete climate
control surface with deterministic ownership across HomeKit and Ecobee.
Ecobee Unified therefore implements a custom integration under
`ecobee_unified`, presented as a hub because one entry manages multiple
thermostat mappings. It reuses installed integrations through Home Assistant
interfaces instead of adding authentication, throttling, or transport code.

HomeKit owns standard live state and control. Ecobee contributes vendor detail
and supported actions. Beestat remains an independent sibling for cloud
history and derived context. Sharing a thermostat device presentation does not
transfer responsibility for another integration's data or storage.

## One entry, stable mappings, existing thermostat devices

Mappings share a lifecycle and need atomic duplicate and identity checks, so
one config entry holds the collection. Per-mapping subentries would add
migration and relinking work without improving the current configuration model.
Separate stable mapping IDs already distinguish the thermostat entities.

Unified links its entities to the selected HomeKit device using the helper
pattern. Creating a second hardware identity or taking over the source device
would make acquisition and removal harder to reason about. Raw source entities
remain available for diagnosis and consumer rollback; visibility and consumer
selection determine the routine UI.

## Source meaning before apparent precision or recency

Deterministic field ownership avoids treating similar-looking values as
interchangeable. Read fallback can preserve a usable thermostat view, but it
does not alter the writer or its supported controls. Physical identity proof
is required because an explicit selection alone cannot establish that two
backend devices are the same thermostat.

The optional temperature sensor preserves accessory precision only when its
metadata, association, and climate agreement support that meaning. A short
settling window accommodates sequential reports. Confirmed contrary evidence
requires a changed agreeing value for recovery, preventing fresh timestamps or
climate movement from rehabilitating a frozen reading. The evidence remains
in memory because persistent fault storage and generic anomaly detection are
outside this source-comparison contract.

Target-step sharing is a narrow metadata exception backed by the HomeKit
writer contract. It is not a general mechanism for borrowing cloud bounds,
measurement precision, or controls.

## One writer and honest command results

Automatic write failover is excluded. A second path could duplicate or conflict
with a hold even when the first call timed out. Per-mapping serialization
preserves dispatch order within a running manager; revision tracking protects
the most recent command's observation record. Neither mechanism claims
control over effects already dispatched to another integration.

Confirmation requires the designated source report and a successful writer
call. Actions without a complete observable result stay submitted, and
notifications retain their native one-way service semantics. This separates
accepted requests from demonstrated source state without building another
command queue or retry service.

## Capability and health are separate questions

An unknown Current Mode value does not necessarily prevent a safe explicit
preset selection. The integration keeps that distinction only when the source
still proves its bounded options, device association, and writer availability.
An advisory explains unreadable state; an actionable degradation activates the
problem entity.

Likewise, a quiet local push source has no heartbeat obligation. Ecobee cadence
can justify a stale threshold, while HomeKit report age remains diagnostic.
Invalid numbers are field faults rather than evidence of a transport outage.
One normalized snapshot keeps these distinctions consistent across consumers.

## Small public surfaces with private operational policy

Vendor-only controls and source-derived UI belong in the product when they
remove routine raw-source targeting and have clear semantics. Household
decisions about occupancy, comfort, notification timing, or cross-service
workflows belong in the user's Home Assistant configuration. That boundary does
not require replacing a working integration or migrating runtime ownership for
naming consistency.

Bounded states and request-time diagnostic ages avoid unnecessary Recorder
churn. Downloadable diagnostics use an allowlist; local state can still contain
the bounded vendor and sensor names needed by household consumers. Public
source, examples, and fixtures must not contain private deployment evidence.

## Changes that need a new demonstrated use case

Additional projections need a distinct semantic and consumer, including a
defined availability and Recorder contract. Duplicate history, generic room
metrics, additional credentials, automatic write failover, or a subentry
migration are not implied next steps. Reclaiming existing entity IDs needs an
explicit history and rollback decision. Public catalog submission is separate
from maintaining a custom-repository installation path.
