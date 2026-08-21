from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.integrated import (
    GovernedLocalPolicyEvolutionExecutor,
    GovernedSkillEvolutionExecutor,
    IntegratedExecutorEvidenceError,
    IntegratedTrack,
)
from tests.test_integrated_repository import (
    POLICY_EXECUTOR,
    RUN_ID,
    SKILL_EXECUTOR,
    _case,
    _repository,
)


def test_real_skill_executor_is_stable_after_child_completion(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path / "queue", start)
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
    _, claimed = repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=3),
    )
    executor = GovernedSkillEvolutionExecutor(tmp_path / "skill-executor")

    first = executor.execute(RUN_ID, claimed)
    second = executor.execute(RUN_ID, claimed)

    assert executor.executor_id == SKILL_EXECUTOR
    assert first == second
    assert first.track == IntegratedTrack.SKILL
    assert first.case_ids == ("case:skill",)
    assert first.skill_promoted is True
    assert first.local_policy_optimized is False
    assert first.component_ref.startswith("skill:")
    assert first.component_hash != "0" * 64
    assert first.metrics["initial_score"] == 0.5
    assert first.metrics["final_score"] == 1.0
    assert first.metrics["regression_count"] == 0.0
    assert first.foundation_model_weights_updated is False
    assert first.production_activation_performed is False
    assert first.production_deployment_performed is False
    assert first.external_execution_performed is False


def test_real_policy_executor_runs_optimizer_acceptance_and_v2_2_promotion(
    tmp_path,
):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path / "queue", start)
    for index, case_id in enumerate(
        ("case:policy:protected", "case:policy:normal"),
        start=1,
    ):
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
    _, claimed = repository.claim_cases(
        RUN_ID,
        case_ids=("case:policy:protected", "case:policy:normal"),
        track=IntegratedTrack.LOCAL_POLICY,
        actor_id=POLICY_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=5),
    )
    executor = GovernedLocalPolicyEvolutionExecutor(
        tmp_path / "policy-executor",
        source_commit="a" * 40,
    )

    first = executor.execute(RUN_ID, claimed)
    second = executor.execute(RUN_ID, claimed)

    assert executor.executor_id == POLICY_EXECUTOR
    assert first == second
    assert first.track == IntegratedTrack.LOCAL_POLICY
    assert first.case_ids == (
        "case:policy:normal",
        "case:policy:protected",
    )
    assert first.skill_promoted is False
    assert first.local_policy_optimized is True
    assert first.local_policy_promoted is True
    assert first.local_policy_activated is True
    assert first.rollback_ready is True
    assert first.component_ref.startswith("local-policy:")
    assert first.metrics["heldout_reward_delta"] > 0.0
    assert first.metrics["heldout_success_delta"] > 0.0
    assert first.metrics["unsafe_action_count"] == 0.0
    assert first.metrics["regression_count"] == 0.0
    assert first.metrics["optimizer_iterations"] > 0.0
    assert first.metrics["optimizer_rollouts"] > 0.0
    assert len(first.source_package_hashes) == 2
    assert first.foundation_model_weights_updated is False
    assert first.production_activation_performed is False
    assert first.production_deployment_performed is False
    assert first.external_execution_performed is False


def test_executors_reject_wrong_claimed_batch(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path / "queue", start)
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
    _, claimed = repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=3),
    )

    with pytest.raises(
        IntegratedExecutorEvidenceError,
        match="another claimed evidence batch",
    ):
        GovernedLocalPolicyEvolutionExecutor(
            tmp_path / "wrong-policy-executor",
            source_commit="b" * 40,
        ).execute(RUN_ID, claimed)
