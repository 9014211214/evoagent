from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evoagent.domain.models import AgentSnapshot


class ReferenceLabPhase(str, Enum):
    ACQUIRED = "acquired"
    BASELINE_EVALUATED = "baseline_evaluated"
    CANDIDATE_CREATED = "candidate_created"
    CANDIDATE_EVALUATED = "candidate_evaluated"
    CAMPAIGN_AUTHORIZED = "campaign_authorized"
    SKILL_PROMOTED = "skill_promoted"
    CAMPAIGN_COMPLETED = "campaign_completed"
    EVIDENCE_BUNDLED = "evidence_bundled"
    RESTART_VERIFIED = "restart_verified"


class ReferenceCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    passed: bool
    expected: str
    observed: str


class ReferenceEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_id: str
    version: str
    cases: tuple[ReferenceCaseResult, ...]
    score: float = Field(ge=0.0, le=1.0)


class ReferenceLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    phases: tuple[ReferenceLabPhase, ...]
    skill_id: str
    base_version: str
    active_version: str
    candidate_version: str
    campaign_id: str
    campaign_state: str
    baseline: ReferenceEvaluationResult
    evolved: ReferenceEvaluationResult
    snapshots: tuple[AgentSnapshot, ...]
    evolution_gain: float
    best_round: int
    skill_checkpoint: dict[str, Any]
    campaign_checkpoint: dict[str, Any]
    trace_checkpoint: dict[str, Any]
    run_bundle_path: str
    run_manifest_hash: str
    restart_verified: bool
    external_execution_performed: bool = False


__all__ = [
    "ReferenceCaseResult",
    "ReferenceEvaluationResult",
    "ReferenceLabPhase",
    "ReferenceLabResult",
]
