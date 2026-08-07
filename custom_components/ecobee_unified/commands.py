"""Revision-guarded command tracking for Ecobee Unified."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

from .models import CommandStatus, CommandSummary, NormalizedSnapshot, command_matches


@dataclass(slots=True)
class PendingCommand:
    """One current command; a newer revision supersedes it."""

    revision: int
    operation: str
    expected: Mapping[str, Any]
    started: float
    status: CommandStatus = CommandStatus.PENDING


class CommandTracker:
    """Track one current command per mapping without retrying writes."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._revisions: dict[str, int] = {}
        self._commands: dict[str, PendingCommand] = {}

    def begin(
        self, mapping_id: str, operation: str, expected: Mapping[str, Any]
    ) -> int:
        """Start a new revision, superseding any earlier command."""

        revision = self._revisions.get(mapping_id, 0) + 1
        self._revisions[mapping_id] = revision
        self._commands[mapping_id] = PendingCommand(
            revision=revision,
            operation=operation,
            expected=dict(expected),
            started=self._clock(),
        )
        return revision

    def observe(
        self, mapping_id: str, revision: int, snapshot: NormalizedSnapshot
    ) -> bool:
        """Confirm only if the observation belongs to the current revision."""

        command = self._commands.get(mapping_id)
        if (
            command is None
            or command.revision != revision
            or command.status is not CommandStatus.PENDING
        ):
            return False
        if not command_matches(snapshot, command.expected):
            return False
        command.status = CommandStatus.CONFIRMED
        return True

    def fail(self, mapping_id: str, revision: int) -> bool:
        """Mark only the current pending revision failed."""

        return self._set_status(mapping_id, revision, CommandStatus.FAILED)

    def timeout(self, mapping_id: str, revision: int) -> bool:
        """Mark only the current pending revision unconfirmed."""

        return self._set_status(mapping_id, revision, CommandStatus.UNCONFIRMED)

    def current_revision(self, mapping_id: str) -> int | None:
        """Return the revision an arriving observation belongs to."""

        command = self._commands.get(mapping_id)
        return command.revision if command else None

    def pending_revision(self, mapping_id: str) -> int | None:
        """Return the current revision only while confirmation is pending."""

        command = self._commands.get(mapping_id)
        return (
            command.revision
            if command is not None and command.status is CommandStatus.PENDING
            else None
        )

    def summary(self, mapping_id: str) -> CommandSummary:
        """Return a bounded projection of recent command state."""

        command = self._commands.get(mapping_id)
        if command is None:
            return CommandSummary()
        return CommandSummary(
            revision=command.revision,
            operation=command.operation,
            status=command.status,
            age_seconds=max(0, int(self._clock() - command.started)),
        )

    def _set_status(
        self, mapping_id: str, revision: int, status: CommandStatus
    ) -> bool:
        command = self._commands.get(mapping_id)
        if (
            command is None
            or command.revision != revision
            or command.status is not CommandStatus.PENDING
        ):
            return False
        command.status = status
        return True
