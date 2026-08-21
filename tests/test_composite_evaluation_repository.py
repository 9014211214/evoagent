from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from evoagent.composite import (
    CompositeEvaluationAuditIntegrityError,
    CompositeEvaluationConflictError,
    CompositeEvaluationRoleError,
    CompositeEvaluationService,
    CompositeStopAction,
    SQLiteCompositeEvaluationRepository,
    SQLiteCompositeSnapshotRegistry,
    build_composite_stop_policy,
)
from evoagent.model_registry.models import canonical_sha256
from tests.test_composite_evaluation import (
    _a0_outcomes,
    _a1_outcomes,
    _a2_outcomes,
)
from tests.test_composite_snapshot_registry import LINEAGE, _lineage


POLICY_REGISTRAR = "independent-composite-policy-registrar"
EVALUATOR = "independent-composite-evaluator"
DECIDER = "independent-composite-stop-controller"


def _repositories(tmp_path, start):
    a0, a1, a2 = _lineage(start)
    snapshots = SQLiteCompositeSnapshotRegistry(tmp_path / "snapshots.db")
    snapshots.register_initial(a0, actor_id=a0.created_by, now=start)
    evaluations = SQLiteCompositeEvaluationRepository(
        tmp_path / "evaluations.db"
    )
    service = CompositeEvaluationService(snapshots, evaluations)
    service.register_policy(
        LINEAGE,
        build_composite_stop_policy(max_rounds=3),
        actor_id=POLICY_REGISTRAR,
        now=start,
    )
    return a0, a1, a2, snapshots, evaluations, service


def _record_round(
    service,
    *,
    outcomes,
    evaluation_id,
    decision_id,
    actionable_cases,
    evaluated_at,
):
    evaluation = service.evaluate_active(
        LINEAGE,
        evaluation_id=evaluation_id,
        outcomes=outcomes,
        evaluator_id=EVALUATOR,
        evaluated_at=evaluated_at,
        now=evaluated_at,
    )
    decision = service.decide_active(
        LINEAGE,
        decision_id=decision_id,
        actionable_case_ids=actionable_cases,
        budget_exhausted=False,
        decided_by=DECIDER,
        decided_at=evaluated_at,
        now=evaluated_at,
    )
    return evaluation, decision


def _complete_lineage(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, a1, a2, snapshots, evaluations, service = _repositories(
        tmp_path,
        start,
    )
    e0, d0 = _record_round(
        service,
        outcomes=_a0_outcomes(),
        evaluation_id="composite-evaluation:repository:a0",
        decision_id="composite-decision:repository:a0",
        actionable_cases=(
            "case:protected-document-skill",
            "case:normal-document-policy",
            "case:protected-document-policy",
        ),
        evaluated_at=start + timedelta(seconds=1),
    )
    snapshots.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=2),
    )
    e1, d1 = _record_round(
        service,
        outcomes=_a1_outcomes(),
        evaluation_id="composite-evaluation:repository:a1",
        decision_id="composite-decision:repository:a1",
        actionable_cases=(
            "case:normal-document-policy",
            "case:protected-document-policy",
        ),
        evaluated_at=start + timedelta(seconds=3),
    )
    snapshots.commit(
        a2,
        expected_active_revision=1,
        actor_id="independent-composite-policy-committer",
        now=start + timedelta(seconds=4),
    )
    e2, d2 = _record_round(
        service,
        outcomes=_a2_outcomes(),
        evaluation_id="composite-evaluation:repository:a2",
        decision_id="composite-decision:repository:a2",
        actionable_cases=(),
        evaluated_at=start + timedelta(seconds=5),
    )
    return {
        "start": start,
        "snapshots": snapshots,
        "evaluations": evaluations,
        "service": service,
        "evaluation_records": (e0, e1, e2),
        "decision_records": (d0, d1, d2),
    }


def test_persistent_a0_a1_a2_evaluations_and_stop_decisions(tmp_path):
    context = _complete_lineage(tmp_path)
    evaluations = context["evaluations"]
    service = context["service"]

    assert tuple(
        item.evaluation.composite_score
        for item in evaluations.list_evaluations(LINEAGE)
    ) == (0.25, 0.5, 1.0)
    assert tuple(
        item.decision.action
        for item in evaluations.list_decisions(LINEAGE)
    ) == (
        CompositeStopAction.CONTINUE,
        CompositeStopAction.CONTINUE,
        CompositeStopAction.STOP,
    )
    assert evaluations.checkpoint(LINEAGE).event_count == 7
    assert evaluations.verify_state(LINEAGE) is True
    assert service.verify_state(LINEAGE) is True
    assert service.latest_evaluation(LINEAGE).composite_score == 1.0
    assert service.latest_decision(LINEAGE).action == CompositeStopAction.STOP


def test_exact_evaluation_and_decision_retries_are_read_only(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, _, _, _, evaluations, service = _repositories(tmp_path, start)
    evaluation_time = start + timedelta(seconds=1)
    first_evaluation, first_decision = _record_round(
        service,
        outcomes=_a0_outcomes(),
        evaluation_id="composite-evaluation:retry:a0",
        decision_id="composite-decision:retry:a0",
        actionable_cases=("case:skill", "case:policy"),
        evaluated_at=evaluation_time,
    )
    before = tuple(evaluations.events(LINEAGE))

    second_evaluation = service.evaluate_active(
        LINEAGE,
        evaluation_id="composite-evaluation:retry:a0",
        outcomes=_a0_outcomes(),
        evaluator_id=EVALUATOR,
        evaluated_at=evaluation_time,
        now=evaluation_time + timedelta(seconds=1),
    )
    second_decision = service.decide_active(
        LINEAGE,
        decision_id="composite-decision:retry:a0",
        actionable_case_ids=("case:skill", "case:policy"),
        budget_exhausted=False,
        decided_by=DECIDER,
        decided_at=evaluation_time,
        now=evaluation_time + timedelta(seconds=1),
    )

    assert second_evaluation == first_evaluation
    assert second_decision == first_decision
    assert tuple(evaluations.events(LINEAGE)) == before


def test_parent_decision_is_required_before_child_evaluation(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, a1, _, snapshots, _, service = _repositories(tmp_path, start)
    service.evaluate_active(
        LINEAGE,
        evaluation_id="composite-evaluation:undecided-parent",
        outcomes=_a0_outcomes(),
        evaluator_id=EVALUATOR,
        evaluated_at=start + timedelta(seconds=1),
        now=start + timedelta(seconds=1),
    )
    snapshots.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=2),
    )

    with pytest.raises(
        CompositeEvaluationConflictError,
        match="parent stop decision",
    ):
        service.evaluate_active(
            LINEAGE,
            evaluation_id="composite-evaluation:child-before-parent-decision",
            outcomes=_a1_outcomes(),
            evaluator_id=EVALUATOR,
            evaluated_at=start + timedelta(seconds=3),
            now=start + timedelta(seconds=3),
        )


def test_terminal_parent_decision_prevents_later_round(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, a1, _, snapshots, evaluations, _ = _repositories(tmp_path, start)
    terminal_service = CompositeEvaluationService(snapshots, evaluations)
    # Replace the empty policy Repository with a one-round frozen policy in a
    # separate lineage database to demonstrate bounded termination.
    terminal_evaluations = SQLiteCompositeEvaluationRepository(
        tmp_path / "terminal-evaluations.db"
    )
    terminal_service = CompositeEvaluationService(
        snapshots,
        terminal_evaluations,
    )
    terminal_service.register_policy(
        LINEAGE,
        build_composite_stop_policy(
            policy_id="composite-stop-policy:one-round",
            max_rounds=1,
        ),
        actor_id=POLICY_REGISTRAR,
        now=start,
    )
    _record_round(
        terminal_service,
        outcomes=_a0_outcomes(),
        evaluation_id="composite-evaluation:terminal-parent",
        decision_id="composite-decision:terminal-parent",
        actionable_cases=("case:remaining",),
        evaluated_at=start + timedelta(seconds=1),
    )
    assert terminal_service.latest_decision(LINEAGE).action == (
        CompositeStopAction.ESCALATE
    )
    snapshots.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=2),
    )

    with pytest.raises(
        CompositeEvaluationConflictError,
        match="terminal decision",
    ):
        terminal_service.evaluate_active(
            LINEAGE,
            evaluation_id="composite-evaluation:after-terminal",
            outcomes=_a1_outcomes(),
            evaluator_id=EVALUATOR,
            evaluated_at=start + timedelta(seconds=3),
            now=start + timedelta(seconds=3),
        )


def test_evaluator_and_stop_decider_roles_are_separated(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, _, _, _, _, service = _repositories(tmp_path, start)

    with pytest.raises(CompositeEvaluationRoleError, match="evaluator"):
        service.evaluate_active(
            LINEAGE,
            evaluation_id="composite-evaluation:invalid-evaluator",
            outcomes=_a0_outcomes(),
            evaluator_id=a0.created_by,
            evaluated_at=start + timedelta(seconds=1),
            now=start + timedelta(seconds=1),
        )

    service.evaluate_active(
        LINEAGE,
        evaluation_id="composite-evaluation:valid-evaluator",
        outcomes=_a0_outcomes(),
        evaluator_id=EVALUATOR,
        evaluated_at=start + timedelta(seconds=1),
        now=start + timedelta(seconds=1),
    )
    with pytest.raises(CompositeEvaluationRoleError, match="stop decider"):
        service.decide_active(
            LINEAGE,
            decision_id="composite-decision:invalid-decider",
            actionable_case_ids=("case:remaining",),
            budget_exhausted=False,
            decided_by=EVALUATOR,
            decided_at=start + timedelta(seconds=1),
            now=start + timedelta(seconds=1),
        )


def test_coherently_rehashed_decision_action_is_rejected(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, _, _, _, evaluations, service = _repositories(tmp_path, start)
    service.evaluate_active(
        LINEAGE,
        evaluation_id="composite-evaluation:decision-tamper",
        outcomes=_a0_outcomes(),
        evaluator_id=EVALUATOR,
        evaluated_at=start + timedelta(seconds=1),
        now=start + timedelta(seconds=1),
    )
    valid = service.decide_active(
        LINEAGE,
        decision_id="composite-decision:decision-tamper",
        actionable_case_ids=("case:remaining",),
        budget_exhausted=False,
        decided_by=DECIDER,
        decided_at=start + timedelta(seconds=1),
        now=start + timedelta(seconds=1),
    ).decision
    payload = valid.model_dump(mode="json", exclude={"decision_hash"})
    payload["action"] = CompositeStopAction.STOP.value
    forged = valid.model_copy(
        update={
            "action": CompositeStopAction.STOP,
            "decision_hash": canonical_sha256(payload),
        }
    )

    # Use a separate snapshot decision row so the immutable-ID conflict does
    # not mask deterministic-policy verification.
    with pytest.raises(ValueError, match="deterministic policy"):
        evaluations._verify_decision(
            service.latest_evaluation(LINEAGE),
            evaluations.policy(LINEAGE).policy,
            forged,
        )


def test_audit_modification_and_tail_truncation_are_detected(tmp_path):
    context = _complete_lineage(tmp_path)
    repository = context["evaluations"]
    checkpoint = repository.checkpoint(LINEAGE)

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE composite_evaluation_audit_events SET reason = ? "
            "WHERE lineage_id = ? AND sequence = 2",
            ("forged evaluation semantics", LINEAGE),
        )
        connection.commit()
    with pytest.raises(
        CompositeEvaluationAuditIntegrityError,
        match="modified",
    ):
        repository.verify_audit(LINEAGE, checkpoint)

    context = _complete_lineage(tmp_path / "tail")
    repository = context["evaluations"]
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "DELETE FROM composite_evaluation_audit_events "
            "WHERE lineage_id = ? AND sequence = 7",
            (LINEAGE,),
        )
        connection.commit()
    with pytest.raises(
        CompositeEvaluationAuditIntegrityError,
        match="omits",
    ):
        repository.verify_state(LINEAGE)
