from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.domain.models import EvolutionAction, FailureLayer


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SupervisorValidationError(ValueError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        encoded = value.isoformat()
        return encoded[:-6] + "Z" if encoded.endswith("+00:00") else encoded
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SupervisorTrack(str, Enum):
    NONE = "none"
    SKILL = "skill"
    MODEL = "model"
    EXTERNAL_REPAIR = "external_repair"
    ESCALATION = "escalation"
    QUARANTINE = "quarantine"


class SupervisorCaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class SupervisorRunStatus(str, Enum):
    OPEN = "open"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ESCALATIONS = "completed_with_escalations"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class SupervisorEventType(str, Enum):
    RUN_CREATED = "run_created"
    RUN_STATUS_CHANGED = "run_status_changed"
    CASE_ADMITTED = "case_admitted"
    CASE_REUSED = "case_reused"
    CASE_ROUTED = "case_routed"
    CASE_CLAIMED = "case_claimed"
    CASE_COMPLETED = "case_completed"
    CASE_BLOCKED = "case_blocked"
    CASE_ESCALATED = "case_escalated"
    CASE_QUARANTINED = "case_quarantined"
    CASE_FAILED = "case_failed"


class SupervisorBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_cases: int = Field(default=16, gt=0)
    max_rounds: int = Field(default=16, gt=0)
    max_skill_executions: int = Field(default=4, ge=0)
    max_model_executions: int = Field(default=2, ge=0)
    max_external_repair_tickets: int = Field(default=4, ge=0)


class SupervisorPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = "closed-loop-supervisor-v1"
    budget: SupervisorBudget = Field(default_factory=SupervisorBudget)
    automatic_skill: bool = True
    automatic_model: bool = True
    automatic_external_repair: bool = False
    stop_on_quarantine: bool = True
    require_idempotent_executors: bool = True


class SupervisorCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    trace_id: str
    task_id: str
    failure_layer: FailureLayer
    action: EvolutionAction
    attribution_hash: str = Field(pattern=_SHA256_PATTERN)
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    source: str
    trust_level: Literal["verified", "untrusted", "quarantined"]
    safety_flags: tuple[str, ...] = ()
    created_at: datetime
    case_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Supervisor case time must include a timezone.")
        return value

    @field_validator("safety_flags")
    @classmethod
    def unique_safety_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Supervisor case safety flags must be unique.")
        return value

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"case_hash"})
        if self.case_hash != canonical_sha256(payload):
            raise ValueError("Supervisor case hash mismatch.")
        return self


class SupervisorOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    track: SupervisorTrack
    status: SupervisorCaseStatus
    reason: str
    executor_id: str | None = None
    child_run_id: str | None = None
    artifact_refs: tuple[str, ...] = ()
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    completed_at: datetime
    skill_promoted: bool = False
    model_candidate_evaluated: bool = False
    model_candidate_activated: bool = False
    model_rollback_verified: bool = False
    training_executed_by_evoagent: Literal[False] = False
    external_execution_performed: Literal[False] = False
    outcome_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("completed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Supervisor outcome time must include a timezone.")
        return value

    @field_validator("artifact_hashes")
    @classmethod
    def validate_artifact_hashes(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        for key, digest in value.items():
            if (
                not key
                or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
            ):
                raise ValueError(
                    "Supervisor artifact hashes must be named lowercase "
                    "SHA-256 values."
                )
        return value

    @field_validator("metrics")
    @classmethod
    def finite_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        if any(
            number != number
            or number in {float("inf"), float("-inf")}
            for number in value.values()
        ):
            raise ValueError("Supervisor metrics must be finite numbers.")
        return value

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.status in {
            SupervisorCaseStatus.PENDING,
            SupervisorCaseStatus.RUNNING,
        }:
            raise ValueError(
                "A persisted Supervisor outcome must be terminal."
            )
        if (
            self.track in {SupervisorTrack.SKILL, SupervisorTrack.MODEL}
            and self.status == SupervisorCaseStatus.COMPLETED
            and not self.executor_id
        ):
            raise ValueError(
                "Completed automatic tracks require an executor ID."
            )
        if (
            self.track == SupervisorTrack.SKILL
            and self.status == SupervisorCaseStatus.COMPLETED
            and not self.skill_promoted
        ):
            raise ValueError(
                "Completed Skill track must attest governed promotion."
            )
        if (
            self.track == SupervisorTrack.MODEL
            and self.status == SupervisorCaseStatus.COMPLETED
            and not (
                self.model_candidate_evaluated
                and self.model_candidate_activated
                and self.model_rollback_verified
            )
        ):
            raise ValueError(
                "Completed Model track must attest evaluation, activation, "
                "and rollback verification."
            )
        if (
            self.track == SupervisorTrack.ESCALATION
            and self.status != SupervisorCaseStatus.ESCALATED
        ):
            raise ValueError("Escalation track must end in ESCALATED.")
        if (
            self.track == SupervisorTrack.QUARANTINE
            and self.status != SupervisorCaseStatus.QUARANTINED
        ):
            raise ValueError("Quarantine track must end in QUARANTINED.")
        payload = self.model_dump(mode="json", exclude={"outcome_hash"})
        if self.outcome_hash != canonical_sha256(payload):
            raise ValueError("Supervisor outcome hash mismatch.")
        return self


class SupervisorCaseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    case: SupervisorCase
    track: SupervisorTrack
    status: SupervisorCaseStatus
    outcome: SupervisorOutcome | None = None
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_terminal_binding(self):
        terminal = self.status not in {
            SupervisorCaseStatus.PENDING,
            SupervisorCaseStatus.RUNNING,
        }
        if terminal != (self.outcome is not None):
            raise ValueError(
                "Terminal Supervisor case state and outcome presence differ."
            )
        if self.outcome is not None:
            if (
                self.outcome.case_id != self.case.case_id
                or self.outcome.track != self.track
            ):
                raise ValueError(
                    "Supervisor outcome does not match its case record."
                )
            if self.outcome.status != self.status:
                raise ValueError(
                    "Supervisor outcome status differs from its case record."
                )
        return self


class SupervisorRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    policy: SupervisorPolicy
    status: SupervisorRunStatus
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_completion(self):
        terminal = self.status not in {
            SupervisorRunStatus.OPEN,
            SupervisorRunStatus.RUNNING,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError(
                "Terminal Supervisor run state and completion time differ."
            )
        return self


class SupervisorAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str
    run_id: str
    case_id: str | None = None
    event_type: SupervisorEventType
    actor_id: str
    from_status: str | None = None
    to_status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)


class SupervisorCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


class SupervisorScoreSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_initial_score: float = Field(ge=0.0, le=1.0)
    skill_final_score: float = Field(ge=0.0, le=1.0)
    model_initial_score: float = Field(ge=0.0, le=1.0)
    model_final_score: float = Field(ge=0.0, le=1.0)
    composite_initial_score: float = Field(ge=0.0, le=1.0)
    composite_final_score: float = Field(ge=0.0, le=1.0)
    composite_gain: float
    escalation_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_derived_values(self):
        expected_initial = (
            self.skill_initial_score + self.model_initial_score
        ) / 2.0
        expected_final = (
            self.skill_final_score + self.model_final_score
        ) / 2.0
        if abs(self.composite_initial_score - expected_initial) > 1e-12:
            raise ValueError(
                "Composite initial score is not derived from the two tracks."
            )
        if abs(self.composite_final_score - expected_final) > 1e-12:
            raise ValueError(
                "Composite final score is not derived from the two tracks."
            )
        if abs(
            self.composite_gain - (expected_final - expected_initial)
        ) > 1e-12:
            raise ValueError(
                "Composite gain is not derived from the two track scores."
            )
        return self


__all__ = [
    "SupervisorAuditEvent",
    "SupervisorBudget",
    "SupervisorCase",
    "SupervisorCaseRecord",
    "SupervisorCaseStatus",
    "SupervisorCheckpoint",
    "SupervisorEventType",
    "SupervisorOutcome",
    "SupervisorPolicy",
    "SupervisorRunRecord",
    "SupervisorRunStatus",
    "SupervisorScoreSummary",
    "SupervisorTrack",
    "SupervisorValidationError",
    "canonical_sha256",
]
