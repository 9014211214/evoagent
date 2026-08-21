from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.release.models import ReleaseDecisionAction, ReleaseState


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class EvolutionProgramError(ValueError):
    pass


def _require_timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _require_finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class ProgramState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    GENERATION_AUTHORIZED = "generation_authorized"
    GENERATION_RUNNING = "generation_running"
    COMPLETED = "completed"
    PAUSED = "paused"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ESCALATED = "escalated"
    FAILED = "failed"


class ProgramAction(str, Enum):
    CONTINUE = "continue"
    STOP_SUCCESS = "stop_success"
    STOP_BUDGET = "stop_budget"
    PAUSE = "pause"
    ESCALATE = "escalate"
    FAIL = "fail"


class GenerationStatus(str, Enum):
    OBSERVED = "observed"
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    HELD = "held"
    ESCALATED = "escalated"


class ProgramEventType(str, Enum):
    PROGRAM_REGISTERED = "program_registered"
    GENERATION_OBSERVED = "generation_observed"
    SIGNAL_STORED = "signal_stored"
    ATTRIBUTION_STORED = "attribution_stored"
    GENERATION_PLANNED = "generation_planned"
    GENERATION_CAMPAIGN_BOUND = "generation_campaign_bound"
    GENERATION_AUTHORIZED = "generation_authorized"
    GENERATION_STARTED = "generation_started"
    GENERATION_COMPLETED = "generation_completed"
    DECISION_STORED = "decision_stored"
    PROGRAM_COMPLETED = "program_completed"
    PROGRAM_PAUSED = "program_paused"
    PROGRAM_BUDGET_EXHAUSTED = "program_budget_exhausted"
    PROGRAM_ESCALATED = "program_escalated"
    PROGRAM_FAILED = "program_failed"


class ProgramBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_generations: int = Field(default=3, ge=1, le=64)
    max_rollbacks: int = Field(default=2, ge=0, le=64)
    max_holds: int = Field(default=1, ge=0, le=64)
    max_generation_campaigns: int = Field(default=2, ge=0, le=64)
    max_total_pairs: int = Field(default=10_000, ge=0)
    max_total_tokens: int = Field(default=10_000_000, ge=0)
    max_total_cost_usd: float = Field(default=0.0, ge=0.0)

    @field_validator("max_total_cost_usd")
    @classmethod
    def finite_cost(cls, value: float) -> float:
        return _require_finite(value, label="Program budget cost")


class EvolutionProgramPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    budget: ProgramBudget
    minimum_attribution_confidence: float = Field(default=0.90, ge=0.0, le=1.0)
    allowed_automatic_layers: tuple[FailureLayer, ...] = (
        FailureLayer.SKILL,
        FailureLayer.ROUTER,
        FailureLayer.TOOL,
        FailureLayer.CONTEXT,
        FailureLayer.VERIFIER,
    )
    require_single_supported_experiment: bool = True
    require_independent_attributor: bool = True
    require_generation_approvals: bool = True
    stop_on_ready: bool = True
    safety_feedback_requires_attribution: bool = True
    maximum_consecutive_non_improving: int = Field(default=2, ge=0, le=64)
    policy_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("minimum_attribution_confidence")
    @classmethod
    def finite_confidence(cls, value: float) -> float:
        return _require_finite(value, label="Program attribution confidence")

    @field_validator("allowed_automatic_layers")
    @classmethod
    def unique_layers(
        cls, value: tuple[FailureLayer, ...]
    ) -> tuple[FailureLayer, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("Program automatic layers must be non-empty and unique.")
        if FailureLayer.MODEL in value or FailureLayer.ENVIRONMENT in value:
            raise ValueError(
                "Model and Environment feedback require separately governed execution or escalation."
            )
        return value

    @model_validator(mode="after")
    def validate_policy(self):
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        validate_safe_content(payload)
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Evolution Program policy hash mismatch.")
        return self


class ProgramLearningSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    signal_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=0)
    source_release_package_hash: str = Field(pattern=_SHA256_PATTERN)
    source_release_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    source_batch_hash: str = Field(pattern=_SHA256_PATTERN)
    source_assessment_hash: str = Field(pattern=_SHA256_PATTERN)
    source_decision_hash: str = Field(pattern=_SHA256_PATTERN)
    source_stage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    incumbent_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    challenger_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    runtime_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    tool_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    terminal_action: ReleaseDecisionAction
    terminal_state: ReleaseState
    reasons: tuple[str, ...]
    affected_segments: tuple[str, ...]
    protected_segments: tuple[str, ...]
    safety_violation_count: int = Field(ge=0)
    evidence_producer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    trust_level: Literal["verified"] = "verified"
    causal_attribution_claimed: Literal[False] = False
    signal_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Program learning signal time")

    @field_validator("reasons", "affected_segments", "protected_segments")
    @classmethod
    def unique_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Program learning signal tuple values must be unique.")
        return value

    @model_validator(mode="after")
    def validate_signal(self):
        if self.terminal_action not in {
            ReleaseDecisionAction.ROLLBACK,
            ReleaseDecisionAction.HOLD,
        }:
            raise ValueError("Only rollback or hold evidence creates a learning signal.")
        expected_state = {
            ReleaseDecisionAction.ROLLBACK: ReleaseState.ROLLED_BACK,
            ReleaseDecisionAction.HOLD: ReleaseState.HOLD,
        }[self.terminal_action]
        if self.terminal_state != expected_state:
            raise ValueError("Learning signal action and final release state differ.")
        if not self.reasons:
            raise ValueError("Learning signal requires observable release reasons.")
        if not set(self.protected_segments).issubset(set(self.affected_segments)):
            raise ValueError("Protected segments must be included in affected segments.")
        payload = self.model_dump(mode="json", exclude={"signal_hash"})
        validate_safe_content(payload)
        if self.signal_hash != canonical_sha256(payload):
            raise ValueError("Program learning signal hash mismatch.")
        return self


_ACTION_BY_LAYER = {
    FailureLayer.SKILL: EvolutionAction.UPDATE_SKILL,
    FailureLayer.ROUTER: EvolutionAction.UPDATE_ROUTER,
    FailureLayer.TOOL: EvolutionAction.REPAIR_TOOL,
    FailureLayer.CONTEXT: EvolutionAction.UPDATE_CONTEXT,
    FailureLayer.VERIFIER: EvolutionAction.REPAIR_VERIFIER,
    FailureLayer.ENVIRONMENT: EvolutionAction.ESCALATE,
    FailureLayer.MODEL: EvolutionAction.TRAIN_MODEL,
}


class AttributionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    signal_id: str = Field(pattern=_SAFE_ID_PATTERN)
    signal_hash: str = Field(pattern=_SHA256_PATTERN)
    failure_layer: FailureLayer
    action: EvolutionAction
    confidence: float = Field(ge=0.0, le=1.0)
    supported_experiment_hashes: tuple[str, ...]
    attributor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    independent: Literal[True] = True
    receipt_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("confidence")
    @classmethod
    def finite_confidence(cls, value: float) -> float:
        return _require_finite(value, label="Attribution confidence")

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Attribution receipt time")

    @field_validator("supported_experiment_hashes")
    @classmethod
    def valid_experiment_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("Attribution requires unique supported experiments.")
        if any(
            len(item) != 64 or any(ch not in "0123456789abcdef" for ch in item)
            for item in value
        ):
            raise ValueError("Supported experiments must be lowercase SHA-256 values.")
        return value

    @model_validator(mode="after")
    def validate_receipt(self):
        if self.action != _ACTION_BY_LAYER[self.failure_layer]:
            raise ValueError("Attribution action does not match its failure layer.")
        payload = self.model_dump(mode="json", exclude={"receipt_hash"})
        validate_safe_content(payload)
        if self.receipt_hash != canonical_sha256(payload):
            raise ValueError("Attribution receipt hash mismatch.")
        return self


class GenerationBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_child_packages: int = Field(default=1, ge=1, le=8)
    max_pairs: int = Field(default=10_000, ge=0)
    max_tokens: int = Field(default=10_000_000, ge=0)
    max_cost_usd: float = Field(default=0.0, ge=0.0)

    @field_validator("max_cost_usd")
    @classmethod
    def finite_cost(cls, value: float) -> float:
        return _require_finite(value, label="Generation budget cost")


class GenerationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=1)
    parent_generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    source_signal_id: str = Field(pattern=_SAFE_ID_PATTERN)
    source_signal_hash: str = Field(pattern=_SHA256_PATTERN)
    attribution_receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attribution_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    intervention_layer: FailureLayer
    intervention_action: EvolutionAction
    parent_agent_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    target_agent_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    target_runtime_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    target_tool_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_release_package_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_release_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    budget: GenerationBudget
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    external_execution_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Generation plan time")

    @model_validator(mode="after")
    def validate_plan(self):
        if self.intervention_action != _ACTION_BY_LAYER[self.intervention_layer]:
            raise ValueError("Generation intervention action differs from its layer.")
        if self.parent_agent_identity_hash == self.target_agent_identity_hash:
            raise ValueError("Generation target identity must differ from its parent.")
        payload = self.model_dump(mode="json", exclude={"plan_hash"})
        validate_safe_content(payload)
        if self.plan_hash != canonical_sha256(payload):
            raise ValueError("Generation plan hash mismatch.")
        return self


class GenerationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=0)
    plan_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    plan_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    release_package_hash: str = Field(pattern=_SHA256_PATTERN)
    release_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    champion_package_hash: str = Field(pattern=_SHA256_PATTERN)
    agent_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    runtime_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    tool_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    release_action: ReleaseDecisionAction
    release_state: ReleaseState
    pair_count: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0.0)
    quality_delta: float
    safety_violation_count: int = Field(ge=0)
    affected_segments: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    completed_at: datetime
    external_model_call_performed_by_evoagent: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    outcome_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("total_cost_usd", "quality_delta")
    @classmethod
    def finite_numbers(cls, value: float) -> float:
        return _require_finite(value, label="Generation outcome numeric value")

    @field_validator("completed_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Generation outcome time")

    @field_validator("affected_segments", "reasons")
    @classmethod
    def unique_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Generation outcome tuple values must be unique.")
        return value

    @model_validator(mode="after")
    def validate_outcome(self):
        expected_state = {
            ReleaseDecisionAction.READY: ReleaseState.READY,
            ReleaseDecisionAction.ROLLBACK: ReleaseState.ROLLED_BACK,
            ReleaseDecisionAction.HOLD: ReleaseState.HOLD,
        }
        if self.release_action not in expected_state:
            raise ValueError("Generation outcome must be ready, rollback, or hold.")
        if self.release_state != expected_state[self.release_action]:
            raise ValueError("Generation release action and state differ.")
        if self.generation_index == 0:
            if self.plan_id is not None or self.plan_hash is not None:
                raise ValueError("Observed Generation 0 must not contain a Program plan.")
        elif self.plan_id is None or self.plan_hash is None:
            raise ValueError("Executed generations require an authorized Program plan.")
        payload = self.model_dump(mode="json", exclude={"outcome_hash"})
        validate_safe_content(payload)
        if self.outcome_hash != canonical_sha256(payload):
            raise ValueError("Generation outcome hash mismatch.")
        return self


class ProgramDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=0)
    source_outcome_hash: str = Field(pattern=_SHA256_PATTERN)
    action: ProgramAction
    reason: str
    next_generation_index: int | None = Field(default=None, ge=1)
    decided_by: str = Field(pattern=_SAFE_ID_PATTERN)
    decided_at: datetime
    decision_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("decided_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Program decision time")

    @model_validator(mode="after")
    def validate_decision(self):
        if self.action == ProgramAction.CONTINUE:
            if self.next_generation_index != self.generation_index + 1:
                raise ValueError("Continue decision must identify the next generation.")
        elif self.next_generation_index is not None:
            raise ValueError("Terminal Program decision must not name a next generation.")
        if not self.reason.strip():
            raise ValueError("Program decision reason is required.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        validate_safe_content(payload)
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Program decision hash mismatch.")
        return self


class ProgramRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy: EvolutionProgramPolicy
    state: ProgramState
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    updated_at: datetime


class GenerationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=0)
    parent_generation_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    status: GenerationStatus
    plan: GenerationPlan | None = None
    outcome: GenerationOutcome | None = None
    campaign_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_record(self):
        if self.generation_index == 0:
            if self.parent_generation_id is not None or self.plan is not None:
                raise ValueError("Generation 0 must be an observed root generation.")
        else:
            if self.parent_generation_id is None or self.plan is None:
                raise ValueError("Successor generations require parent and plan.")
            if self.plan.generation_id != self.generation_id:
                raise ValueError("Generation record plan ID differs from its generation.")
        terminal = self.status in {
            GenerationStatus.COMPLETED,
            GenerationStatus.ROLLED_BACK,
            GenerationStatus.HELD,
            GenerationStatus.ESCALATED,
        }
        if terminal != (self.outcome is not None):
            raise ValueError("Generation terminal state and outcome presence differ.")
        return self


class ProgramHead(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    state: ProgramState
    current_generation_index: int = Field(ge=0)
    active_generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    revision: int = Field(ge=0)
    rollback_count: int = Field(ge=0)
    hold_count: int = Field(ge=0)
    generation_campaign_count: int = Field(ge=0)
    total_pairs: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0.0)
    last_decision_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    updated_at: datetime

    @field_validator("total_cost_usd")
    @classmethod
    def finite_cost(cls, value: float) -> float:
        return _require_finite(value, label="Program head cost")

    @field_validator("updated_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Program head time")


class ProgramAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    event_type: ProgramEventType
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    reason: str
    payload: dict[str, Any]
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)


class ProgramCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


__all__ = [
    "AttributionReceipt",
    "EvolutionProgramError",
    "EvolutionProgramPolicy",
    "GenerationBudget",
    "GenerationOutcome",
    "GenerationPlan",
    "GenerationRecord",
    "GenerationStatus",
    "ProgramAction",
    "ProgramAuditEvent",
    "ProgramBudget",
    "ProgramCheckpoint",
    "ProgramDecision",
    "ProgramEventType",
    "ProgramHead",
    "ProgramLearningSignal",
    "ProgramRecord",
    "ProgramState",
]
