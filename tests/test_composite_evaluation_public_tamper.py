from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.composite import (
    CompositeStopAction,
    CompositeStopDecision,
    CompositeEvaluationService,
    SQLiteCompositeEvaluationRepository,
    SQLiteCompositeSnapshotRegistry,
    build_composite_stop_decision,
    build_composite_stop_policy,
)
from evoagent.model_registry.models import canonical_sha256
from tests.test_composite_evaluation import _a0_outcomes
from tests.test_composite_snapshot_registry import LINEAGE, _lineage


def test_coherently_rehashed_stop_action_fails_public_write_boundary(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, _, _ = _lineage(start)
    snapshots = SQLiteCompositeSnapshotRegistry(tmp_path / "snapshots.db")
    snapshots.register_initial(a0, actor_id=a0.created_by, now=start)
    evaluations = SQLiteCompositeEvaluationRepository(
        tmp_path / "evaluations.db"
    )
    service = CompositeEvaluationService(snapshots, evaluations)
    policy = build_composite_stop_policy(max_rounds=3)
    service.register_policy(
        LINEAGE,
        policy,
        actor_id="independent-composite-policy-registrar",
        now=start,
    )
    evaluation = service.evaluate_active(
        LINEAGE,
        evaluation_id="composite-evaluation:public-action-tamper",
        outcomes=_a0_outcomes(),
        evaluator_id="independent-composite-evaluator",
        evaluated_at=start + timedelta(seconds=1),
        now=start + timedelta(seconds=1),
    ).evaluation
    valid = build_composite_stop_decision(
        evaluation,
        policy,
        decision_id="composite-decision:public-action-tamper",
        actionable_case_ids=("case:remaining",),
        budget_exhausted=False,
        decided_by="independent-composite-stop-controller",
        decided_at=start + timedelta(seconds=1),
    )
    forged_payload = valid.model_dump(mode="json", exclude={"decision_hash"})
    forged_payload["action"] = CompositeStopAction.STOP.value
    forged = CompositeStopDecision.model_construct(
        **{
            **valid.model_dump(),
            "action": CompositeStopAction.STOP,
            "decision_hash": canonical_sha256(forged_payload),
        }
    )

    with pytest.raises(ValueError, match="deterministic policy"):
        evaluations.record_decision(
            forged,
            actor_id=forged.decided_by,
            now=start + timedelta(seconds=1),
        )

    assert evaluations.list_decisions(LINEAGE) == ()
