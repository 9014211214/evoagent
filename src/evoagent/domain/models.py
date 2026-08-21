from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field

class FailureLayer(str, Enum):
    NONE="none"; SKILL="skill"; ROUTER="router"; TOOL="tool"; CONTEXT="context"
    VERIFIER="verifier"; MODEL="model"; ENVIRONMENT="environment"; SAFETY="safety"; UNKNOWN="unknown"

class EvolutionAction(str, Enum):
    NO_ACTION="no_action"; CREATE_SKILL="create_skill"; UPDATE_SKILL="update_skill"
    UPDATE_ROUTER="update_router"; REPAIR_TOOL="repair_tool"; UPDATE_CONTEXT="update_context"
    REPAIR_VERIFIER="repair_verifier"; TRAIN_MODEL="train_model"; QUARANTINE="quarantine"
    ESCALATE="escalate"

class Task(BaseModel):
    task_id: str
    task_type: str
    input: dict[str, Any]
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

class Skill(BaseModel):
    skill_id: str
    name: str
    version: str
    description: str
    rules: list[str] = Field(default_factory=list)
    provenance: str = "independent"
    status: Literal["candidate","stable","rejected"] = "stable"

class ExecutionTrace(BaseModel):
    trace_id: str
    task: Task
    model_id: str
    skill_id: str | None = None
    skill_version: str | None = None
    observable_events: list[dict[str, Any]] = Field(default_factory=list)
    final_output: dict[str, Any] = Field(default_factory=dict)
    verifier_passed: bool
    verifier_feedback: str = ""
    cost: dict[str, float] = Field(default_factory=dict)

class FailureReport(BaseModel):
    trace_id: str
    layer: FailureLayer
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    recommended_action: EvolutionAction
    summary: str

class EvolutionDecision(BaseModel):
    action: EvolutionAction
    target_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    rationale: str
    estimated_cost: float = 0
    estimated_risk: Literal["low","medium","high"] = "low"

class EvolutionTicket(BaseModel):
    ticket_id: str
    target_layer: FailureLayer
    target_id: str | None
    evidence_trace_ids: list[str]
    proposed_action: EvolutionAction
    expected_benefit: str
    required_evaluations: list[str] = Field(default_factory=lambda:["held_out","regression"])

class CandidateArtifact(BaseModel):
    artifact_id: str
    artifact_type: Literal["skill","model","harness","tool","context","verifier"]
    base_version: str
    candidate_version: str
    payload: dict[str, Any]
    generated_by: str
    status: Literal["candidate","promoted","rejected"] = "candidate"

class EvaluationResult(BaseModel):
    snapshot_id: str
    total: int
    passed: int
    score: float
    failed_task_ids: list[str] = Field(default_factory=list)
    per_task: dict[str,bool] = Field(default_factory=dict)

class AgentSnapshot(BaseModel):
    snapshot_id: str
    round_index: int
    model_id: str
    skills: dict[str,Skill] = Field(default_factory=dict)
    harness_version: str = "0.1"
    parent_snapshot_id: str | None = None
    metadata: dict[str,Any] = Field(default_factory=dict)
