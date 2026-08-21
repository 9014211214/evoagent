from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.domain.models import FailureLayer


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class TrainingMethod(str, Enum):
    SFT = "sft"
    DPO = "dpo"
    AGENTIC_RL = "agentic_rl"


class RLAlgorithm(str, Enum):
    GRPO = "grpo"
    PPO = "ppo"
    REINFORCE_PLUS_PLUS = "reinforce_plus_plus"


class DatasetSignals(BaseModel):
    model_config = ConfigDict(frozen=True)

    gold_trajectories: int = Field(default=0, ge=0)
    preference_pairs: int = Field(default=0, ge=0)
    replayable_environment: bool = False
    resettable_environment: bool = False
    machine_verifier: bool = False


class TrainingBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_gpu_hours: float = Field(default=0.0, ge=0.0)
    max_rollouts: int = Field(default=0, ge=0)
    max_training_tokens: int = Field(default=0, ge=0)
    max_cost_usd: float = Field(default=0.0, ge=0.0)


class MetricTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    minimum_value: float | None = None
    minimum_improvement: float | None = None
    maximum_regression: float | None = None


class ModelImprovementTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticket_id: str
    base_model_id: str
    problem_cluster: str
    evidence_trace_ids: tuple[str, ...]
    ruled_out_layers: tuple[FailureLayer, ...]
    target_metrics: tuple[MetricTarget, ...]
    dataset_signals: DatasetSignals
    allowed_methods: tuple[TrainingMethod, ...]
    budget: TrainingBudget
    replay_environment: str | None = None
    safety_constraints: tuple[str, ...] = ()
    regression_suite: str = "default"
    evidence_dataset_uri: str | None = None
    evidence_manifest_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    held_out_task_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_binding(self):
        if (self.evidence_dataset_uri is None) != (self.evidence_manifest_hash is None):
            raise ValueError(
                "Model ticket evidence dataset URI and manifest hash must be supplied together."
            )
        if len(set(self.held_out_task_ids)) != len(self.held_out_task_ids):
            raise ValueError("Held-out model evaluation Task IDs must be unique.")
        return self


class TrainingPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: TrainingMethod
    rationale: str
    dataset_plan: tuple[str, ...]
    evaluation_plan: tuple[str, ...]
    budget: TrainingBudget


class MLInternTaskSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    command: tuple[str, ...]
    prompt: str
    workspace: str
    runtime_config: dict[str, object]
    required_environment_variables: tuple[str, ...]
    execution_enabled: bool = False


class RewardComponent(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    weight: float
    kind: Literal["reward", "penalty"]
    machine_computable: bool = True


class RewardSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    components: tuple[RewardComponent, ...]
    aggregation: Literal["weighted_sum"] = "weighted_sum"


class AgenticRLEnvironmentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_id: str
    replayable: bool
    resettable: bool
    machine_verifier: bool
    isolated: bool
    side_effect_free: bool
    max_episode_steps: int = Field(gt=0)
    dataset_ref: str | None = None


class AgenticRLTaskSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: RLAlgorithm
    environment: AgenticRLEnvironmentSpec
    reward: RewardSpec
    workspace: str
    runtime_config: dict[str, object]
    rollout_budget: int = Field(gt=0)
    evidence_dataset_uri: str | None = None
    evidence_manifest_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    held_out_task_ids: tuple[str, ...] = ()
    execution_enabled: bool = False


class ModelCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    base_model_id: str
    method: TrainingMethod
    artifact_uri: str
    status: Literal["candidate"] = "candidate"
    plan: TrainingPlan
    task_spec: MLInternTaskSpec | AgenticRLTaskSpec | None = None
    generated_by: str
    evidence_manifest_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    held_out_task_ids: tuple[str, ...] = ()
    training_executed: Literal[False] = False
