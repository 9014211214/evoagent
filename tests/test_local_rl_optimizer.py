import math

import pytest

from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import (
    IndependentLocalPolicyEvaluator,
    LocalGroupRelativePolicyOptimizer,
    LocalRLError,
    LocalRLTaskKind,
    TabularSoftmaxPolicy,
    build_local_rl_task,
    build_run_manifest,
    build_training_budget,
)


def test_optimizer_updates_numeric_parameters_deterministically(tmp_path):
    manifest = LocalAgenticRLTrainingLab(tmp_path).build_manifest()
    first = LocalGroupRelativePolicyOptimizer().train(manifest)
    second = LocalGroupRelativePolicyOptimizer().train(manifest)

    assert first == second
    assert first.numeric_parameters_updated is True
    assert first.local_rollout_training_executed_by_evoagent is True
    assert first.foundation_model_training_performed is False
    assert first.initial_checkpoint.checkpoint_hash != (
        first.retained_checkpoints[-1].checkpoint_hash
    )
    assert first.usage.iterations == 24
    assert first.usage.rollouts == 24 * 4 * 24
    assert first.usage.parameter_updates == 24 * 4
    assert first.usage.fits(manifest.budget) is True
    assert all(metric.parameter_delta_l2 >= 0.0 for metric in first.iterations)
    assert any(metric.parameter_delta_l2 > 0.0 for metric in first.iterations)
    assert all(math.isfinite(value) for checkpoint in first.retained_checkpoints for row in checkpoint.logits for value in row)


def test_optimizer_learns_safe_greedy_actions_on_disjoint_tasks(tmp_path):
    manifest = LocalAgenticRLTrainingLab(tmp_path).build_manifest()
    training = LocalGroupRelativePolicyOptimizer().train(manifest)
    evaluator = IndependentLocalPolicyEvaluator()
    baseline = evaluator.evaluate(
        manifest,
        training.initial_checkpoint,
        evaluator_id="independent-evaluator",
        trainer_id="trainer",
    )
    reports = [
        evaluator.evaluate(
            manifest,
            checkpoint,
            evaluator_id="independent-evaluator",
            trainer_id="trainer",
        )
        for checkpoint in training.retained_checkpoints
    ]

    assert baseline.overall_score == 0.0
    assert baseline.unsafe_action_count > 0
    assert max(report.overall_score for report in reports) == 1.0
    best = next(report for report in reports if report.overall_score == 1.0)
    assert best.normal_score == 1.0
    assert best.protected_score == 1.0
    assert best.unsafe_action_count == 0


def test_static_budget_exhaustion_fails_before_rollout(tmp_path):
    lab = LocalAgenticRLTrainingLab(tmp_path)
    manifest = lab.build_manifest()
    tiny_budget = build_training_budget(
        maximum_iterations=manifest.budget.maximum_iterations,
        maximum_rollouts=1,
        maximum_episode_steps=1,
        maximum_parameter_updates=1,
        maximum_wall_seconds=30.0,
    )
    payload = manifest.model_dump(exclude={"manifest_hash"})
    payload["budget"] = tiny_budget
    from evoagent.model_registry.models import canonical_sha256

    constrained = build_run_manifest(
        run_id=manifest.run_id,
        created_at=manifest.created_at,
        environment=manifest.environment,
        training_tasks=manifest.training_tasks,
        held_out_tasks=manifest.held_out_tasks,
        hyperparameters=manifest.hyperparameters,
        budget=tiny_budget,
    )
    with pytest.raises(LocalRLError, match="planned rollouts"):
        LocalGroupRelativePolicyOptimizer().train(constrained)


def test_train_heldout_leakage_is_rejected(tmp_path):
    manifest = LocalAgenticRLTrainingLab(tmp_path).build_manifest()
    duplicated = build_local_rl_task(
        manifest.training_tasks[0].task_id,
        LocalRLTaskKind.NORMAL,
    )
    with pytest.raises(ValueError, match="must be disjoint"):
        build_run_manifest(
            run_id=manifest.run_id,
            created_at=manifest.created_at,
            environment=manifest.environment,
            training_tasks=manifest.training_tasks,
            held_out_tasks=(duplicated, *manifest.held_out_tasks[1:]),
            hyperparameters=manifest.hyperparameters,
            budget=manifest.budget,
        )


def test_nonfinite_parameters_and_nonindependent_evaluator_are_rejected(tmp_path):
    manifest = LocalAgenticRLTrainingLab(tmp_path).build_manifest()
    with pytest.raises(ValueError, match="finite"):
        TabularSoftmaxPolicy(
            state_keys=manifest.environment.state_keys,
            actions=manifest.environment.actions,
            logits=((float("nan"), 0.0, 0.0),) * 4,
        )

    training = LocalGroupRelativePolicyOptimizer().train(manifest)
    with pytest.raises(LocalRLError, match="independent"):
        IndependentLocalPolicyEvaluator().evaluate(
            manifest,
            training.initial_checkpoint,
            evaluator_id="same-actor",
            trainer_id="same-actor",
        )
