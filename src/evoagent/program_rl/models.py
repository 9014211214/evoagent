from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import ProgramCheckpoint


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class LocalRLExecutionBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_iterations: int = Field(ge=1, le=100_000)
    max_rollouts: int = Field(ge=1, le=100_000_000)
    max_tokens: int = Field(default=0, ge=0)
    max_cost_usd: float = Field(default=0.0, ge=0.0)

    @field_validator("max_cost_usd")
    @classmethod
    def finite_cost(cls, value: float) -> float:
        return _finite(value, "Local-RL execution budget cost")


class LocalRLExecutionUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    iterations: int = Field(ge=0)
    rollouts: int = Field(ge=0)
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)

    @field_validator("cost_usd")
    @classmethod
    def finite_cost(cls, value: float) -> float:
        return _finite(value, "Local-RL execution usage cost")


class ProgramLocalRLIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=1)
    program_head_revision: int = Field(ge=0)
    program_checkpoint: ProgramCheckpoint
    campaign_id: str = Field(pattern=_SAFE_ID_PATTERN)
    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    source_signal_id: str = Field(pattern=_SAFE_ID_PATTERN)
    source_signal_hash: str = Field(pattern=_SHA256_PATTERN)
    attribution_receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attribution_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    intervention_layer: FailureLayer
    intervention_action: EvolutionAction
    parent_agent_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    target_agent_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_release_package_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_release_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    optimizer_config_hash: str = Field(pattern=_SHA256_PATTERN)
    training_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    governed_actor_ids: tuple[str, ...]
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    optimizer_execution_authorized: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    intent_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Program local-RL intent time")

    @field_validator("governed_actor_ids")
    @classmethod
    def unique_actors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) < 5 or len(set(value)) != len(value):
            raise ValueError(
                "Program local-RL intent requires at least five unique governed actors."
            )
        return value

    @model_validator(mode="after")
    def validate_intent(self):
        if self.training_task_set_hash == self.heldout_task_set_hash:
            raise ValueError("Training and held-out task sets must be disjoint.")
        if self.parent_agent_identity_hash == self.target_agent_identity_hash:
            raise ValueError("Program local-RL target identity must differ from its parent.")
        if self.created_by in set(self.governed_actor_ids):
            raise ValueError(
                "Program local-RL intent author must be independent from governed Program roles."
            )
        payload = self.model_dump(mode="json", exclude={"intent_hash"})
        validate_safe_content(payload)
        if self.intent_hash != canonical_sha256(payload):
            raise ValueError("Program local-RL intent hash mismatch.")
        return self


class ProgramLocalRLAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)

    authorization_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_hash: str = Field(pattern=_SHA256_PATTERN)
    budget: LocalRLExecutionBudget
    authorized_by: str = Field(pattern=_SAFE_ID_PATTERN)
    authorized_at: datetime
    expires_at: datetime | None = None
    optimizer_execution_authorized: Literal[True] = True
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    authorization_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("authorized_at", "expires_at")
    @classmethod
    def timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _timezone(value, "Program local-RL authorization time")

    @model_validator(mode="after")
    def validate_authorization(self):
        if self.expires_at is not None and self.expires_at <= self.authorized_at:
            raise ValueError("Program local-RL authorization expiry is not in the future.")
        payload = self.model_dump(mode="json", exclude={"authorization_hash"})
        validate_safe_content(payload)
        if self.authorization_hash != canonical_sha256(payload):
            raise ValueError("Program local-RL authorization hash mismatch.")
        return self


class ProgramLocalRLResultBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    result_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_hash: str = Field(pattern=_SHA256_PATTERN)
    authorization_id: str = Field(pattern=_SAFE_ID_PATTERN)
    authorization_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    initial_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    usage: LocalRLExecutionUsage
    heldout_reward_delta: float
    heldout_success_delta: float
    unsafe_action_count: int = Field(ge=0)
    regression_count: int = Field(ge=0)
    executed_by: str = Field(pattern=_SAFE_ID_PATTERN)
    started_at: datetime
    completed_at: datetime
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    result_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("heldout_reward_delta", "heldout_success_delta")
    @classmethod
    def finite_delta(cls, value: float) -> float:
        return _finite(value, "Program local-RL held-out delta")

    @field_validator("started_at", "completed_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Program local-RL result time")

    @model_validator(mode="after")
    def validate_result(self):
        if self.completed_at < self.started_at:
            raise ValueError("Program local-RL completion precedes execution start.")
        if self.selected_checkpoint_hash == self.initial_checkpoint_hash:
            raise ValueError("Program local-RL result did not change the policy checkpoint.")
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        validate_safe_content(payload)
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Program local-RL result hash mismatch.")
        return self


class ProgramLocalRLBindingPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    intent: ProgramLocalRLIntent
    authorization: ProgramLocalRLAuthorization
    result: ProgramLocalRLResultBinding
    created_at: datetime
    external_model_call_performed_by_evoagent: Literal[False] = False
    foundation_model_weights_updated: Literal[False] = False
    checkpoint_promotion_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Program local-RL binding package time")

    @model_validator(mode="after")
    def validate_package(self):
        if (
            self.authorization.intent_id != self.intent.intent_id
            or self.authorization.intent_hash != self.intent.intent_hash
            or self.result.intent_id != self.intent.intent_id
            or self.result.intent_hash != self.intent.intent_hash
            or self.result.authorization_id != self.authorization.authorization_id
            or self.result.authorization_hash != self.authorization.authorization_hash
        ):
            raise ValueError("Program local-RL package lineage differs across records.")
        if self.created_at < self.result.completed_at:
            raise ValueError("Program local-RL package predates its result.")
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != program_payload_hash(payload):
            raise ValueError("Program local-RL binding package hash mismatch.")
        return self


__all__ = [
    "LocalRLExecutionBudget",
    "LocalRLExecutionUsage",
    "ProgramLocalRLAuthorization",
    "ProgramLocalRLBindingPackage",
    "ProgramLocalRLIntent",
    "ProgramLocalRLResultBinding",
]
