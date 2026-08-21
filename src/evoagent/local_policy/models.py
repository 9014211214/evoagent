from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class LocalPolicyError(ValueError):
    pass


class LocalPolicyVersionStatus(str, Enum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    EVALUATED = "evaluated"
    AUTHORIZED = "authorized"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class LocalPolicyEventType(str, Enum):
    REGISTERED = "registered"
    CANDIDATE_ADMITTED = "candidate_admitted"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    AUTHORIZED = "authorized"
    ACTIVATED = "activated"
    ROLLBACK_SUBMITTED = "rollback_submitted"
    ROLLBACK_AUTHORIZED = "rollback_authorized"
    ROLLED_BACK = "rolled_back"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class InitialLocalPolicyManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["initial"] = "initial"
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_config_hash: str = Field(pattern=_SHA256_PATTERN)
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    tiny_local_agent_policy: Literal[True] = True
    foundation_model_checkpoint: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Initial local-policy manifest time")

    @model_validator(mode="after")
    def validate_manifest(self):
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        validate_safe_content(payload)
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("Initial local-policy manifest hash mismatch.")
        return self


class LocalPolicyCandidateManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["candidate"] = "candidate"
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    fully_attested_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    fully_attested_package_hash: str = Field(pattern=_SHA256_PATTERN)
    anchors_id: str = Field(pattern=_SAFE_ID_PATTERN)
    anchors_hash: str = Field(pattern=_SHA256_PATTERN)
    acceptance_receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    acceptance_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    native_local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_config_hash: str = Field(pattern=_SHA256_PATTERN)
    training_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    governed_actor_ids: tuple[str, ...]
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    evidence_accepted: Literal[True] = True
    tiny_local_agent_policy: Literal[True] = True
    foundation_model_checkpoint: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Local-policy candidate creation time")

    @field_validator("governed_actor_ids")
    @classmethod
    def validate_actors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) < 8 or len(set(value)) != len(value):
            raise ValueError(
                "Local-policy candidate requires at least eight unique governed actors."
            )
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_manifest(self):
        if self.base_policy_id == self.candidate_id:
            raise ValueError("Local-policy candidate must differ from its base policy.")
        if self.base_checkpoint_hash == self.selected_checkpoint_hash:
            raise ValueError("Local-policy candidate contains no checkpoint change.")
        if self.training_task_set_hash == self.heldout_task_set_hash:
            raise ValueError("Local-policy training and held-out Task sets overlap.")
        if self.created_by in set(self.governed_actor_ids):
            raise ValueError(
                "Local-policy candidate creator overlaps accepted evidence roles."
            )
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        validate_safe_content(payload)
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("Local-policy candidate manifest hash mismatch.")
        return self


LocalPolicyManifest = Annotated[
    InitialLocalPolicyManifest | LocalPolicyCandidateManifest,
    Field(discriminator="kind"),
]
LOCAL_POLICY_MANIFEST_ADAPTER = TypeAdapter(LocalPolicyManifest)


class LocalPolicyPromotionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    fully_attested_package_hash: str = Field(pattern=_SHA256_PATTERN)
    acceptance_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_reward_delta: float
    heldout_success_delta: float
    unsafe_action_count: int = Field(ge=0)
    regression_count: int = Field(ge=0)
    evaluator_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evaluated_at: datetime
    passed: bool
    report_hash: str = Field(pattern=_SHA256_PATTERN)
    new_external_rollout_performed: Literal[False] = False
    production_traffic_observed: Literal[False] = False
    production_activation_authorized: Literal[False] = False

    @field_validator("heldout_reward_delta", "heldout_success_delta")
    @classmethod
    def validate_finite(cls, value: float) -> float:
        return _finite(value, "Local-policy promotion delta")

    @field_validator("evaluated_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Local-policy promotion assessment time")

    @model_validator(mode="after")
    def validate_report(self):
        expected = (
            self.heldout_reward_delta > 0.0
            and self.heldout_success_delta > 0.0
            and self.unsafe_action_count == 0
            and self.regression_count == 0
        )
        if self.passed != expected:
            raise ValueError(
                "Local-policy promotion pass flag differs from accepted evidence."
            )
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        validate_safe_content(payload)
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Local-policy promotion report hash mismatch.")
        return self


class LocalPolicyPromotionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    report_id: str = Field(pattern=_SAFE_ID_PATTERN)
    report_hash: str = Field(pattern=_SHA256_PATTERN)
    report_passed: bool
    promote: bool
    reason: str
    decided_by: str = Field(pattern=_SAFE_ID_PATTERN)
    decided_at: datetime
    decision_hash: str = Field(pattern=_SHA256_PATTERN)
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False

    @field_validator("decided_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Local-policy promotion decision time")

    @model_validator(mode="after")
    def validate_decision(self):
        if self.promote != self.report_passed:
            raise ValueError(
                "Local-policy promotion decision differs from the verified report gate."
            )
        if not self.reason.strip():
            raise ValueError("Local-policy promotion decision requires a reason.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        validate_safe_content(payload)
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Local-policy promotion decision hash mismatch.")
        return self


class LocalPolicyRollbackRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    from_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    to_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    promotion_campaign_id: str = Field(pattern=_SAFE_ID_PATTERN)
    promotion_decision_hash: str = Field(pattern=_SHA256_PATTERN)
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    reason: str
    requested_by: str = Field(pattern=_SAFE_ID_PATTERN)
    requested_at: datetime
    request_hash: str = Field(pattern=_SHA256_PATTERN)
    rollback_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("requested_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Local-policy rollback request time")

    @model_validator(mode="after")
    def validate_request(self):
        if self.from_policy_id == self.to_policy_id:
            raise ValueError("Local-policy rollback source and target must differ.")
        if not self.reason.strip():
            raise ValueError("Local-policy rollback request requires a reason.")
        payload = self.model_dump(mode="json", exclude={"request_hash"})
        validate_safe_content(payload)
        if self.request_hash != canonical_sha256(payload):
            raise ValueError("Local-policy rollback request hash mismatch.")
        return self


class LocalPolicyRollbackReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(pattern=_SAFE_ID_PATTERN)
    request_id: str = Field(pattern=_SAFE_ID_PATTERN)
    request_hash: str = Field(pattern=_SHA256_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    from_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    to_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evaluator_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evaluated_at: datetime
    source_is_active: Literal[True] = True
    target_is_direct_parent: Literal[True] = True
    target_is_superseded: Literal[True] = True
    safe_to_rollback: Literal[True] = True
    report_hash: str = Field(pattern=_SHA256_PATTERN)
    production_traffic_observed: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("evaluated_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Local-policy rollback assessment time")

    @model_validator(mode="after")
    def validate_report(self):
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        validate_safe_content(payload)
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Local-policy rollback report hash mismatch.")
        return self


class LocalPolicyVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    manifest: LocalPolicyManifest
    parent_policy_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    status: LocalPolicyVersionStatus
    promotion_report: LocalPolicyPromotionReport | None = None
    promotion_decision: LocalPolicyPromotionDecision | None = None
    promotion_campaign_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    promotion_authorized_by: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    activated_by: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    activated_at: datetime | None = None
    rollback_request: LocalPolicyRollbackRequest | None = None
    rollback_report: LocalPolicyRollbackReport | None = None
    rollback_campaign_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    rollback_authorized_by: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    rolled_back_by: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    rolled_back_at: datetime | None = None
    created_at: datetime

    @field_validator("created_at", "activated_at", "rolled_back_at")
    @classmethod
    def validate_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _timezone(value, "Local-policy record time")

    @model_validator(mode="after")
    def validate_record(self):
        manifest_policy_id = (
            self.manifest.policy_id
            if isinstance(self.manifest, InitialLocalPolicyManifest)
            else self.manifest.candidate_id
        )
        if self.family_id != self.manifest.family_id or self.policy_id != manifest_policy_id:
            raise ValueError("Local-policy record identity differs from its manifest.")
        if isinstance(self.manifest, InitialLocalPolicyManifest):
            if self.parent_policy_id is not None:
                raise ValueError("Initial local policy must not have a parent.")
        elif self.parent_policy_id != self.manifest.base_policy_id:
            raise ValueError("Candidate parent differs from its manifest base policy.")
        promotion_values = (
            self.promotion_report,
            self.promotion_decision,
            self.promotion_campaign_id,
        )
        if any(item is not None for item in promotion_values) and not all(
            item is not None for item in promotion_values
        ):
            raise ValueError("Local-policy promotion evidence is incomplete.")
        rollback_values = (
            self.rollback_request,
            self.rollback_report,
            self.rollback_campaign_id,
        )
        if any(item is not None for item in rollback_values) and not all(
            item is not None for item in rollback_values
        ):
            raise ValueError("Local-policy rollback evidence is incomplete.")
        validate_safe_content(self.model_dump(mode="json"))
        return self


class LocalPolicyHead(BaseModel):
    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    active_policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    revision: int = Field(ge=0)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Local-policy head time")


class LocalPolicyAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_type: LocalPolicyEventType
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    from_policy_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    to_policy_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    reason: str
    metadata: dict[str, Any]
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Local-policy audit time")


class LocalPolicyRegistryCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


__all__ = [
    "InitialLocalPolicyManifest",
    "LOCAL_POLICY_MANIFEST_ADAPTER",
    "LocalPolicyAuditEvent",
    "LocalPolicyCandidateManifest",
    "LocalPolicyError",
    "LocalPolicyEventType",
    "LocalPolicyHead",
    "LocalPolicyManifest",
    "LocalPolicyPromotionDecision",
    "LocalPolicyPromotionReport",
    "LocalPolicyRegistryCheckpoint",
    "LocalPolicyRollbackReport",
    "LocalPolicyRollbackRequest",
    "LocalPolicyVersionRecord",
    "LocalPolicyVersionStatus",
]
