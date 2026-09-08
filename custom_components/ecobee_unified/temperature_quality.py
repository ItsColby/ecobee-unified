"""Evidence-based recovery for an explicitly selected precise temperature."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose

SourceIdentity = tuple[str, str, str, str]


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
