"""Evidence-based recovery for an explicitly selected precise temperature."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import isclose

SourceIdentity = tuple[str, str, str, str]

# The rounded climate reading and the precise sensor serialize one HomeKit
# characteristic, so every rounded change implies a precise report. Two
# unanswered changes tolerate one reordered or dropped pair, and the older one
# must have waited long enough that a burst of transport events cannot qualify.
SILENT_CLIMATE_CHANGES = 2
SILENT_MINIMUM_SECONDS = 60


@dataclass(frozen=True, slots=True)
class TemperatureObservation:
    """One real source association and its temperature in canonical Celsius."""

    identity: SourceIdentity
    celsius: float

    def matches(self, other: TemperatureObservation) -> bool:
        """Ignore only floating-point conversion noise, never source replacement."""

        return self.identity == other.identity and isclose(
            self.celsius, other.celsius, rel_tol=0.0, abs_tol=1e-9
        )


@dataclass(slots=True)
class TemperatureRecovery:
    """Retain confirmed contrary evidence until the same source proves recovery."""

    identity: SourceIdentity | None = None
    rejected: TemperatureObservation | None = None

    def observe(
        self,
        identity: SourceIdentity | None,
        observation: TemperatureObservation | None,
        agrees: bool | None,
        *,
        confirmed: bool = False,
    ) -> bool:
        """Return whether precision must stay blocked after this observation.

        Missing data is not replacement or recovery. Confirmation belongs to the
        manager's bounded pair-settle timer, not to report age or refresh count.
        """

        if identity is not None and identity != self.identity:
            self.identity = identity
            self.rejected = None
        if observation is not None:
            if agrees is False and confirmed:
                self.rejected = observation
            elif (
                agrees is True
                and self.rejected is not None
                and not observation.matches(self.rejected)
            ):
                self.rejected = None
        return self.rejected is not None


@dataclass(slots=True)
class TemperatureSilence:
    """Count rounded climate changes that no precise report has answered."""

    identity: SourceIdentity | None = None
    changes: list[datetime] = field(default_factory=list)

    def climate_changed(
        self, identity: SourceIdentity | None, changed_at: datetime
    ) -> None:
        """Record one rounded climate change for this source association."""

        if identity != self.identity:
            self.identity = identity
            self.changes.clear()
        if identity is None:
            return
        self.changes.append(changed_at)
        del self.changes[:-SILENT_CLIMATE_CHANGES]

    def silent(
        self,
        identity: SourceIdentity | None,
        precise_reported_at: datetime | None,
        now: datetime,
    ) -> bool:
        """Return whether the precise sensor missed enough paired changes."""

        if identity is None or identity != self.identity:
            return False
        if precise_reported_at is not None:
            self.changes = [
                changed for changed in self.changes if changed > precise_reported_at
            ]
        return (
            len(self.changes) >= SILENT_CLIMATE_CHANGES
            and (now - self.changes[0]).total_seconds() >= SILENT_MINIMUM_SECONDS
        )

    def reset(self) -> None:
        """Require fresh evidence after a recovery attempt."""

        self.changes.clear()
