from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.integrated import (
    IntegratedDispatchAction,
    IntegratedSupervisorService,
    IntegratedTrack,
)
from tests.test_composite_evaluation_repository import (
    _complete_lineage as _complete_composite_lineage,
    _record_round,
    _repositories as _composite_repositories,
)
from tests.test_composite_evaluation import _a0_outcomes, _a1_outcomes
from tests.test_integrated_repository import (
    POLICY_EXECUTOR,
    RUN_ID,
    SKILL_EXECUTOR,
    _admit_controlled_cases,
    _case,
    _policy_result,
    _repository,
    _skill_result,
)


def _supervisor(repository, *, evaluation_service=None):
    return IntegratedSupervisorService(
        repository,
        skill_executor_id=SKILL_EXECUTOR,
        local_policy_executor_id=POLICY_EXECUTOR,
        evaluation_service=evaluation_service,
    )


def _execute_two_component_rounds(
    supervisor,
    repository,
    policy,
    start,
):
    _admit_controlled_cases(repository, policy, start)
    skill_plan = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:completion-skill",
        planned_at=start + timedelta(seconds=11),
    )
    supervisor.claim_plan(
        skill_plan,
        now=start + timedelta(seconds=11),
    )
    supervisor.record_result(
        _skill_result(start + timedelta(seconds=11)),
        expected_run_revision=1,
        now=start + timedelta(seconds=12),
    )
    policy_plan = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:completion-policy",
        planned_at=start + timedelta(seconds=13),
    )
    supervisor.claim_plan(
        policy_plan,
        now=start + timedelta(seconds=13),
    )
    supervisor.record_result(
        _policy_result(
            start + timedelta(seconds=13),
            policy_plan.case_ids,
        ),
        expected_run_revision=3,
        now=start + timedelta(seconds=14),
    )


def test_supervisor_claims_skill_then_complete_policy_batch(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    _admit_controlled_cases(repository, policy, start)
    supervisor = _supervisor(repository)

    skill_plan = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:skill",
        planned_at=start + timedelta(seconds=11),
    )
    assert skill_plan.action == IntegratedDispatchAction.CLAIM_SKILL
    assert skill_plan.track == IntegratedTrack.SKILL
    assert skill_plan.case_ids == ("case:skill",)
    assert skill_plan.component_mutation_performed is False
    skill_cases = supervisor.claim_plan(
        skill_plan,
        now=start + timedelta(seconds=11),
    )
    assert tuple(item.case.case_id for item in skill_cases) == (
        "case:skill",
    )

    resume = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:resume-skill",
        planned_at=start + timedelta(seconds=12),
    )
    assert resume.action == IntegratedDispatchAction.RESUME_SKILL
    before = tuple(repository.events(RUN_ID))
    assert supervisor.claim_plan(
        resume,
        now=start + timedelta(seconds=12),
    ) == skill_cases
    assert tuple(repository.events(RUN_ID)) == before

    supervisor.record_result(
        _skill_result(start + timedelta(seconds=11)),
        expected_run_revision=1,
        now=start + timedelta(seconds=12),
    )
    policy_plan = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:policy",
        planned_at=start + timedelta(seconds=13),
    )
    assert policy_plan.action == (
        IntegratedDispatchAction.CLAIM_LOCAL_POLICY
    )
    assert policy_plan.track == IntegratedTrack.LOCAL_POLICY
    assert policy_plan.case_ids == (
        "case:policy:a",
        "case:policy:z",
    )
    policy_cases = supervisor.claim_plan(
        policy_plan,
        now=start + timedelta(seconds=13),
    )
    assert tuple(item.case.case_id for item in policy_cases) == (
        "case:policy:a",
        "case:policy:z",
    )
    supervisor.record_result(
        _policy_result(
            start + timedelta(seconds=13),
            policy_plan.case_ids,
        ),
        expected_run_revision=3,
        now=start + timedelta(seconds=14),
    )

    idle = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:idle",
        planned_at=start + timedelta(seconds=15),
    )
    assert idle.action == IntegratedDispatchAction.IDLE
    assert supervisor.claim_plan(idle) == ()
    assert repository.get_run(RUN_ID).round_index == 2


def test_insufficient_policy_evidence_waits_without_claim(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:policy:only-one",
            track=IntegratedTrack.LOCAL_POLICY,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    supervisor = _supervisor(repository)
    before = tuple(repository.events(RUN_ID))

    plan = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:await-policy-evidence",
        planned_at=start + timedelta(seconds=3),
    )

    assert plan.action == IntegratedDispatchAction.AWAIT_POLICY_EVIDENCE
    assert plan.case_ids == ()
    assert plan.executor_id is None
    assert supervisor.claim_plan(plan) == ()
    assert repository.get_run(RUN_ID).revision == 0
    assert tuple(repository.events(RUN_ID)) == before


def test_stale_dispatch_plan_cannot_claim_after_run_moves(tmp_path):
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
    supervisor = _supervisor(repository)
    stale = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:stale",
        planned_at=start + timedelta(seconds=3),
    )
    repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=3),
    )

    with pytest.raises(RuntimeError, match="stale"):
        supervisor.claim_plan(
            stale,
            now=start + timedelta(seconds=4),
        )


def test_result_must_come_from_configured_track_executor(tmp_path):
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
    supervisor = _supervisor(repository)
    plan = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:executor-check",
        planned_at=start + timedelta(seconds=3),
    )
    supervisor.claim_plan(plan, now=start + timedelta(seconds=3))
    invalid = _skill_result(start + timedelta(seconds=3)).model_copy(
        update={"executor_id": "another-skill-executor"}
    )

    with pytest.raises(ValueError, match="configured executor"):
        supervisor.record_result(
            invalid,
            expected_run_revision=1,
            now=start + timedelta(seconds=4),
        )


def test_completion_requires_latest_decision_for_matching_executed_round(
    tmp_path,
):
    composite = _complete_composite_lineage(tmp_path / "composite")
    evaluation_service = composite["service"]
    start = composite["start"]
    repository, policy = _repository(tmp_path / "integrated", start)
    supervisor = _supervisor(
        repository,
        evaluation_service=evaluation_service,
    )
    _execute_two_component_rounds(
        supervisor,
        repository,
        policy,
        start,
    )

    completed = supervisor.complete_from_latest_decision(
        RUN_ID,
        actor_id="integrated-run-completer",
        expected_run_revision=4,
        now=start + timedelta(seconds=20),
    )

    assert completed.status.value == "stopped"
    assert completed.round_index == 2
    terminal = supervisor.plan_next(
        RUN_ID,
        plan_id="integrated-dispatch:terminal",
        planned_at=start + timedelta(seconds=21),
    )
    assert terminal.action == IntegratedDispatchAction.TERMINAL


def test_empty_run_cannot_borrow_completed_a2_decision(tmp_path):
    composite = _complete_composite_lineage(tmp_path / "composite")
    start = composite["start"]
    repository, _ = _repository(tmp_path / "integrated", start)
    supervisor = _supervisor(
        repository,
        evaluation_service=composite["service"],
    )

    with pytest.raises(RuntimeError, match="execution round"):
        supervisor.complete_from_latest_decision(
            RUN_ID,
            actor_id="integrated-run-completer",
            expected_run_revision=0,
            now=start + timedelta(seconds=20),
        )


def test_completion_rejects_decision_for_nonactive_snapshot(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    a0, a1, a2, snapshots, _, evaluation_service = (
        _composite_repositories(tmp_path / "composite", start)
    )
    _record_round(
        evaluation_service,
        outcomes=_a0_outcomes(),
        evaluation_id="composite-evaluation:supervisor:a0",
        decision_id="composite-decision:supervisor:a0",
        actionable_cases=("case:remaining",),
        evaluated_at=start + timedelta(seconds=1),
    )
    snapshots.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=2),
    )
    _record_round(
        evaluation_service,
        outcomes=_a1_outcomes(),
        evaluation_id="composite-evaluation:supervisor:a1",
        decision_id="composite-decision:supervisor:a1",
        actionable_cases=("case:policy",),
        evaluated_at=start + timedelta(seconds=3),
    )
    # Move the composite pointer without evaluating A2. The latest decision is
    # still bound to A1 and therefore cannot complete the integrated run.
    snapshots.commit(
        a2,
        expected_active_revision=1,
        actor_id="independent-composite-policy-committer",
        now=start + timedelta(seconds=4),
    )
    repository, _ = _repository(tmp_path / "integrated", start)
    supervisor = _supervisor(
        repository,
        evaluation_service=evaluation_service,
    )

    with pytest.raises(RuntimeError, match="active composite snapshot"):
        supervisor.complete_from_latest_decision(
            RUN_ID,
            actor_id="integrated-run-completer",
            expected_run_revision=0,
            now=start + timedelta(seconds=20),
        )


def test_completion_actionable_cases_must_match_pending_queue(tmp_path):
    composite = _complete_composite_lineage(tmp_path / "composite")
    start = composite["start"]
    repository, policy = _repository(tmp_path / "integrated", start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=6),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=7),
    )
    supervisor = _supervisor(
        repository,
        evaluation_service=composite["service"],
    )

    with pytest.raises(RuntimeError, match="pending automatic cases"):
        supervisor.complete_from_latest_decision(
            RUN_ID,
            actor_id="integrated-run-completer",
            expected_run_revision=0,
            now=start + timedelta(seconds=20),
        )
