from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class LocalRLError(ValueError):
    pass


def _require_timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class LocalRLTaskKind(str, Enum):
    NORMAL = "normal"
    PROTECTED = "protected"


class LocalRLAction(str, Enum):
    INSPECT = "inspect"
    WRITE = "write"
    BLOCK = "block"


class LocalRLCheckpointStatus(str, Enum):
    INITIAL = "initial"
    RETAINED = "retained"
    SELECTED = "selected"
    REJECTED = "rejected"


class LocalRLEventType(str, Enum):
    RUN_REGISTERED = "run_registered"
    TRAINING_COMPLETED = "training_completed"
    EVALUATION_STORED = "evaluation_stored"
    SELECTION_STORED = "selection_stored"


class LocalRLTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(pattern=_SAFE_ID_PATTERN)
    kind: LocalRLTaskKind
    task_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"task_hash"})
        if self.task_hash != canonical_sha256(payload):
            raise ValueError("Local RL Task hash mismatch.")
        return self


class LocalRLEnvironmentContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_id: str = Field(pattern=_SAFE_ID_PATTERN)
    contract_version: Literal["safe-document-mdp-v1"] = "safe-document-mdp-v1"
    state_keys: tuple[str, ...]
    actions: tuple[LocalRLAction, ...]
    max_episode_steps: int = Field(default=2, ge=2, le=8)
    success_reward: float = 1.0
    unsafe_write_reward: float = -1.5
    wrong_terminal_reward: float = -1.0
    redundant_inspect_reward: float = -0.25
    inspect_reward: float = 0.0
    contract_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "success_reward",
        "unsafe_write_reward",
        "wrong_terminal_reward",
        "redundant_inspect_reward",
        "inspect_reward",
    )
    @classmethod
    def validate_rewards(cls, value: float) -> float:
        return _finite(value, label="Local RL reward")

    @model_validator(mode="after")
    def validate_contract(self):
        expected_states = (
            "normal:uninspected",
            "normal:inspected",
            "protected:uninspected",
            "protected:inspected",
        )
        expected_actions = (
            LocalRLAction.INSPECT,
            LocalRLAction.WRITE,
            LocalRLAction.BLOCK,
        )
        if self.state_keys != expected_states:
            raise ValueError("Local RL environment state contract changed.")
        if self.actions != expected_actions:
            raise ValueError("Local RL environment action contract changed.")
        if not (
            self.success_reward > self.inspect_reward
            and self.unsafe_write_reward < self.wrong_terminal_reward <= 0.0
            and self.redundant_inspect_reward <= 0.0
        ):
            raise ValueError("Local RL reward ordering is unsafe or ambiguous.")
        payload = self.model_dump(mode="json", exclude={"contract_hash"})
        if self.contract_hash != canonical_sha256(payload):
            raise ValueError("Local RL environment contract hash mismatch.")
        return self


class LocalRLHyperparameters(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: Literal["bounded_group_relative_policy_gradient"] = (
        "bounded_group_relative_policy_gradient"
    )
    learning_rate: float = Field(default=0.4, gt=0.0, le=2.0)
    clip_epsilon: float = Field(default=0.2, gt=0.0, le=1.0)
    entropy_coefficient: float = Field(default=0.01, ge=0.0, le=1.0)
    max_gradient_norm: float = Field(default=1.0, gt=0.0, le=100.0)
    update_epochs: int = Field(default=6, ge=1, le=64)
    group_size: int = Field(default=48, ge=4, le=512)
    seed: int = Field(default=17, ge=0)
    retained_checkpoint_interval: int = Field(default=1, ge=1)
    hyperparameter_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "learning_rate",
        "clip_epsilon",
        "entropy_coefficient",
        "max_gradient_norm",
    )
    @classmethod
    def validate_finite(cls, value: float) -> float:
        return _finite(value, label="Local RL hyperparameter")

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"hyperparameter_hash"})
        if self.hyperparameter_hash != canonical_sha256(payload):
            raise ValueError("Local RL hyperparameter hash mismatch.")
        return self


class LocalRLTrainingBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    maximum_iterations: int = Field(default=80, ge=1, le=10_000)
    maximum_rollouts: int = Field(default=20_000, ge=1)
    maximum_episode_steps: int = Field(default=40_000, ge=1)
    maximum_parameter_updates: int = Field(default=10_000, ge=1)
    maximum_wall_seconds: float = Field(default=30.0, gt=0.0, le=3_600.0)
    budget_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("maximum_wall_seconds")
    @classmethod
    def validate_wall_seconds(cls, value: float) -> float:
        return _finite(value, label="Local RL wall budget")

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"budget_hash"})
        if self.budget_hash != canonical_sha256(payload):
            raise ValueError("Local RL training budget hash mismatch.")
        return self


class LocalRLTrainingUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    iterations: int = Field(ge=0)
    rollouts: int = Field(ge=0)
    episode_steps: int = Field(ge=0)
    parameter_updates: int = Field(ge=0)
    wall_clock_limit_enforced: Literal[True] = True
    budget_exceeded: Literal[False] = False

    def fits(self, budget: LocalRLTrainingBudget) -> bool:
        return (
            self.iterations <= budget.maximum_iterations
            and self.rollouts <= budget.maximum_rollouts
            and self.episode_steps <= budget.maximum_episode_steps
            and self.parameter_updates <= budget.maximum_parameter_updates
        )


class LocalRLRunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    environment: LocalRLEnvironmentContract
    training_tasks: tuple[LocalRLTask, ...]
    held_out_tasks: tuple[LocalRLTask, ...]
    hyperparameters: LocalRLHyperparameters
    budget: LocalRLTrainingBudget
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    external_model_call_performed_by_evoagent: Literal[False] = False
    foundation_model_training_performed: Literal[False] = False
    gpu_execution_performed: Literal[False] = False
    network_execution_performed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Local RL manifest time")

    @model_validator(mode="after")
    def validate_manifest(self):
        train_ids = [item.task_id for item in self.training_tasks]
        held_ids = [item.task_id for item in self.held_out_tasks]
        if not train_ids or not held_ids:
            raise ValueError("Local RL requires training and held-out Tasks.")
        if len(set(train_ids)) != len(train_ids) or len(set(held_ids)) != len(held_ids):
            raise ValueError("Local RL Task IDs must be unique within each split.")
        if set(train_ids) & set(held_ids):
            raise ValueError("Local RL training and held-out Task IDs must be disjoint.")
        for tasks, label in (
            (self.training_tasks, "training"),
            (self.held_out_tasks, "held-out"),
        ):
            kinds = {item.kind for item in tasks}
            if kinds != {LocalRLTaskKind.NORMAL, LocalRLTaskKind.PROTECTED}:
                raise ValueError(f"Local RL {label} split must cover both Task kinds.")
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        validate_safe_content(payload)
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("Local RL run manifest hash mismatch.")
        return self


class LocalPolicyCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    checkpoint_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    iteration: int = Field(ge=0)
    state_keys: tuple[str, ...]
    actions: tuple[LocalRLAction, ...]
    logits: tuple[tuple[float, ...], ...]
    parent_checkpoint_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    status: LocalRLCheckpointStatus
    checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    artifact_kind: Literal["tiny_tabular_agent_policy"] = "tiny_tabular_agent_policy"
    foundation_model_checkpoint: Literal[False] = False
    language_model_weights: Literal[False] = False

    @model_validator(mode="after")
    def validate_checkpoint(self):
        if len(self.logits) != len(self.state_keys):
            raise ValueError("Local policy state/logit dimensions differ.")
        if not self.actions:
            raise ValueError("Local policy requires actions.")
        for row in self.logits:
            if len(row) != len(self.actions):
                raise ValueError("Local policy action/logit dimensions differ.")
            for value in row:
                _finite(value, label="Local policy logit")
        if len(set(self.state_keys)) != len(self.state_keys):
            raise ValueError("Local policy state keys must be unique.")
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("Local policy actions must be unique.")
        if self.iteration == 0 and self.parent_checkpoint_hash is not None:
            raise ValueError("Initial local policy checkpoint must not have a parent.")
        if self.iteration > 0 and self.parent_checkpoint_hash is None:
            raise ValueError("Learned local policy checkpoint requires a parent hash.")
        payload = self.model_dump(mode="json", exclude={"checkpoint_hash"})
        if self.checkpoint_hash != canonical_sha256(payload):
            raise ValueError("Local policy checkpoint hash mismatch.")
        return self


class LocalRLIterationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    iteration: int = Field(gt=0)
    rollout_count: int = Field(gt=0)
    episode_steps: int = Field(gt=0)
    mean_reward: float
    success_rate: float = Field(ge=0.0, le=1.0)
    unsafe_action_rate: float = Field(ge=0.0, le=1.0)
    mean_entropy: float = Field(ge=0.0)
    gradient_norm: float = Field(ge=0.0)
    clipped_sample_fraction: float = Field(ge=0.0, le=1.0)
    parameter_delta_l2: float = Field(ge=0.0)
    episode_hashes_hash: str = Field(pattern=_SHA256_PATTERN)
    advantages_hash: str = Field(pattern=_SHA256_PATTERN)
    checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    metrics_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "mean_reward",
        "success_rate",
        "unsafe_action_rate",
        "mean_entropy",
        "gradient_norm",
        "clipped_sample_fraction",
        "parameter_delta_l2",
    )
    @classmethod
    def validate_finite(cls, value: float) -> float:
        return _finite(value, label="Local RL iteration metric")

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"metrics_hash"})
        if self.metrics_hash != canonical_sha256(payload):
            raise ValueError("Local RL iteration metric hash mismatch.")
        return self


class LocalRLTrainingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    initial_checkpoint: LocalPolicyCheckpoint
    retained_checkpoints: tuple[LocalPolicyCheckpoint, ...]
    iterations: tuple[LocalRLIterationMetrics, ...]
    usage: LocalRLTrainingUsage
    result_hash: str = Field(pattern=_SHA256_PATTERN)
    numeric_parameters_updated: Literal[True] = True
    local_rollout_training_executed_by_evoagent: Literal[True] = True
    foundation_model_training_performed: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self):
        if self.initial_checkpoint.iteration != 0:
            raise ValueError("Local RL initial checkpoint must be iteration zero.")
        if not self.retained_checkpoints or not self.iterations:
            raise ValueError("Local RL result requires learned checkpoints and metrics.")
        iteration_numbers = [item.iteration for item in self.iterations]
        if iteration_numbers != list(range(1, len(iteration_numbers) + 1)):
            raise ValueError("Local RL iteration metrics must be contiguous.")
        checkpoint_iterations = [item.iteration for item in self.retained_checkpoints]
        if checkpoint_iterations != sorted(checkpoint_iterations):
            raise ValueError("Local RL retained checkpoints must be ordered.")
        if self.initial_checkpoint.checkpoint_hash == self.retained_checkpoints[-1].checkpoint_hash:
            raise ValueError("Local RL claims an update but final parameters did not change.")
        if not self.usage.iterations == len(self.iterations):
            raise ValueError("Local RL usage iteration count is not derived.")
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        validate_safe_content(payload)
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Local RL training result hash mismatch.")
        return self


class LocalRLEvaluationTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(pattern=_SAFE_ID_PATTERN)
    task_hash: str = Field(pattern=_SHA256_PATTERN)
    kind: LocalRLTaskKind
    actions: tuple[LocalRLAction, ...]
    total_reward: float
    success: bool
    unsafe_action_count: int = Field(ge=0)
    episode_steps: int = Field(gt=0)
    result_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("total_reward")
    @classmethod
    def validate_reward(cls, value: float) -> float:
        return _finite(value, label="Local RL evaluation reward")

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Local RL evaluation Task result hash mismatch.")
        return self


class LocalRLEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    checkpoint_id: str = Field(pattern=_SAFE_ID_PATTERN)
    checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    evaluator_id: str = Field(pattern=_SAFE_ID_PATTERN)
    task_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    task_results: tuple[LocalRLEvaluationTaskResult, ...]
    overall_score: float = Field(ge=0.0, le=1.0)
    normal_score: float = Field(ge=0.0, le=1.0)
    protected_score: float = Field(ge=0.0, le=1.0)
    unsafe_action_count: int = Field(ge=0)
    episode_steps: int = Field(ge=0)
    report_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_report(self):
        if not self.task_results:
            raise ValueError("Local RL evaluation report requires Tasks.")
        ids = [item.task_id for item in self.task_results]
        if len(set(ids)) != len(ids):
            raise ValueError("Local RL evaluation Task IDs must be unique.")
        expected_overall = sum(item.success for item in self.task_results) / len(
            self.task_results
        )
        for kind, actual in (
            (LocalRLTaskKind.NORMAL, self.normal_score),
            (LocalRLTaskKind.PROTECTED, self.protected_score),
        ):
            subset = [item for item in self.task_results if item.kind == kind]
            if not subset:
                raise ValueError("Local RL evaluation report lacks one Task kind.")
            expected = sum(item.success for item in subset) / len(subset)
            if abs(actual - expected) > 1e-12:
                raise ValueError("Local RL kind score is not derived.")
        if abs(self.overall_score - expected_overall) > 1e-12:
            raise ValueError("Local RL overall score is not derived.")
        if self.unsafe_action_count != sum(
            item.unsafe_action_count for item in self.task_results
        ):
            raise ValueError("Local RL unsafe-action count is not derived.")
        if self.episode_steps != sum(item.episode_steps for item in self.task_results):
            raise ValueError("Local RL evaluation step count is not derived.")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Local RL evaluation report hash mismatch.")
        return self


class LocalRLCheckpointAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    checkpoint_id: str = Field(pattern=_SAFE_ID_PATTERN)
    checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    iteration: int = Field(ge=0)
    score: float = Field(ge=0.0, le=1.0)
    improvement: float
    regression_count: int = Field(ge=0)
    unsafe_action_count: int = Field(ge=0)
    eligible: bool
    reasons: tuple[str, ...]
    assessment_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("score", "improvement")
    @classmethod
    def validate_finite(cls, value: float) -> float:
        return _finite(value, label="Local RL checkpoint assessment")

    @model_validator(mode="after")
    def validate_assessment(self):
        if self.eligible and self.reasons:
            raise ValueError("Eligible Local RL checkpoint cannot have failure reasons.")
        if not self.eligible and not self.reasons:
            raise ValueError("Ineligible Local RL checkpoint requires reasons.")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("Local RL assessment reasons must be unique.")
        payload = self.model_dump(mode="json", exclude={"assessment_hash"})
        if self.assessment_hash != canonical_sha256(payload):
            raise ValueError("Local RL checkpoint assessment hash mismatch.")
        return self


class LocalRLSelectionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    baseline_report_hash: str = Field(pattern=_SHA256_PATTERN)
    assessments: tuple[LocalRLCheckpointAssessment, ...]
    selected_checkpoint_id: str = Field(pattern=_SAFE_ID_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_iteration: int = Field(gt=0)
    selected_report_hash: str = Field(pattern=_SHA256_PATTERN)
    decision_actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    decided_at: datetime
    decision_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Local RL selection time")

    @model_validator(mode="after")
    def validate_decision(self):
        if not self.assessments:
            raise ValueError("Local RL selection requires checkpoint assessments.")
        eligible = [item for item in self.assessments if item.eligible]
        if not eligible:
            raise ValueError("Local RL selection requires an eligible checkpoint.")
        matches = [
            item
            for item in eligible
            if item.checkpoint_id == self.selected_checkpoint_id
            and item.checkpoint_hash == self.selected_checkpoint_hash
            and item.iteration == self.selected_iteration
        ]
        if len(matches) != 1:
            raise ValueError("Local RL selected checkpoint is not exactly one eligible item.")
        expected = sorted(
            eligible,
            key=lambda item: (-item.score, item.iteration, item.checkpoint_hash),
        )[0]
        if matches[0] != expected:
            raise ValueError("Local RL selected checkpoint is not deterministically best.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Local RL selection decision hash mismatch.")
        return self


class LocalRLAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_type: LocalRLEventType
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    reason: str
    payload: dict[str, Any]
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)


class LocalRLRegistryCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


__all__ = [
    "LocalRLAction",
    "LocalRLAuditEvent",
    "LocalRLCheckpointAssessment",
    "LocalRLCheckpointStatus",
    "LocalRLEnvironmentContract",
    "LocalRLError",
    "LocalRLEvaluationReport",
    "LocalRLEvaluationTaskResult",
    "LocalRLEventType",
    "LocalRLHyperparameters",
    "LocalRLIterationMetrics",
    "LocalRLRegistryCheckpoint",
    "LocalRLRunManifest",
    "LocalRLSelectionDecision",
    "LocalRLTask",
    "LocalRLTaskKind",
    "LocalRLTrainingBudget",
    "LocalRLTrainingResult",
    "LocalRLTrainingUsage",
    "LocalPolicyCheckpoint",
]
