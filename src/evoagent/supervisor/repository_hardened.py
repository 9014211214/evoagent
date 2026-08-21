from __future__ import annotations

from evoagent.supervisor.models import (
    SupervisorCaseStatus,
    SupervisorRunStatus,
    SupervisorTrack,
)
from evoagent.supervisor.repository import (
    SQLiteSupervisorRepository as _BaseSQLiteSupervisorRepository,
    StaleSupervisorRevision,
    SupervisorAuditIntegrityError,
    SupervisorConflictError,
)


class SQLiteSupervisorRepository(_BaseSQLiteSupervisorRepository):
    """Hardened repository semantics for routed cases versus executions.

    A routed Skill or Model case can legitimately finish as ``BLOCKED`` when
    its immutable execution budget is exhausted. The execution budget therefore
    counts only outcomes carrying a governed executor identity, not every case
    that was causally routed to that track.
    """

    def verify_state(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        cases = self.list_cases(run_id)
        if len(cases) > run.policy.budget.max_cases:
            raise SupervisorConflictError(
                "Persisted Supervisor cases exceed the run budget."
            )
        if run.status not in {
            SupervisorRunStatus.OPEN,
            SupervisorRunStatus.RUNNING,
        } and any(
            item.status
            in {SupervisorCaseStatus.PENDING, SupervisorCaseStatus.RUNNING}
            for item in cases
        ):
            raise SupervisorConflictError(
                "Terminal Supervisor run contains unfinished cases."
            )
        if (
            run.status == SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS
            and not any(
                item.status == SupervisorCaseStatus.ESCALATED for item in cases
            )
        ):
            raise SupervisorConflictError(
                "COMPLETED_WITH_ESCALATIONS run has no escalated case."
            )
        if run.status == SupervisorRunStatus.QUARANTINED and not any(
            item.status == SupervisorCaseStatus.QUARANTINED for item in cases
        ):
            raise SupervisorConflictError(
                "QUARANTINED run has no quarantined case."
            )

        def executed(track: SupervisorTrack) -> int:
            return sum(
                item.track == track
                and item.outcome is not None
                and item.outcome.executor_id is not None
                for item in cases
            )

        if executed(SupervisorTrack.SKILL) > (
            run.policy.budget.max_skill_executions
        ):
            raise SupervisorConflictError(
                "Skill execution count exceeds the Supervisor budget."
            )
        if executed(SupervisorTrack.MODEL) > (
            run.policy.budget.max_model_executions
        ):
            raise SupervisorConflictError(
                "Model execution count exceeds the Supervisor budget."
            )
        repair_tickets = sum(
            item.track == SupervisorTrack.EXTERNAL_REPAIR
            and item.outcome is not None
            and bool(item.outcome.artifact_refs)
            for item in cases
        )
        if repair_tickets > run.policy.budget.max_external_repair_tickets:
            raise SupervisorConflictError(
                "External repair ticket count exceeds the Supervisor budget."
            )
        self.verify_audit()
        return True


__all__ = [
    "SQLiteSupervisorRepository",
    "StaleSupervisorRevision",
    "SupervisorAuditIntegrityError",
    "SupervisorConflictError",
]
