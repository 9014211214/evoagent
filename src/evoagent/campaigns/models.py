from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CampaignType(str, Enum):
    SKILL = "skill"
    MODEL = "model"
    MODEL_ACTIVATION = "model_activation"
    CHAMPION_PROMOTION = "champion_promotion"
    CHAMPION_RELEASE = "champion_release"
    CHAMPION_ROLLBACK = "champion_rollback"
    EVOLUTION_GENERATION = "evolution_generation"
    LOCAL_POLICY_PROMOTION = "local_policy_promotion"
    LOCAL_POLICY_ROLLBACK = "local_policy_rollback"
    ROUTER = "router"
    TOOL = "tool"
    CONTEXT = "context"
    VERIFIER = "verifier"


class CampaignState(str, Enum):
    OPEN = "open"
    EVIDENCE_ACCUMULATING = "evidence_accumulating"
    CANDIDATE_READY = "candidate_ready"
    EVALUATION_PENDING = "evaluation_pending"
    APPROVAL_PENDING = "approval_pending"
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class CampaignRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class CampaignRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    campaign_id: str
    campaign_type: CampaignType
    target_key: str
    fingerprint: str
    state: CampaignState
    risk: CampaignRisk
    generated_by: str
    required_approvals: int = Field(ge=1)
    candidate_ref: str | None = None
    artifact_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    cooldown_until: datetime | None = None
    revision: int = Field(ge=0)


class CampaignReservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    campaign: CampaignRecord
    reused: bool


class CampaignApproval(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    campaign_id: str
    actor_id: str
    decision: ApprovalDecision
    reason: str
    created_at: datetime

    @property
    def approver_id(self) -> str:
        """Semantic read-only alias; persisted and serialized field stays actor_id."""

        return self.actor_id


class CampaignAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str
    campaign_id: str | None = None
    event_type: str
    actor_id: str
    payload: dict[str, Any]
    created_at: datetime
    previous_hash: str
    event_hash: str


class CampaignCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str


class ModelEvidenceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_model_id: str
    problem_cluster: str
    trace_ids: tuple[str, ...]
    task_ids: tuple[str, ...]
    ready: bool
