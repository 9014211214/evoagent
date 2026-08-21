from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.composite import (
    CompositeSnapshotEvaluation,
    CompositeStopAction,
    CompositeTaskOutcome,
    CompositeTaskTrack,
    build_composite_evaluation,
    build_composite_stop_decision,
    build_composite_stop_policy,
)
from tests.test_composite_snapshot_registry import _lineage


SKILL_NORMAL = "composite-task:skill:normal"
SKILL_PROTECTED = "composite-task:skill:protected"
POLICY_NORMAL = "composite-task:policy:normal"
POLICY_PROTECTED = "composite-task:policy:protected"


def _outcome(
    task_id: str,
    track: CompositeTaskTrack,
    *,
    passed: bool,
    unsafe: int = 0,
    suffix: str,
) -> CompositeTaskOutcome:
    return CompositeTaskOutcome(
        task_id=task_id,
        track=track,
        passed=passed,
        score=1.0 if passed else 0.0,
        unsafe_action_count=unsafe,
        tool_calls=1,
        episode_steps=2,
        deterministic_cost=0.01,
        trace_hash=(suffix * 64)[:64],
        verifier_hash="f" * 64,
    )


def _a0_outcomes():
    return (
        _outcome(
            SKILL_NORMAL,
            CompositeTaskTrack.SKILL,
            passed=True,
            suffix="1",
        ),
        _outcome(
            SKILL_PROTECTED,
            CompositeTaskTrack.SKILL,
            passed=False,
            suffix="2",
        ),
        _outcome(
            POLICY_NORMAL,
            CompositeTaskTrack.LOCAL_POLICY,
            passed=False,
            unsafe=1,
            suffix="3",
        ),
        _outcome(
            POLICY_PROTECTED,
            CompositeTaskTrack.LOCAL_POLICY,
            passed=False,
            unsafe=1,
            suffix="4",
        ),
    )


def _a1_outcomes():
    return (
        _outcome(
            SKILL_NORMAL,
            CompositeTaskTrack.SKILL,
            passed=True,
            suffix="5",
        ),
        _outcome(
            SKILL_PROTECTED,
            CompositeTaskTrack.SKILL,
            passed=True,
            suffix="6",
        ),
        _outcome(
            POLICY_NORMAL,
            CompositeTaskTrack.LOCAL_POLICY,
            passed=False,
            unsafe=1,
            suffix="7",
        ),
        _outcome(
            POLICY_PROTECTED,
            CompositeTaskTrack.LOCAL_POLICY,
            passed=False,
            unsafe=1,
            suffix="8",
        ),
    )


def _a2_outcomes(*, unsafe: int = 0):
    return (
        _outcome(
            SKILL_NORMAL,
            CompositeTaskTrack.SKILL,
            passed=True,
            suffix="9",
        ),
        _outcome(
            SKILL_PROTECTED,
            CompositeTaskTrack.SKILL,
            passed=True,
            suffix="a",
        ),
        _outcome(
            POLICY_NORMAL,
            CompositeTaskTrack.LOCAL_POLICY,
            passed=True,
            unsafe=unsafe,
            suffix="b",
        ),
        _outcome(
            POLICY_PROTECTED,
            CompositeTaskTrack.LOCAL_POLICY,
            passed=True,
            suffix="c",
        ),
    )


def _evaluations(start):
    a0, a1, a2 = _lineage(start)
    e0 = build_composite_evaluation(
        a0,
        evaluation_id="composite-evaluation:a0",
        outcomes=_a0_outcomes(),
        evaluator_id="independent-composite-evaluator",
        evaluated_at=start + timedelta(seconds=1),
    )
    e1 = build_composite_evaluation(
        a1,
        evaluation_id="composite-evaluation:a1",
        outcomes=_a1_outcomes(),
        evaluator_id="independent-composite-evaluator",
        evaluated_at=start + timedelta(seconds=2),
        parent=e0,
    )
    e2 = build_composite_evaluation(
        a2,
        evaluation_id="composite-evaluation:a2",
        outcomes=_a2_outcomes(),
        evaluator_id="independent-composite-evaluator",
        evaluated_at=start + timedelta(seconds=3),
        parent=e1,
    )
    return (a0, a1, a2), (e0, e1, e2)


def test_controlled_composite_scores_are_025_050_100():
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, (e0, e1, e2) = _evaluations(start)

    assert (e0.skill_score, e0.local_policy_score, e0.composite_score) == (
        0.5,
        0.0,
        0.25,
    )
    assert (e1.skill_score, e1.local_policy_score, e1.composite_score) == (
        1.0,
        0.0,
        0.5,
    )
    assert (e2.skill_score, e2.local_policy_score, e2.composite_score) == (
        1.0,
        1.0,
        1.0,
    )
    assert (e0.safety_violation_count, e1.safety_violation_count) == (2, 2)
    assert e2.safety_violation_count == 0
    assert e0.regression_count == e1.regression_count == e2.regression_count == 0
    assert e2.parent_evaluation_hash == e1.evaluation_hash


def test_stop_policy_continues_continues_then_stops():
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, (e0, e1, e2) = _evaluations(start)
    policy = build_composite_stop_policy(max_rounds=3)

    d0 = build_composite_stop_decision(
        e0,
        policy,
        decision_id="composite-stop-decision:a0",
        actionable_case_ids=(
            "case:protected-document-skill",
            "case:normal-document-policy",
            "case:protected-document-policy",
        ),
        budget_exhausted=False,
        decided_by="independent-composite-stop-controller",
        decided_at=e0.evaluated_at,
    )
    d1 = build_composite_stop_decision(
        e1,
        policy,
        decision_id="composite-stop-decision:a1",
        actionable_case_ids=(
            "case:normal-document-policy",
            "case:protected-document-policy",
        ),
        budget_exhausted=False,
        decided_by="independent-composite-stop-controller",
        decided_at=e1.evaluated_at,
    )
    d2 = build_composite_stop_decision(
        e2,
        policy,
        decision_id="composite-stop-decision:a2",
        actionable_case_ids=(),
        budget_exhausted=False,
        decided_by="independent-composite-stop-controller",
        decided_at=e2.evaluated_at,
    )

    assert (d0.action, d1.action, d2.action) == (
        CompositeStopAction.CONTINUE,
        CompositeStopAction.CONTINUE,
        CompositeStopAction.STOP,
    )
    assert d2.production_activation_authorized is False
    assert d2.production_deployment_authorized is False


def test_final_round_or_budget_exhaustion_escalates():
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    (a0, a1, a2), (e0, e1, _) = _evaluations(start)
    failed_final = build_composite_evaluation(
        a2,
        evaluation_id="composite-evaluation:failed-final",
        outcomes=_a1_outcomes(),
        evaluator_id="independent-composite-evaluator",
        evaluated_at=start + timedelta(seconds=3),
        parent=e1,
    )
    policy = build_composite_stop_policy(max_rounds=3)

    final_decision = build_composite_stop_decision(
        failed_final,
        policy,
        decision_id="composite-stop-decision:failed-final",
        actionable_case_ids=("case:remaining-policy",),
        budget_exhausted=False,
        decided_by="independent-composite-stop-controller",
        decided_at=failed_final.evaluated_at,
    )
    budget_decision = build_composite_stop_decision(
        e0,
        policy,
        decision_id="composite-stop-decision:budget-exhausted",
        actionable_case_ids=("case:skill",),
        budget_exhausted=True,
        decided_by="independent-composite-stop-controller",
        decided_at=e0.evaluated_at,
    )

    assert final_decision.action == CompositeStopAction.ESCALATE
    assert budget_decision.action == CompositeStopAction.ESCALATE


def test_perfect_score_with_safety_violation_cannot_stop():
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    (a0, a1, a2), (e0, e1, _) = _evaluations(start)
    unsafe = build_composite_evaluation(
        a2,
        evaluation_id="composite-evaluation:unsafe-perfect-score",
        outcomes=_a2_outcomes(unsafe=1),
        evaluator_id="independent-composite-evaluator",
        evaluated_at=start + timedelta(seconds=3),
        parent=e1,
    )
    decision = build_composite_stop_decision(
        unsafe,
        build_composite_stop_policy(max_rounds=3),
        decision_id="composite-stop-decision:unsafe-perfect-score",
        actionable_case_ids=(),
        budget_exhausted=False,
        decided_by="independent-composite-stop-controller",
        decided_at=unsafe.evaluated_at,
    )

    assert unsafe.composite_score == 1.0
    assert unsafe.safety_violation_count == 1
    assert decision.action == CompositeStopAction.ESCALATE


def test_regressions_and_frozen_task_set_changes_are_derived_or_rejected():
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    (a0, a1, _), (e0, _, _) = _evaluations(start)
    regressed = list(_a1_outcomes())
    regressed[0] = _outcome(
        SKILL_NORMAL,
        CompositeTaskTrack.SKILL,
        passed=False,
        suffix="d",
    )
    e1 = build_composite_evaluation(
        a1,
        evaluation_id="composite-evaluation:regressed-a1",
        outcomes=tuple(regressed),
        evaluator_id="independent-composite-evaluator",
        evaluated_at=start + timedelta(seconds=2),
        parent=e0,
    )
    assert e1.regression_count == 1

    changed_tasks = tuple(
        item
        for item in _a1_outcomes()
        if item.task_id != SKILL_NORMAL
    ) + (
        _outcome(
            "composite-task:skill:replacement",
            CompositeTaskTrack.SKILL,
            passed=True,
            suffix="e",
        ),
    )
    with pytest.raises(ValueError, match="frozen Task set"):
        build_composite_evaluation(
            a1,
            evaluation_id="composite-evaluation:changed-task-set",
            outcomes=changed_tasks,
            evaluator_id="independent-composite-evaluator",
            evaluated_at=start + timedelta(seconds=2),
            parent=e0,
        )


def test_score_tamper_is_rejected_before_outer_hash_acceptance():
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    _, (evaluation, _, _) = _evaluations(start)
    forged = evaluation.model_dump(mode="json")
    forged["skill_score"] = 1.0

    with pytest.raises(ValueError, match="Skill score"):
        CompositeSnapshotEvaluation.model_validate(forged)
