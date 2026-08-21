from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from evoagent.composite import (
    build_composite_stop_decision,
    build_composite_stop_policy,
)
from evoagent.diagnosis import (
    AttributionReport,
    ExperimentResult,
    ExperimentType,
    LayerScore,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.integrated import (
    IntegratedAuditIntegrityError,
    IntegratedCaseStatus,
    IntegratedRepositoryConflictError,
    IntegratedRunStatus,
    IntegratedTrack,
    SQLiteIntegratedEvolutionRepository,
    StaleIntegratedRevision,
    build_integrated_case,
    build_integrated_run_policy,
    build_integrated_track_result,
)
from tests.test_composite_evaluation import _evaluations
from tests.test_composite_snapshot_registry import LINEAGE


RUN_ID = "integrated-run:controlled-v2.3"
RUN_CREATOR = "integrated-supervisor-controller"
SKILL_EXECUTOR = "integrated-skill-executor"
POLICY_EXECUTOR = "integrated-local-policy-executor"
RUN_COMPLETER = "integrated-run-completer"


def _experiment(
    experiment_id: str,
    experiment_type: ExperimentType,
    layer: FailureLayer,
    *,
    supports: bool,
) -> ExperimentResult:
    return ExperimentResult(
        experiment_id=experiment_id,
        hypothesis_id=f"hypothesis:{layer.value}:{experiment_id}",
        experiment_type=experiment_type,
        baseline_success=False,
        counterfactual_success=supports,
        supports_hypothesis=supports,
        confidence=0.95 if supports else 0.2,
        evidence=[
            (
                f"bounded supporting counterfactual for {experiment_id}"
                if supports
                else f"bounded negative control for {experiment_id}"
            )
        ],
        metadata={"hypothesis_layer": layer.value},
    )


def _report(
    *,
    layer: FailureLayer,
    action: EvolutionAction,
    confidence: float,
    experiment: ExperimentResult,
    actionable: bool = True,
) -> AttributionReport:
    supporting = (
        [experiment.experiment_id]
        if experiment.supports_hypothesis
        else []
    )
    return AttributionReport(
        root_cause_layer=layer,
        confidence=confidence,
        ranked_causes=[
            LayerScore(
                layer=layer,
                score=confidence,
                supporting_experiment_ids=supporting,
            )
        ],
        evidence=["Bounded mixed-track attribution evidence."],
        experiments=[experiment],
        recommended_action=action,
        actionable=actionable,
        reason="The unique observable counterfactual determines the track.",
    )


def _case(
    policy,
    *,
    case_id: str,
    track: IntegratedTrack,
    created_at: datetime,
):
    if track == IntegratedTrack.SKILL:
        report = _report(
            layer=FailureLayer.SKILL,
            action=EvolutionAction.UPDATE_SKILL,
            confidence=0.95,
            experiment=_experiment(
                f"experiment:{case_id}:replace-skill",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
        )
        trust, flags = "verified", ()
    elif track == IntegratedTrack.LOCAL_POLICY:
        report = _report(
            layer=FailureLayer.MODEL,
            action=EvolutionAction.TRAIN_MODEL,
            confidence=0.95,
            experiment=_experiment(
                f"experiment:{case_id}:reference-policy",
                ExperimentType.REFERENCE_MODEL,
                FailureLayer.MODEL,
                supports=True,
            ),
        )
        trust, flags = "verified", ()
    elif track == IntegratedTrack.ESCALATION:
        report = _report(
            layer=FailureLayer.SKILL,
            action=EvolutionAction.UPDATE_SKILL,
            confidence=0.4,
            experiment=_experiment(
                f"experiment:{case_id}:low-confidence",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
        )
        trust, flags = "verified", ()
    else:
        report = _report(
            layer=FailureLayer.SKILL,
            action=EvolutionAction.UPDATE_SKILL,
            confidence=0.95,
            experiment=_experiment(
                f"experiment:{case_id}:untrusted",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
        )
        trust, flags = "untrusted", ("untrusted_trace",)
    return build_integrated_case(
        report,
        policy=policy,
        case_id=case_id,
        trace_id=f"trace:{case_id}",
        task_id=f"task:{case_id}",
        evidence_hash="a" * 64,
        source="controlled-local-runtime",
        trust_level=trust,
        safety_flags=flags,
        created_at=created_at,
    )


def _skill_result(start, case_id="case:skill"):
    return build_integrated_track_result(
        result_id="integrated-result:skill",
        run_id=RUN_ID,
        track=IntegratedTrack.SKILL,
        case_ids=(case_id,),
        source_decision_hashes=("b" * 64,),
        source_package_hashes=("c" * 64,),
        component_ref="skill:document_guard:0.2.0",
        component_hash="d" * 64,
        executor_id=SKILL_EXECUTOR,
        started_at=start,
        completed_at=start + timedelta(seconds=1),
        metrics={"heldout_score": 1.0, "regression_count": 0.0},
        skill_promoted=True,
    )


def _policy_result(start, case_ids):
    return build_integrated_track_result(
        result_id="integrated-result:local-policy",
        run_id=RUN_ID,
        track=IntegratedTrack.LOCAL_POLICY,
        case_ids=case_ids,
        source_decision_hashes=("e" * 64,),
        source_package_hashes=("f" * 64, "1" * 64),
        component_ref="local-policy:document-guard:p1",
        component_hash="2" * 64,
        executor_id=POLICY_EXECUTOR,
        started_at=start,
        completed_at=start + timedelta(seconds=1),
        metrics={
            "heldout_reward_delta": 1.0,
            "heldout_success_delta": 1.0,
            "unsafe_action_count": 0.0,
            "regression_count": 0.0,
        },
        local_policy_optimized=True,
        local_policy_promoted=True,
        local_policy_activated=True,
        rollback_ready=True,
    )


def _stop_decision(start):
    _, (_, _, evaluation) = _evaluations(start)
    return build_composite_stop_decision(
        evaluation,
        build_composite_stop_policy(max_rounds=3),
        decision_id="composite-stop-decision:integrated-final",
        actionable_case_ids=(),
        budget_exhausted=False,
        decided_by="independent-composite-stop-controller",
        decided_at=evaluation.evaluated_at,
    )


def _repository(tmp_path, start):
    repository = SQLiteIntegratedEvolutionRepository(
        tmp_path / "integrated.db"
    )
    policy = build_integrated_run_policy()
    repository.create_run(
        run_id=RUN_ID,
        lineage_id=LINEAGE,
        policy=policy,
        actor_id=RUN_CREATOR,
        now=start,
    )
    return repository, policy


def _admit_controlled_cases(repository, policy, start):
    cases = (
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        # Deliberately admit z before a; execution batches must still use
        # canonical case-ID order.
        _case(
            policy,
            case_id="case:policy:z",
            track=IntegratedTrack.LOCAL_POLICY,
            created_at=start + timedelta(seconds=2),
        ),
        _case(
            policy,
            case_id="case:policy:a",
            track=IntegratedTrack.LOCAL_POLICY,
            created_at=start + timedelta(seconds=3),
        ),
        _case(
            policy,
            case_id="case:escalation",
            track=IntegratedTrack.ESCALATION,
            created_at=start + timedelta(seconds=4),
        ),
        _case(
            policy,
            case_id="case:quarantine",
            track=IntegratedTrack.QUARANTINE,
            created_at=start + timedelta(seconds=5),
        ),
    )
    for index, case in enumerate(cases, start=1):
        repository.admit_case(
            RUN_ID,
            case,
            actor_id="integrated-case-admitter",
            now=start + timedelta(seconds=5 + index),
        )
    return cases


def test_skill_then_complete_policy_batch_then_stop(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    _admit_controlled_cases(repository, policy, start)

    run, skill_cases = repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=11),
    )
    assert run.revision == 1
    assert tuple(item.case.case_id for item in skill_cases) == ("case:skill",)
    run = repository.record_result(
        _skill_result(start + timedelta(seconds=11)),
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=1,
        now=start + timedelta(seconds=12),
    )
    assert (run.revision, run.round_index, run.skill_execution_count) == (
        2,
        1,
        1,
    )

    with pytest.raises(ValueError, match="complete pending evidence batch"):
        repository.claim_cases(
            RUN_ID,
            case_ids=("case:policy:a",),
            track=IntegratedTrack.LOCAL_POLICY,
            actor_id=POLICY_EXECUTOR,
            expected_run_revision=2,
            now=start + timedelta(seconds=13),
        )

    run, policy_cases = repository.claim_cases(
        RUN_ID,
        case_ids=("case:policy:z", "case:policy:a"),
        track=IntegratedTrack.LOCAL_POLICY,
        actor_id=POLICY_EXECUTOR,
        expected_run_revision=2,
        now=start + timedelta(seconds=13),
    )
    assert run.revision == 3
    assert tuple(item.case.case_id for item in policy_cases) == (
        "case:policy:a",
        "case:policy:z",
    )
    run = repository.record_result(
        _policy_result(
            start + timedelta(seconds=13),
            ("case:policy:z", "case:policy:a"),
        ),
        actor_id=POLICY_EXECUTOR,
        expected_run_revision=3,
        now=start + timedelta(seconds=14),
    )
    assert (
        run.revision,
        run.round_index,
        run.skill_execution_count,
        run.policy_execution_count,
    ) == (4, 2, 1, 1)
    assert repository.pending_cases(RUN_ID) == ()
    assert repository.get_case(RUN_ID, "case:escalation").status == (
        IntegratedCaseStatus.ESCALATED
    )
    assert repository.get_case(RUN_ID, "case:quarantine").status == (
        IntegratedCaseStatus.QUARANTINED
    )

    completed = repository.complete_run(
        RUN_ID,
        _stop_decision(start),
        actor_id=RUN_COMPLETER,
        expected_run_revision=4,
        now=start + timedelta(seconds=20),
    )
    assert completed.status == IntegratedRunStatus.STOPPED
    assert completed.revision == 5
    assert repository.checkpoint(RUN_ID).event_count == 11
    assert repository.verify_state(RUN_ID) is True


def test_exact_claim_and_result_retries_are_read_only(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    first_run, first_cases = repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=3),
    )
    before_claim_retry = tuple(repository.events(RUN_ID))

    second_run, second_cases = repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=4),
    )
    assert second_run == first_run
    assert second_cases == first_cases
    assert tuple(repository.events(RUN_ID)) == before_claim_retry

    with pytest.raises(IntegratedRepositoryConflictError, match="retry"):
        repository.claim_cases(
            RUN_ID,
            case_ids=("case:skill",),
            track=IntegratedTrack.SKILL,
            actor_id="different-skill-executor",
            expected_run_revision=0,
            now=start + timedelta(seconds=4),
        )

    result = _skill_result(start + timedelta(seconds=3))
    first_result_run = repository.record_result(
        result,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=1,
        now=start + timedelta(seconds=4),
    )
    before_result_retry = tuple(repository.events(RUN_ID))
    second_result_run = repository.record_result(
        result,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=1,
        now=start + timedelta(seconds=5),
    )
    assert first_result_run == second_result_run
    assert tuple(repository.events(RUN_ID)) == before_result_retry


def test_policy_claim_retry_remains_canonical_after_out_of_order_admission(
    tmp_path,
):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    for index, case_id in enumerate(("case:policy:z", "case:policy:a"), start=1):
        repository.admit_case(
            RUN_ID,
            _case(
                policy,
                case_id=case_id,
                track=IntegratedTrack.LOCAL_POLICY,
                created_at=start + timedelta(seconds=index),
            ),
            actor_id="integrated-case-admitter",
            now=start + timedelta(seconds=index + 2),
        )
    first_run, first_cases = repository.claim_cases(
        RUN_ID,
        case_ids=("case:policy:z", "case:policy:a"),
        track=IntegratedTrack.LOCAL_POLICY,
        actor_id=POLICY_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=5),
    )
    before = tuple(repository.events(RUN_ID))

    second_run, second_cases = repository.claim_cases(
        RUN_ID,
        case_ids=("case:policy:a", "case:policy:z"),
        track=IntegratedTrack.LOCAL_POLICY,
        actor_id=POLICY_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=6),
    )

    assert first_run == second_run
    assert tuple(item.case.case_id for item in first_cases) == (
        "case:policy:a",
        "case:policy:z",
    )
    assert second_cases == first_cases
    assert tuple(repository.events(RUN_ID)) == before


def test_stale_revision_and_wrong_track_claim_fail_closed(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )

    with pytest.raises(StaleIntegratedRevision):
        repository.claim_cases(
            RUN_ID,
            case_ids=("case:skill",),
            track=IntegratedTrack.SKILL,
            actor_id=SKILL_EXECUTOR,
            expected_run_revision=99,
            now=start + timedelta(seconds=3),
        )
    with pytest.raises(IntegratedRepositoryConflictError, match="wrong-track"):
        repository.claim_cases(
            RUN_ID,
            case_ids=("case:skill",),
            track=IntegratedTrack.LOCAL_POLICY,
            actor_id=POLICY_EXECUTOR,
            expected_run_revision=0,
            now=start + timedelta(seconds=3),
        )


def test_stop_rejects_pending_automatic_cases_but_escalate_may_close(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    stop = _stop_decision(start)
    with pytest.raises(IntegratedRepositoryConflictError, match="no pending"):
        repository.complete_run(
            RUN_ID,
            stop,
            actor_id=RUN_COMPLETER,
            expected_run_revision=0,
            now=start + timedelta(seconds=20),
        )

    _, (_, _, evaluation) = _evaluations(start)
    escalation = build_composite_stop_decision(
        evaluation,
        build_composite_stop_policy(max_rounds=3),
        decision_id="composite-stop-decision:integrated-escalation",
        actionable_case_ids=("case:skill",),
        budget_exhausted=True,
        decided_by="independent-composite-stop-controller",
        decided_at=evaluation.evaluated_at,
    )
    completed = repository.complete_run(
        RUN_ID,
        escalation,
        actor_id=RUN_COMPLETER,
        expected_run_revision=0,
        now=start + timedelta(seconds=20),
    )
    assert completed.status == IntegratedRunStatus.ESCALATED
    assert repository.get_case(RUN_ID, "case:skill").status == (
        IntegratedCaseStatus.PENDING
    )


def test_audit_modification_and_tail_truncation_are_detected(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    checkpoint = repository.checkpoint(RUN_ID)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE integrated_audit_events SET reason = ? "
            "WHERE run_id = ? AND sequence = 2",
            ("forged integrated admission semantics", RUN_ID),
        )
        connection.commit()
    with pytest.raises(IntegratedAuditIntegrityError, match="modified"):
        repository.verify_audit(RUN_ID, checkpoint)

    repository, policy = _repository(tmp_path / "tail", start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "DELETE FROM integrated_audit_events "
            "WHERE run_id = ? AND sequence = 2",
            (RUN_ID,),
        )
        connection.commit()
    with pytest.raises(IntegratedAuditIntegrityError, match="omits"):
        repository.verify_state(RUN_ID)
