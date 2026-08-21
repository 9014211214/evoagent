from __future__ import annotations

from evoagent.composite import (
    CompositeStopAction,
    build_composite_stop_decision,
)
from evoagent.composite.evaluation_repository import (
    SQLiteCompositeEvaluationRepository,
)
from evoagent.composite.repository import SQLiteCompositeSnapshotRegistry

from .models import IntegratedTrack
from .package import (
    IntegratedEvolutionPackageError,
    IntegratedEvolutionPackageManager as _BasePackageManager,
    IntegratedEvolutionPackageManifest,
)
from .repository import SQLiteIntegratedEvolutionRepository


class IntegratedEvolutionPackageManager(_BasePackageManager):
    """Final package verifier with canonical lineage and audit replay."""

    @classmethod
    def _verify_evaluation_state(cls, package) -> None:
        snapshots = tuple(
            sorted(
                package.composite_snapshots,
                key=lambda item: item.manifest.round_index,
            )
        )
        evaluations = tuple(
            sorted(
                package.evaluations,
                key=lambda item: item.evaluation.round_index,
            )
        )
        decisions = tuple(
            sorted(
                package.stop_decisions,
                key=lambda item: item.decision.round_index,
            )
        )
        if (
            len(snapshots) != 3
            or len(evaluations) != 3
            or len(decisions) != 3
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated package requires one Snapshot, Evaluation and Decision per round."
            )
        scores = tuple(item.evaluation.composite_score for item in evaluations)
        actions = tuple(item.decision.action for item in decisions)
        if scores != (0.25, 0.5, 1.0) or actions != (
            CompositeStopAction.CONTINUE,
            CompositeStopAction.CONTINUE,
            CompositeStopAction.STOP,
        ):
            raise IntegratedEvolutionPackageError(
                "Composite score or bounded stop sequence differs from target."
            )
        for snapshot_record, evaluation_record, decision_record in zip(
            snapshots,
            evaluations,
            decisions,
            strict=True,
        ):
            snapshot = snapshot_record.manifest
            evaluation = evaluation_record.evaluation
            decision = decision_record.decision
            if (
                evaluation.round_index != snapshot.round_index
                or evaluation.snapshot_id != snapshot.snapshot_id
                or evaluation.snapshot_manifest_hash != snapshot.manifest_hash
                or decision.round_index != evaluation.round_index
                or decision.snapshot_id != evaluation.snapshot_id
                or decision.evaluation_hash != evaluation.evaluation_hash
                or decision.policy_hash
                != package.evaluation_policy.policy.policy_hash
            ):
                raise IntegratedEvolutionPackageError(
                    "Evaluation or stop decision differs from canonical snapshot/policy lineage."
                )
            expected = build_composite_stop_decision(
                evaluation,
                package.evaluation_policy.policy,
                decision_id=decision.decision_id,
                actionable_case_ids=decision.actionable_case_ids,
                budget_exhausted=decision.budget_exhausted,
                decided_by=decision.decided_by,
                decided_at=decision.decided_at,
            )
            if expected != decision:
                raise IntegratedEvolutionPackageError(
                    "Persisted stop decision differs from deterministic policy."
                )
        if (
            evaluations[0].evaluation.parent_evaluation_hash is not None
            or evaluations[1].evaluation.parent_evaluation_hash
            != evaluations[0].evaluation.evaluation_hash
            or evaluations[2].evaluation.parent_evaluation_hash
            != evaluations[1].evaluation.evaluation_hash
            or evaluations[2].evaluation.safety_violation_count != 0
            or any(item.evaluation.regression_count for item in evaluations)
        ):
            raise IntegratedEvolutionPackageError(
                "Composite evaluation parent, safety or regression evidence differs."
            )
        cls._verify_event_chain(
            package.evaluation_events,
            package.evaluation_checkpoint,
            SQLiteCompositeEvaluationRepository._event_hash,
            "evaluation",
        )

    @staticmethod
    def _verify_cross_bindings(package) -> None:
        results = {item.track: item for item in package.track_results}
        if set(results) != {
            IntegratedTrack.SKILL,
            IntegratedTrack.LOCAL_POLICY,
        }:
            raise IntegratedEvolutionPackageError(
                "Integrated package lacks one exact result per automatic track."
            )
        snapshots = tuple(
            sorted(
                package.composite_snapshots,
                key=lambda item: item.manifest.round_index,
            )
        )
        decisions = tuple(
            sorted(
                package.stop_decisions,
                key=lambda item: item.decision.round_index,
            )
        )
        if len(snapshots) != 3 or len(decisions) != 3:
            raise IntegratedEvolutionPackageError(
                "Integrated cross-binding requires canonical A0/A1/A2 evidence."
            )
        skill_result = results[IntegratedTrack.SKILL]
        policy_result = results[IntegratedTrack.LOCAL_POLICY]
        if (
            snapshots[1].manifest.source_case_ids != skill_result.case_ids
            or not set(skill_result.source_decision_hashes).issubset(
                snapshots[1].manifest.source_decision_hashes
            )
            or not set(skill_result.source_package_hashes).issubset(
                snapshots[1].manifest.source_package_hashes
            )
            or snapshots[1].manifest.skill.content_hash
            != skill_result.component_hash
            or snapshots[2].manifest.source_case_ids != policy_result.case_ids
            or not set(policy_result.source_decision_hashes).issubset(
                snapshots[2].manifest.source_decision_hashes
            )
            or not set(policy_result.source_package_hashes).issubset(
                snapshots[2].manifest.source_package_hashes
            )
            or snapshots[2].manifest.local_policy.checkpoint_hash
            != policy_result.component_hash
            or package.run.terminal_decision_hash
            != decisions[-1].decision.decision_hash
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated results, snapshots or terminal decision are not cross-bound."
            )

    @staticmethod
    def _verify_event_chain(events, checkpoint, hash_function, label: str) -> None:
        del hash_function  # The label selects the exact persisted event schema.
        if not events:
            raise IntegratedEvolutionPackageError(
                f"{label} audit chain must not be empty."
            )
        if label == "integrated":
            identity = events[0].run_id
            if (
                checkpoint.run_id != identity
                or any(event.run_id != identity for event in events)
            ):
                raise IntegratedEvolutionPackageError(
                    "Integrated audit checkpoint or events belong to another run."
                )
        elif label in {"composite", "evaluation"}:
            identity = events[0].lineage_id
            if (
                checkpoint.lineage_id != identity
                or any(event.lineage_id != identity for event in events)
            ):
                raise IntegratedEvolutionPackageError(
                    f"{label} audit checkpoint or events belong to another lineage."
                )
        else:  # pragma: no cover - internal closed set
            raise IntegratedEvolutionPackageError(
                f"Unknown integrated audit chain label: {label}."
            )

        previous = "0" * 64
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous:
                raise IntegratedEvolutionPackageError(
                    f"{label} audit sequence or previous hash differs."
                )
            if label == "integrated":
                expected_hash = SQLiteIntegratedEvolutionRepository._event_hash(
                    sequence=event.sequence,
                    event_id=event.event_id,
                    run_id=event.run_id,
                    event_type=event.event_type,
                    case_ids=event.case_ids,
                    actor_id=event.actor_id,
                    reason=event.reason,
                    metadata=event.metadata,
                    created_at=event.created_at,
                    previous_hash=event.previous_hash,
                )
            elif label == "composite":
                expected_hash = SQLiteCompositeSnapshotRegistry._event_hash(
                    sequence=event.sequence,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    lineage_id=event.lineage_id,
                    snapshot_id=event.snapshot_id,
                    from_snapshot_id=event.from_snapshot_id,
                    to_snapshot_id=event.to_snapshot_id,
                    reason=event.reason,
                    metadata=event.metadata,
                    actor_id=event.actor_id,
                    created_at=event.created_at,
                    previous_hash=event.previous_hash,
                )
            else:
                expected_hash = SQLiteCompositeEvaluationRepository._event_hash(
                    sequence=event.sequence,
                    event_id=event.event_id,
                    lineage_id=event.lineage_id,
                    snapshot_id=event.snapshot_id,
                    event_type=event.event_type,
                    reason=event.reason,
                    metadata=event.metadata,
                    actor_id=event.actor_id,
                    created_at=event.created_at,
                    previous_hash=event.previous_hash,
                )
            if event.event_hash != expected_hash:
                raise IntegratedEvolutionPackageError(
                    f"{label} audit event content hash differs."
                )
            previous = event.event_hash
        if checkpoint.event_count != len(events) or checkpoint.head_hash != previous:
            raise IntegratedEvolutionPackageError(
                f"{label} audit checkpoint differs from the complete chain."
            )


__all__ = [
    "IntegratedEvolutionPackageError",
    "IntegratedEvolutionPackageManager",
    "IntegratedEvolutionPackageManifest",
]
