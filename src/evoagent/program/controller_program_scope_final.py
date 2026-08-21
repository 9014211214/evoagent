from __future__ import annotations

from copy import copy
from typing import Any

from evoagent.program.controller_public_final import (
    RetryHardenedEvolutionProgramController as _AuditBoundController,
)


class _ProgramScopedRepository:
    """Read-only facade that scopes audit-event reads to one Program.

    Program generation IDs are unique only inside a Program.  The underlying
    audit table is global to the SQLite repository, so lifecycle validation must
    not count another Program's identically named generation events.
    """

    def __init__(self, repository: Any, program_id: str):
        self._repository = repository
        self._program_id = program_id

    def events(self):
        return tuple(
            event
            for event in self._repository.events()
            if event.program_id == self._program_id
        )

    def __getattr__(self, name: str):
        return getattr(self._repository, name)


class RetryHardenedEvolutionProgramController(_AuditBoundController):
    """Final public Controller with Program-scoped lifecycle audit reads."""

    def _validate_campaign_lifecycle_audit(self, campaign, approvals) -> None:
        _, _, _, plan = self._campaign_evidence(campaign)
        scoped_controller = copy(self)
        scoped_controller.repository = _ProgramScopedRepository(
            self.repository,
            plan.program_id,
        )
        _AuditBoundController._validate_campaign_lifecycle_audit(
            scoped_controller,
            campaign,
            approvals,
        )


__all__ = ["RetryHardenedEvolutionProgramController"]
