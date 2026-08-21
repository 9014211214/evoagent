from __future__ import annotations

from evoagent.supervisor.models import (
    SupervisorCase,
    SupervisorCaseStatus,
    SupervisorOutcome,
    SupervisorTrack,
)
from evoagent.supervisor.repository import SupervisorConflictError
from evoagent.supervisor.service import (
    EvolutionTrackExecutor,
    PersistentEvolutionSupervisor as _BasePersistentEvolutionSupervisor,
    build_supervisor_case,
    route_case,
)


class PersistentEvolutionSupervisor(_BasePersistentEvolutionSupervisor):
    """Supervisor that verifies completed track attestations after execution."""

    def _execute_or_route(
        self,
        case: SupervisorCase,
        track: SupervisorTrack,
    ) -> SupervisorOutcome:
        outcome = super()._execute_or_route(case, track)
        if outcome.status != SupervisorCaseStatus.COMPLETED:
            return outcome
        if track == SupervisorTrack.SKILL and not outcome.skill_promoted:
            raise SupervisorConflictError(
                "Completed Skill executor omitted governed promotion evidence."
            )
        if track == SupervisorTrack.MODEL and not (
            outcome.model_candidate_evaluated
            and outcome.model_candidate_activated
            and outcome.model_rollback_verified
        ):
            raise SupervisorConflictError(
                "Completed Model executor omitted evaluation, activation, or rollback evidence."
            )
        return outcome


__all__ = [
    "EvolutionTrackExecutor",
    "PersistentEvolutionSupervisor",
    "build_supervisor_case",
    "route_case",
]
