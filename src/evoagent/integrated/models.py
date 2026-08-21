from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.diagnosis import AttributionReport, ExperimentType
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class IntegratedTrack(str, Enum):
    SKILL = "skill"
    LOCAL_POLICY = "local_policy"
    ESCALATION = "escalation"
    QUARANTINE = "quarantine"


class IntegratedCaseStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class IntegratedRunStatus(str, Enum):
    OPEN = "open"
    RUNNING = "running"
    STOPPED = "stopped"
    ESCALATED = "escalated"
    FAILED = "failed"


class IntegratedEventType(str, Enum):
    RUN_CREATED = "run_created"
    CASE_ADMITTED = "case_admitted"
    CASES_CLAIMED = "cases_claimed"
    TRACK_RESULT_RECORDED = "track_result_recorded"
    RUN_COMPLETED = "run_completed"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _sorted_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not values:
        raise ValueError(f"{label} must not be empty.")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates.")
    return tuple(sorted(values))


def _validate_hashes(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    normalized = _sorted_unique(values, label)
    if any(
        len(item) != 64
        or any(character not in "0123456789abcdef" for character in item)
        for item in normalized
    ):
        raise ValueError(f"{label} must contain lowercase SHA-256 values.")
    return normalized


class IntegratedRunPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    max_cases: int = Field(default=8, gt=0)
    max_rounds: int = Field(default=3, gt=0)
    min_policy_cases: int = Field(default=2, gt=0)
    max_skill_executions: int = Field(default=1, ge=0)
    max_policy_executions: int = Field(default=1, ge=0)
    minimum_attribution_confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
    )
    require_unique_supporting_counterfactual: Literal[True] = True
    policy_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_policy(self):
        if self.max_rounds < (
            self.max_skill_executions + self.max_policy_executions
        ):
            raise ValueError(
                "Integrated max rounds cannot be lower than component execution budgets."
            )
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        validate_safe_content(payload)
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Integrated run policy hash mismatch.")
        return self


class IntegratedCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(pattern=_SAFE_ID_PATTERN)
    trace_id: str = Field(pattern=_SAFE_ID_PATTERN)
    task_id: str = Field(pattern=_SAFE_ID_PATTERN)
    root_cause_layer: FailureLayer
    recommended_action: EvolutionAction
    attribution_confidence: float = Field(ge=0.0, le=1.0)
    attribution_hash: str = Field(pattern=_SHA256_PATTERN)
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    track: IntegratedTrack
    source: str = Field(pattern=_SAFE_ID_PATTERN)
    trust_level: Literal["verified", "untrusted", "quarantined"]
    safety_flags: tuple[str, ...] = ()
    supporting_experiment_ids: tuple[str, ...] = ()
    created_at: datetime
    case_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Integrated case time")

    @field_validator("safety_flags", "supporting_experiment_ids")
    @classmethod
    def validate_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError(
                "Integrated case evidence sets must not contain duplicates."
            )
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_case(self):
        if self.track == IntegratedTrack.SKILL and not (
            self.root_cause_layer == FailureLayer.SKILL
            and self.recommended_action == EvolutionAction.UPDATE_SKILL
            and len(self.supporting_experiment_ids) == 1
            and self.trust_level == "verified"
            and not self.safety_flags
        ):
            raise ValueError(
                "Automatic Skill case differs from governed attribution evidence."
            )
        if self.track == IntegratedTrack.LOCAL_POLICY and not (
            self.root_cause_layer == FailureLayer.MODEL
            and self.recommended_action == EvolutionAction.TRAIN_MODEL
            and len(self.supporting_experiment_ids) == 1
            and self.trust_level == "verified"
            and not self.safety_flags
        ):
            raise ValueError(
                "Automatic local-policy case differs from governed attribution evidence."
            )
        if self.track == IntegratedTrack.QUARANTINE and not (
            self.trust_level in {"untrusted", "quarantined"}
            or bool(self.safety_flags)
        ):
            raise ValueError(
                "Quarantined integrated case lacks trust or safety evidence."
            )
        payload = self.model_dump(mode="json", exclude={"case_hash"})
        validate_safe_content(payload)
        if self.case_hash != canonical_sha256(payload):
            raise ValueError("Integrated case hash mismatch.")
        return self


class IntegratedTrackResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    result_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    track: IntegratedTrack
    case_ids: tuple[str, ...]
    source_decision_hashes: tuple[str, ...]
    source_package_hashes: tuple[str, ...]
    component_ref: str = Field(pattern=_SAFE_ID_PATTERN)
    component_hash: str = Field(pattern=_SHA256_PATTERN)
    executor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    started_at: datetime
    completed_at: datetime
    metrics: dict[str, float] = Field(default_factory=dict)
    skill_promoted: bool = False
    local_policy_optimized: bool = False
    local_policy_promoted: bool = False
    local_policy_activated: bool = False
    rollback_ready: bool = False
    foundation_model_weights_updated: Literal[False] = False
    production_activation_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_execution_performed: Literal[False] = False
    result_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("case_ids")
    @classmethod
    def validate_cases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, "Integrated result cases")

    @field_validator("source_decision_hashes", "source_package_hashes")
    @classmethod
    def validate_hash_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_hashes(value, "Integrated result source hashes")

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_times(cls, value: datetime) -> datetime:
        return _timezone(value, "Integrated track result time")

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        if any(
            number != number
            or number in {float("inf"), float("-inf")}
            or number < 0.0
            for number in value.values()
        ):
            raise ValueError(
                "Integrated result metrics must be finite and non-negative."
            )
        return value

    @model_validator(mode="after")
    def validate_result(self):
        if self.completed_at < self.started_at:
            raise ValueError(
                "Integrated track result completes before it starts."
            )
        if self.track == IntegratedTrack.SKILL:
            if not self.skill_promoted or any(
                (
                    self.local_policy_optimized,
                    self.local_policy_promoted,
                    self.local_policy_activated,
                    self.rollback_ready,
                )
            ):
                raise ValueError(
                    "Skill result differs from isolated Skill evolution authority."
                )
        elif self.track == IntegratedTrack.LOCAL_POLICY:
            if not all(
                (
                    self.local_policy_optimized,
                    self.local_policy_promoted,
                    self.local_policy_activated,
                    self.rollback_ready,
                )
            ) or self.skill_promoted:
                raise ValueError(
                    "Local-policy result lacks optimizer, Promotion, activation or rollback evidence."
                )
        else:
            raise ValueError(
                "Automatic track result must belong to Skill or local policy."
            )
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        validate_safe_content(payload)
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Integrated track result hash mismatch.")
        return self


class IntegratedRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy: IntegratedRunPolicy
    status: IntegratedRunStatus
    revision: int = Field(ge=0)
    round_index: int = Field(ge=0)
    skill_execution_count: int = Field(ge=0)
    policy_execution_count: int = Field(ge=0)
    terminal_decision_hash: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @field_validator("created_at", "updated_at", "completed_at")
    @classmethod
    def validate_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _timezone(value, "Integrated run time")

    @model_validator(mode="after")
    def validate_run(self):
        terminal = self.status in {
            IntegratedRunStatus.STOPPED,
            IntegratedRunStatus.ESCALATED,
            IntegratedRunStatus.FAILED,
        }
        has_completed_at = self.completed_at is not None
        has_terminal_hash = self.terminal_decision_hash is not None
        if terminal and not (has_completed_at and has_terminal_hash):
            raise ValueError(
                "Integrated terminal run lacks complete decision evidence."
            )
        if not terminal and (has_completed_at or has_terminal_hash):
            raise ValueError(
                "Integrated non-terminal run contains terminal decision evidence."
            )
        if self.round_index != (
            self.skill_execution_count + self.policy_execution_count
        ):
            raise ValueError(
                "Integrated round index differs from component executions."
            )
        if (
            self.round_index > self.policy.max_rounds
            or self.skill_execution_count
            > self.policy.max_skill_executions
            or self.policy_execution_count
            > self.policy.max_policy_executions
        ):
            raise ValueError(
                "Integrated run exceeds its frozen execution budget."
            )
        if self.updated_at < self.created_at or (
            self.completed_at is not None
            and self.completed_at < self.updated_at
        ):
            raise ValueError("Integrated run chronology is invalid.")
        return self


class IntegratedCaseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    case: IntegratedCase
    status: IntegratedCaseStatus
    claimed_by: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    result_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_times(cls, value: datetime) -> datetime:
        return _timezone(value, "Integrated case record time")

    @model_validator(mode="after")
    def validate_record(self):
        if self.status == IntegratedCaseStatus.PENDING and (
            self.claimed_by is not None or self.result_id is not None
        ):
            raise ValueError(
                "Pending integrated case contains claim or result evidence."
            )
        if self.status == IntegratedCaseStatus.CLAIMED and (
            self.claimed_by is None or self.result_id is not None
        ):
            raise ValueError(
                "Claimed integrated case lacks exact executor evidence."
            )
        if self.status == IntegratedCaseStatus.COMPLETED and (
            self.claimed_by is None or self.result_id is None
        ):
            raise ValueError(
                "Completed integrated case lacks result evidence."
            )
        if self.updated_at < self.created_at:
            raise ValueError(
                "Integrated case record chronology is invalid."
            )
        return self


class IntegratedAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_type: IntegratedEventType
    case_ids: tuple[str, ...] = ()
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    reason: str
    metadata: dict[str, Any]
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Integrated audit time")

    @field_validator("case_ids")
    @classmethod
    def validate_cases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError(
                "Integrated audit case IDs must be unique."
            )
        return tuple(sorted(value))


class IntegratedCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


def route_integrated_attribution(
    report: AttributionReport,
    *,
    trust_level: str,
    safety_flags: tuple[str, ...],
    minimum_confidence: float,
) -> tuple[IntegratedTrack, tuple[str, ...]]:
    supporting = tuple(
        sorted(
            result.experiment_id
            for result in report.experiments
            if (
                result.supports_hypothesis
                and result.counterfactual_success
                and not result.baseline_success
            )
        )
    )
    if trust_level in {"untrusted", "quarantined"} or safety_flags:
        return IntegratedTrack.QUARANTINE, supporting
    if (
        not report.actionable
        or report.confidence < minimum_confidence
        or len(supporting) != 1
    ):
        return IntegratedTrack.ESCALATION, supporting
    supporting_result = next(
        item
        for item in report.experiments
        if item.experiment_id == supporting[0]
    )
    if (
        report.root_cause_layer == FailureLayer.SKILL
        and report.recommended_action == EvolutionAction.UPDATE_SKILL
        and supporting_result.experiment_type == ExperimentType.REPLACE_SKILL
    ):
        return IntegratedTrack.SKILL, supporting
    if (
        report.root_cause_layer == FailureLayer.MODEL
        and report.recommended_action == EvolutionAction.TRAIN_MODEL
        and supporting_result.experiment_type == ExperimentType.REFERENCE_MODEL
    ):
        return IntegratedTrack.LOCAL_POLICY, supporting
    return IntegratedTrack.ESCALATION, supporting


def build_integrated_run_policy(
    *,
    policy_id: str = "integrated-multitrack-policy:v2.3",
    max_cases: int = 8,
    max_rounds: int = 3,
    min_policy_cases: int = 2,
    max_skill_executions: int = 1,
    max_policy_executions: int = 1,
    minimum_attribution_confidence: float = 0.8,
) -> IntegratedRunPolicy:
    payload = {
        "policy_id": policy_id,
        "max_cases": max_cases,
        "max_rounds": max_rounds,
        "min_policy_cases": min_policy_cases,
        "max_skill_executions": max_skill_executions,
        "max_policy_executions": max_policy_executions,
        "minimum_attribution_confidence": minimum_attribution_confidence,
        "require_unique_supporting_counterfactual": True,
    }
    return IntegratedRunPolicy(
        **payload,
        policy_hash=canonical_sha256(payload),
    )


def build_integrated_case(
    report: AttributionReport,
    *,
    policy: IntegratedRunPolicy,
    case_id: str,
    trace_id: str,
    task_id: str,
    evidence_hash: str,
    source: str,
    trust_level: Literal["verified", "untrusted", "quarantined"],
    safety_flags: tuple[str, ...],
    created_at: datetime,
) -> IntegratedCase:
    normalized_flags = tuple(sorted(safety_flags))
    track, supporting = route_integrated_attribution(
        report,
        trust_level=trust_level,
        safety_flags=normalized_flags,
        minimum_confidence=policy.minimum_attribution_confidence,
    )
    attribution_hash = canonical_sha256(report.model_dump(mode="json"))
    payload = {
        "case_id": case_id,
        "trace_id": trace_id,
        "task_id": task_id,
        "root_cause_layer": report.root_cause_layer,
        "recommended_action": report.recommended_action,
        "attribution_confidence": report.confidence,
        "attribution_hash": attribution_hash,
        "evidence_hash": evidence_hash,
        "track": track,
        "source": source,
        "trust_level": trust_level,
        "safety_flags": normalized_flags,
        "supporting_experiment_ids": supporting,
        "created_at": created_at,
    }
    return IntegratedCase(
        **payload,
        case_hash=canonical_sha256(payload),
    )


def build_integrated_track_result(
    *,
    result_id: str,
    run_id: str,
    track: IntegratedTrack,
    case_ids: tuple[str, ...],
    source_decision_hashes: tuple[str, ...],
    source_package_hashes: tuple[str, ...],
    component_ref: str,
    component_hash: str,
    executor_id: str,
    started_at: datetime,
    completed_at: datetime,
    metrics: dict[str, float],
    skill_promoted: bool = False,
    local_policy_optimized: bool = False,
    local_policy_promoted: bool = False,
    local_policy_activated: bool = False,
    rollback_ready: bool = False,
) -> IntegratedTrackResult:
    payload = {
        "result_id": result_id,
        "run_id": run_id,
        "track": track,
        "case_ids": tuple(sorted(case_ids)),
        "source_decision_hashes": tuple(sorted(source_decision_hashes)),
        "source_package_hashes": tuple(sorted(source_package_hashes)),
        "component_ref": component_ref,
        "component_hash": component_hash,
        "executor_id": executor_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "metrics": metrics,
        "skill_promoted": skill_promoted,
        "local_policy_optimized": local_policy_optimized,
        "local_policy_promoted": local_policy_promoted,
        "local_policy_activated": local_policy_activated,
        "rollback_ready": rollback_ready,
        "foundation_model_weights_updated": False,
        "production_activation_performed": False,
        "production_deployment_performed": False,
        "external_execution_performed": False,
    }
    return IntegratedTrackResult(
        **payload,
        result_hash=canonical_sha256(payload),
    )


__all__ = [
    "IntegratedAuditEvent",
    "IntegratedCase",
    "IntegratedCaseRecord",
    "IntegratedCaseStatus",
    "IntegratedCheckpoint",
    "IntegratedEventType",
    "IntegratedRunPolicy",
    "IntegratedRunRecord",
    "IntegratedRunStatus",
    "IntegratedTrack",
    "IntegratedTrackResult",
    "build_integrated_case",
    "build_integrated_run_policy",
    "build_integrated_track_result",
    "route_integrated_attribution",
]
