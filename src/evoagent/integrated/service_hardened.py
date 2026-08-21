from __future__ import annotations

from evoagent.composite import CompositeStopAction

from .service import (
    IntegratedDispatchAction,
    IntegratedDispatchPlan,
    IntegratedSupervisorService as _BaseSupervisorService,
)


class IntegratedSupervisorService(_BaseSupervisorService):
    """Final Supervisor with exact integrated/composite round binding."""

    def complete_from_latest_decision(
        self,
        run_id: str,
        *,
        actor_id: str,
        expected_run_revision: int,
        now=None,
    ):
        if self.evaluation_service is None:
            raise RuntimeError(
                "Integrated completion requires the composite evaluation service."
            )
        run = self.repository.get_run(run_id)
        decision = self.evaluation_service.latest_decision(run.lineage_id)
        active = self.evaluation_service.snapshot_registry.active(
            run.lineage_id
        )
        if decision.snapshot_id != active.snapshot_id:
            raise RuntimeError(
                "Integrated terminal decision does not target the active composite snapshot."
            )
        pending_ids = tuple(
            sorted(
                item.case.case_id
                for item in self.repository.pending_cases(run_id)
            )
        )
        if tuple(decision.actionable_case_ids) != pending_ids:
            raise RuntimeError(
                "Integrated terminal decision differs from pending automatic cases."
            )
        if run.round_index != active.manifest.round_index:
            raise RuntimeError(
                "Integrated execution round differs from the active composite snapshot round."
            )
        if decision.round_index != run.round_index:
            raise RuntimeError(
                "Integrated terminal decision differs from the executed run round."
            )
        if decision.action == CompositeStopAction.CONTINUE:
            raise RuntimeError(
                "Integrated completion cannot consume a CONTINUE decision."
            )
        return self.repository.complete_run(
            run_id,
            decision,
            actor_id=actor_id,
            expected_run_revision=expected_run_revision,
            now=now,
        )


__all__ = [
    "IntegratedDispatchAction",
    "IntegratedDispatchPlan",
    "IntegratedSupervisorService",
]
