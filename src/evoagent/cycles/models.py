from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.diagnosis.counterfactual import AttributionReport
from evoagent.domain.models import EvolutionDecision, EvolutionTicket, ExecutionTrace
from evoagent.skills.models import SkillPatch, SkillSpec
from evoagent.traces.models import TraceTrustLevel
from evoagent.training.models import (
    DatasetSignals,
    MetricTarget,
    ModelCandidate,
    ModelImprovementTicket,
    TrainingBudget,
    TrainingMethod,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class CycleStatus(str, Enum):
    NO_ACTION = "no_action"
    QUARANTINED = "quarantined"
    ESCALATED = "escalated"
    TICKET_CREATED = "ticket_created"
    SKILL_CANDIDATE = "skill_candidate"
    MODEL_EVIDENCE_ACCUMULATED = "model_evidence_accumulated"
    MODEL_CANDIDATE = "model_candidate"


class EvolutionCyclePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    quarantine_untrusted: bool = True
    blocking_safety_flags: tuple[str, ...] = (
        "prompt_injection",
        "secret_leak",
        "policy_violation",
        "training_data_poisoning",
    )
    model_min_traces: int = Field(default=3, gt=0)
    model_min_distinct_tasks: int = Field(default=3, gt=0)


class ModelEvolutionSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    problem_cluster: str
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
                "Model settings evidence dataset URI and manifest hash must be supplied together."
            )
        if len(set(self.held_out_task_ids)) != len(self.held_out_task_ids):
            raise ValueError("Held-out Task IDs must be unique.")
        return self


class EvolutionCycleRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace: ExecutionTrace
    source: str
    trust_level: TraceTrustLevel
    safety_flags: tuple[str, ...] = ()
    model_settings: ModelEvolutionSettings | None = None


class ModelEvidenceCluster(BaseModel):
    model_config = ConfigDict(frozen=True)

    cluster_id: str
    base_model_id: str
    problem_cluster: str
    trace_ids: tuple[str, ...]
    task_ids: tuple[str, ...]
    ready: bool


class EvolutionCycleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CycleStatus
    trace_id: str
    trace_record_hash: str
    reason: str
    attribution: AttributionReport | None = None
    decision: EvolutionDecision | None = None
    evolution_ticket: EvolutionTicket | None = None
    skill_candidate: SkillSpec | None = None
    skill_patch: SkillPatch | None = None
    model_evidence: ModelEvidenceCluster | None = None
    model_ticket: ModelImprovementTicket | None = None
    model_candidate: ModelCandidate | None = None
