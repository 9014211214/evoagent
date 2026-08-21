from __future__ import annotations

from datetime import datetime

from evoagent.local_rl.models import (
    LocalRLAction,
    LocalRLCheckpointStatus,
    LocalRLEnvironmentContract,
    LocalRLEvaluationReport,
    LocalRLEvaluationTaskResult,
    LocalRLHyperparameters,
    LocalRLIterationMetrics,
    LocalRLRunManifest,
    LocalRLSelectionDecision,
    LocalRLTask,
    LocalRLTaskKind,
    LocalRLTrainingBudget,
    LocalRLTrainingResult,
    LocalRLTrainingUsage,
    LocalPolicyCheckpoint,
)
from evoagent.model_registry.models import canonical_sha256


def build_local_rl_task(task_id: str, kind: LocalRLTaskKind) -> LocalRLTask:
    payload = {"task_id": task_id, "kind": kind}
    return LocalRLTask(**payload, task_hash=canonical_sha256(payload))


def build_environment_contract() -> LocalRLEnvironmentContract:
    payload = {
        "contract_id": "local-rl:safe-document-mdp",
        "contract_version": "safe-document-mdp-v1",
        "state_keys": (
            "normal:uninspected",
            "normal:inspected",
            "protected:uninspected",
            "protected:inspected",
        ),
        "actions": (
            LocalRLAction.INSPECT,
            LocalRLAction.WRITE,
            LocalRLAction.BLOCK,
        ),
        "max_episode_steps": 2,
        "success_reward": 1.0,
        "unsafe_write_reward": -1.5,
        "wrong_terminal_reward": -1.0,
        "redundant_inspect_reward": -0.25,
        "inspect_reward": 0.0,
    }
    return LocalRLEnvironmentContract(
        **payload,
        contract_hash=canonical_sha256(payload),
    )


def build_hyperparameters(
    *,
    learning_rate: float = 0.4,
    clip_epsilon: float = 0.2,
    entropy_coefficient: float = 0.01,
    max_gradient_norm: float = 1.0,
    update_epochs: int = 6,
    group_size: int = 48,
    seed: int = 17,
    retained_checkpoint_interval: int = 1,
) -> LocalRLHyperparameters:
    payload = {
        "algorithm": "bounded_group_relative_policy_gradient",
        "learning_rate": learning_rate,
        "clip_epsilon": clip_epsilon,
        "entropy_coefficient": entropy_coefficient,
        "max_gradient_norm": max_gradient_norm,
        "update_epochs": update_epochs,
        "group_size": group_size,
        "seed": seed,
        "retained_checkpoint_interval": retained_checkpoint_interval,
    }
    return LocalRLHyperparameters(
        **payload,
        hyperparameter_hash=canonical_sha256(payload),
    )


def build_training_budget(
    *,
    maximum_iterations: int = 80,
    maximum_rollouts: int = 20_000,
    maximum_episode_steps: int = 40_000,
    maximum_parameter_updates: int = 10_000,
    maximum_wall_seconds: float = 30.0,
) -> LocalRLTrainingBudget:
    payload = {
        "maximum_iterations": maximum_iterations,
        "maximum_rollouts": maximum_rollouts,
        "maximum_episode_steps": maximum_episode_steps,
        "maximum_parameter_updates": maximum_parameter_updates,
        "maximum_wall_seconds": maximum_wall_seconds,
    }
    return LocalRLTrainingBudget(
        **payload,
        budget_hash=canonical_sha256(payload),
    )


def build_run_manifest(
    *,
    run_id: str,
    created_at: datetime,
    environment: LocalRLEnvironmentContract,
    training_tasks: tuple[LocalRLTask, ...],
    held_out_tasks: tuple[LocalRLTask, ...],
    hyperparameters: LocalRLHyperparameters,
    budget: LocalRLTrainingBudget,
) -> LocalRLRunManifest:
    payload = {
        "run_id": run_id,
        "created_at": created_at,
        "environment": environment,
        "training_tasks": training_tasks,
        "held_out_tasks": held_out_tasks,
        "hyperparameters": hyperparameters,
        "budget": budget,
        "external_model_call_performed_by_evoagent": False,
        "foundation_model_training_performed": False,
        "gpu_execution_performed": False,
        "network_execution_performed": False,
    }
    return LocalRLRunManifest(
        **payload,
        manifest_hash=canonical_sha256(payload),
    )


def build_checkpoint(
    *,
    checkpoint_id: str,
    run_id: str,
    iteration: int,
    state_keys: tuple[str, ...],
    actions: tuple[LocalRLAction, ...],
    logits: tuple[tuple[float, ...], ...],
    parent_checkpoint_hash: str | None,
    status: LocalRLCheckpointStatus,
) -> LocalPolicyCheckpoint:
    payload = {
        "checkpoint_id": checkpoint_id,
        "run_id": run_id,
        "iteration": iteration,
        "state_keys": state_keys,
        "actions": actions,
        "logits": logits,
        "parent_checkpoint_hash": parent_checkpoint_hash,
        "status": status,
        "artifact_kind": "tiny_tabular_agent_policy",
        "foundation_model_checkpoint": False,
        "language_model_weights": False,
    }
    return LocalPolicyCheckpoint(
        **payload,
        checkpoint_hash=canonical_sha256(payload),
    )


def build_iteration_metrics(**values) -> LocalRLIterationMetrics:
    payload = dict(values)
    return LocalRLIterationMetrics(
        **payload,
        metrics_hash=canonical_sha256(payload),
    )


def build_training_result(
    *,
    run_id: str,
    manifest_hash: str,
    initial_checkpoint: LocalPolicyCheckpoint,
    retained_checkpoints: tuple[LocalPolicyCheckpoint, ...],
    iterations: tuple[LocalRLIterationMetrics, ...],
    usage: LocalRLTrainingUsage,
) -> LocalRLTrainingResult:
    payload = {
        "run_id": run_id,
        "manifest_hash": manifest_hash,
        "initial_checkpoint": initial_checkpoint,
        "retained_checkpoints": retained_checkpoints,
        "iterations": iterations,
        "usage": usage,
        "numeric_parameters_updated": True,
        "local_rollout_training_executed_by_evoagent": True,
        "foundation_model_training_performed": False,
    }
    return LocalRLTrainingResult(
        **payload,
        result_hash=canonical_sha256(payload),
    )


def build_evaluation_task_result(**values) -> LocalRLEvaluationTaskResult:
    payload = dict(values)
    return LocalRLEvaluationTaskResult(
        **payload,
        result_hash=canonical_sha256(payload),
    )


def build_evaluation_report(
    *,
    report_id: str,
    run_id: str,
    checkpoint: LocalPolicyCheckpoint,
    evaluator_id: str,
    task_manifest_hash: str,
    task_results: tuple[LocalRLEvaluationTaskResult, ...],
) -> LocalRLEvaluationReport:
    normal = [item for item in task_results if item.kind == LocalRLTaskKind.NORMAL]
    protected = [
        item for item in task_results if item.kind == LocalRLTaskKind.PROTECTED
    ]
    payload = {
        "report_id": report_id,
        "run_id": run_id,
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_hash": checkpoint.checkpoint_hash,
        "evaluator_id": evaluator_id,
        "task_manifest_hash": task_manifest_hash,
        "task_results": task_results,
        "overall_score": sum(item.success for item in task_results) / len(task_results),
        "normal_score": sum(item.success for item in normal) / len(normal),
        "protected_score": sum(item.success for item in protected) / len(protected),
        "unsafe_action_count": sum(
            item.unsafe_action_count for item in task_results
        ),
        "episode_steps": sum(item.episode_steps for item in task_results),
    }
    return LocalRLEvaluationReport(
        **payload,
        report_hash=canonical_sha256(payload),
    )


def build_selection_decision(**values) -> LocalRLSelectionDecision:
    payload = dict(values)
    return LocalRLSelectionDecision(
        **payload,
        decision_hash=canonical_sha256(payload),
    )


__all__ = [
    "build_checkpoint",
    "build_environment_contract",
    "build_evaluation_report",
    "build_evaluation_task_result",
    "build_hyperparameters",
    "build_iteration_metrics",
    "build_local_rl_task",
    "build_run_manifest",
    "build_selection_decision",
    "build_training_budget",
    "build_training_result",
]
