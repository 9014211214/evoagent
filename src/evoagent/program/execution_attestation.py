from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


class ProgramExecutionCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


class RunningGenerationRoles(BaseModel):
    model_config = ConfigDict(frozen=True)

    release_evidence_producer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    feedback_ingestor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    causal_attributor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    decision_planner_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_evaluator_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_approver_ids: tuple[str, str]
    authorization_actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    start_actor_id: str = Field(pattern=_SAFE_ID_PATTERN)

    @field_validator("generation_approver_ids")
    @classmethod
    def distinct_approvers(cls, value: tuple[str, str]) -> tuple[str, str]:
        if len(set(value)) != 2:
            raise ValueError("Running Generation attestation requires two approvers.")
        return value

    @model_validator(mode="after")
    def validate_roles(self):
        review_origins = {
            self.release_evidence_producer_id,
            self.feedback_ingestor_id,
            self.causal_attributor_id,
            self.decision_planner_id,
            self.generation_evaluator_id,
            *self.generation_approver_ids,
        }
        if len(review_origins) != 7:
            raise ValueError(
                "Running Generation evidence, attribution, planning, evaluation and approval roles overlap."
            )
        if (
            self.authorization_actor_id in review_origins
            or self.start_actor_id in review_origins
        ):
            raise ValueError(
                "Running Generation authorization/start actors overlap governed review roles."
            )
        validate_safe_content(self.model_dump(mode="json"))
        return self

    def all_actor_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    self.release_evidence_producer_id,
                    self.feedback_ingestor_id,
                    self.causal_attributor_id,
                    self.decision_planner_id,
                    self.generation_evaluator_id,
                    *self.generation_approver_ids,
                    self.authorization_actor_id,
                    self.start_actor_id,
                }
            )
        )


class RunningGenerationAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    attestation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=1)
    program_state: Literal["generation_running"] = "generation_running"
    program_head_revision: int = Field(ge=0)
    program_checkpoint: ProgramExecutionCheckpoint
    campaign_id: str = Field(pattern=_SAFE_ID_PATTERN)
    campaign_state: Literal["authorized"] = "authorized"
    campaign_revision: int = Field(ge=0)
    campaign_checkpoint: ProgramExecutionCheckpoint
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
    roles: RunningGenerationRoles
    attested_by: str = Field(pattern=_SAFE_ID_PATTERN)
    attested_at: datetime
    optimizer_execution_authorized: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    attestation_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("attested_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Running Generation attestation time")

    @model_validator(mode="after")
    def validate_attestation(self):
        if self.parent_agent_identity_hash == self.target_agent_identity_hash:
            raise ValueError("Running Generation target identity equals its parent.")
        if self.attested_by in set(self.roles.all_actor_ids()):
            raise ValueError(
                "Running Generation attestor overlaps a governed lifecycle role."
            )
        payload = self.model_dump(mode="json", exclude={"attestation_hash"})
        validate_safe_content(payload)
        if self.attestation_hash != canonical_sha256(payload):
            raise ValueError("Running Generation attestation hash mismatch.")
        return self


def build_running_generation_attestation(**payload) -> RunningGenerationAttestation:
    return RunningGenerationAttestation(
        **payload,
        attestation_hash=program_payload_hash(payload),
    )


__all__ = [
    "ProgramExecutionCheckpoint",
    "RunningGenerationAttestation",
    "RunningGenerationRoles",
    "build_running_generation_attestation",
]
