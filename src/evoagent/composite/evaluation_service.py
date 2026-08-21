from __future__ import annotations

from datetime import datetime

from .evaluation import (
    CompositeSnapshotEvaluation,
    CompositeStopDecision,
    CompositeStopPolicy,
    CompositeTaskOutcome,
    build_composite_evaluation,
    build_composite_stop_decision,
)
from .evaluation_repository import (
    CompositeDecisionRecord,
    CompositeEvaluationPolicyRecord,
    CompositeEvaluationRecord,
    SQLiteCompositeEvaluationRepository,
)
from .repository import SQLiteCompositeSnapshotRegistry


class CompositeEvaluationRoleError(ValueError):
    pass


class CompositeEvaluationService:
    """Evaluate only the active composite pointer under separated roles."""

    def __init__(
        self,
        snapshot_registry: SQLiteCompositeSnapshotRegistry,
        evaluation_repository: SQLiteCompositeEvaluationRepository,
    ):
        self.snapshot_registry = snapshot_registry
        self.evaluation_repository = evaluation_repository

    def register_policy(
        self,
        lineage_id: str,
        policy: CompositeStopPolicy,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> CompositeEvaluationPolicyRecord:
        active = self.snapshot_registry.active(lineage_id)
        if actor_id in {
            active.manifest.created_by,
            active.committed_by,
        }:
            raise CompositeEvaluationRoleError(
                "Composite stop-policy registrar overlaps snapshot control roles."
            )
        return self.evaluation_repository.register_policy(
            lineage_id,
            policy,
            actor_id=actor_id,
            now=now,
        )

    def evaluate_active(
        self,
        lineage_id: str,
        *,
        evaluation_id: str,
        outcomes: tuple[CompositeTaskOutcome, ...],
        evaluator_id: str,
        evaluated_at: datetime,
        now: datetime | None = None,
    ) -> CompositeEvaluationRecord:
        active = self.snapshot_registry.active(lineage_id)
        if evaluator_id in {
            active.manifest.created_by,
            active.committed_by,
        }:
            raise CompositeEvaluationRoleError(
                "Composite evaluator overlaps snapshot creation or commit roles."
            )
        parent = self._parent_evaluation(active.manifest.round_index, lineage_id)
        evaluation = build_composite_evaluation(
            active.manifest,
            evaluation_id=evaluation_id,
            outcomes=outcomes,
            evaluator_id=evaluator_id,
            evaluated_at=evaluated_at,
            parent=parent,
        )
        return self.evaluation_repository.record_evaluation(
            evaluation,
            actor_id=evaluator_id,
            now=now,
        )

    def decide_active(
        self,
        lineage_id: str,
        *,
        decision_id: str,
        actionable_case_ids: tuple[str, ...],
        budget_exhausted: bool,
        decided_by: str,
        decided_at: datetime,
        now: datetime | None = None,
    ) -> CompositeDecisionRecord:
        active = self.snapshot_registry.active(lineage_id)
        evaluation_record = self.evaluation_repository.evaluation(
            lineage_id,
            active.snapshot_id,
        )
        policy_record = self.evaluation_repository.policy(lineage_id)
        prohibited = {
            active.manifest.created_by,
            active.committed_by,
            evaluation_record.evaluation.evaluator_id,
            policy_record.registered_by,
        }
        if decided_by in prohibited:
            raise CompositeEvaluationRoleError(
                "Composite stop decider overlaps governed evaluation roles."
            )
        decision = build_composite_stop_decision(
            evaluation_record.evaluation,
            policy_record.policy,
            decision_id=decision_id,
            actionable_case_ids=actionable_case_ids,
            budget_exhausted=budget_exhausted,
            decided_by=decided_by,
            decided_at=decided_at,
        )
        return self.evaluation_repository.record_decision(
            decision,
            actor_id=decided_by,
            now=now,
        )

    def verify_state(self, lineage_id: str) -> bool:
        self.snapshot_registry.verify_state(lineage_id)
        self.evaluation_repository.verify_state(lineage_id)
        snapshots = self.snapshot_registry.list_snapshots(lineage_id)
        evaluations = self.evaluation_repository.list_evaluations(lineage_id)
        if len(evaluations) > len(snapshots):
            raise RuntimeError(
                "Composite evaluation lineage contains an unknown snapshot round."
            )
        by_snapshot = {item.snapshot_id: item for item in snapshots}
        for evaluation_record in evaluations:
            evaluation = evaluation_record.evaluation
            snapshot = by_snapshot.get(evaluation.snapshot_id)
            if snapshot is None:
                raise RuntimeError(
                    "Composite evaluation references an unknown snapshot."
                )
            if (
                evaluation.lineage_id != snapshot.lineage_id
                or evaluation.round_index != snapshot.manifest.round_index
                or evaluation.snapshot_manifest_hash
                != snapshot.manifest.manifest_hash
                or evaluation.task_manifest_hash
                != snapshot.manifest.task_manifest_hash
            ):
                raise RuntimeError(
                    "Composite evaluation differs from its immutable snapshot."
                )
        return True

    def latest_evaluation(
        self,
        lineage_id: str,
    ) -> CompositeSnapshotEvaluation:
        evaluations = self.evaluation_repository.list_evaluations(lineage_id)
        if not evaluations:
            raise KeyError(
                f"Composite lineage has no evaluation: {lineage_id}"
            )
        return evaluations[-1].evaluation

    def latest_decision(self, lineage_id: str) -> CompositeStopDecision:
        decisions = self.evaluation_repository.list_decisions(lineage_id)
        if not decisions:
            raise KeyError(
                f"Composite lineage has no stop decision: {lineage_id}"
            )
        return decisions[-1].decision

    def _parent_evaluation(
        self,
        round_index: int,
        lineage_id: str,
    ) -> CompositeSnapshotEvaluation | None:
        if round_index == 0:
            return None
        evaluations = self.evaluation_repository.list_evaluations(lineage_id)
        if len(evaluations) != round_index:
            raise RuntimeError(
                "Composite active snapshot is not preceded by one evaluation per round."
            )
        return evaluations[-1].evaluation


__all__ = [
    "CompositeEvaluationRoleError",
    "CompositeEvaluationService",
]
