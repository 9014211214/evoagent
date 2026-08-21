from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class ReleaseEvidenceError(ValueError):
    pass


def _require_timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _require_finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class ReleaseStageKind(str, Enum):
    SHADOW = "shadow"
    CANARY = "canary"


class ReleaseEvidenceSource(str, Enum):
    EXTERNAL_OBSERVATION = "external_observation"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class ReleaseAssessmentStatus(str, Enum):
    PASS = "pass"
    HOLD = "hold"
    ROLLBACK = "rollback"


class ReleaseDecisionAction(str, Enum):
    ADVANCE = "advance"
    HOLD = "hold"
    ROLLBACK = "rollback"
    READY = "ready"


class ReleaseState(str, Enum):
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    SHADOW = "shadow"
    CANARY = "canary"
    HOLD = "hold"
    ROLLBACK_RECOMMENDED = "rollback_recommended"
    READY = "ready"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class ReleaseEventType(str, Enum):
    PLAN_REGISTERED = "plan_registered"
    RELEASE_CAMPAIGN_BOUND = "release_campaign_bound"
    RELEASE_AUTHORIZED = "release_authorized"
    STAGE_ACTIVATED = "stage_activated"
    EVIDENCE_IMPORTED = "evidence_imported"
    STAGE_ASSESSED = "stage_assessed"
    DECISION_STORED = "decision_stored"
    STAGE_ADVANCED = "stage_advanced"
    HOLD_RECORDED = "hold_recorded"
    ROLLBACK_RECOMMENDED = "rollback_recommended"
    ROLLBACK_CAMPAIGN_BOUND = "rollback_campaign_bound"
    READY_RECORDED = "ready_recorded"
    ROLLED_BACK = "rolled_back"


class ReleaseSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(pattern=_SAFE_ID_PATTERN)
    protected: bool = False
    segment_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_segment(self):
        payload = self.model_dump(mode="json", exclude={"segment_hash"})
        if self.segment_hash != canonical_sha256(payload):
            raise ValueError("Release segment hash mismatch.")
        return self


class ReleaseStageSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    stage_index: int = Field(ge=0)
    kind: ReleaseStageKind
    candidate_traffic_percent: float = Field(ge=0.0, le=100.0)
    minimum_pairs: int = Field(gt=0)
    minimum_pairs_per_segment: int = Field(gt=0)
    observation_window_seconds: int = Field(gt=0)
    stage_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("candidate_traffic_percent")
    @classmethod
    def validate_percent(cls, value: float) -> float:
        return _require_finite(value, label="Release candidate traffic percent")

    @model_validator(mode="after")
    def validate_stage(self):
        if self.kind == ReleaseStageKind.SHADOW and self.candidate_traffic_percent != 0.0:
            raise ValueError("Shadow stage candidate traffic must be zero percent.")
        if self.kind == ReleaseStageKind.CANARY and not (
            0.0 < self.candidate_traffic_percent < 100.0
        ):
            raise ValueError("Canary traffic must be greater than zero and below 100 percent.")
        payload = self.model_dump(mode="json", exclude={"stage_hash"})
        if self.stage_hash != canonical_sha256(payload):
            raise ValueError("Release stage hash mismatch.")
        return self


class ReleasePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    minimum_success_rate_delta: float = -0.02
    bootstrap_confidence: float = Field(default=0.80, gt=0.50, lt=1.0)
    bootstrap_resamples: int = Field(default=4096, ge=256, le=100_000)
    bootstrap_seed: int = Field(default=29, ge=0)
    minimum_delta_lower_bound: float = -0.25
    maximum_error_rate_delta: float = Field(default=0.02, ge=0.0)
    maximum_safety_violations: int = Field(default=0, ge=0)
    maximum_p95_latency_growth_ratio: float = Field(default=0.50, ge=0.0)
    maximum_input_token_growth_ratio: float = Field(default=0.50, ge=0.0)
    maximum_output_token_growth_ratio: float = Field(default=0.50, ge=0.0)
    maximum_cost_growth_ratio: float = Field(default=0.50, ge=0.0)
    maximum_regressed_segments: int = Field(default=0, ge=0)
    maximum_regressed_segment_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    protected_segment_zero_regression: bool = True
    require_token_evidence: bool = True
    require_cost_evidence: bool = True
    hold_on_insufficient_evidence: bool = True
    policy_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "minimum_success_rate_delta",
        "minimum_delta_lower_bound",
        "maximum_error_rate_delta",
        "maximum_p95_latency_growth_ratio",
        "maximum_input_token_growth_ratio",
        "maximum_output_token_growth_ratio",
        "maximum_cost_growth_ratio",
        "maximum_regressed_segment_fraction",
    )
    @classmethod
    def validate_finite_numbers(cls, value: float) -> float:
        return _require_finite(value, label="Release policy numeric value")

    @model_validator(mode="after")
    def validate_policy(self):
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        validate_safe_content(payload)
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Release policy hash mismatch.")
        return self


class ReleasePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    champion_package_hash: str = Field(pattern=_SHA256_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    incumbent_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    challenger_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    champion_decision_hash: str = Field(pattern=_SHA256_PATTERN)
    runtime_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    tool_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    segments: tuple[ReleaseSegment, ...]
    stages: tuple[ReleaseStageSpec, ...]
    policy: ReleasePolicy
    evidence_source: ReleaseEvidenceSource
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    production_deployment_authorized: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Release plan creation time")

    @model_validator(mode="after")
    def validate_plan(self):
        if self.incumbent_snapshot_id == self.challenger_snapshot_id:
            raise ValueError("Release plan incumbent and Challenger must differ.")
        if not self.segments or len({item.segment_id for item in self.segments}) != len(
            self.segments
        ):
            raise ValueError("Release plan requires unique segments.")
        if len(self.stages) < 2:
            raise ValueError("Release plan requires shadow and at least one Canary stage.")
        indexes = [item.stage_index for item in self.stages]
        if indexes != list(range(len(self.stages))):
            raise ValueError("Release plan stage indexes must be consecutive from zero.")
        if len({item.stage_id for item in self.stages}) != len(self.stages):
            raise ValueError("Release plan stage IDs must be unique.")
        if self.stages[0].kind != ReleaseStageKind.SHADOW:
            raise ValueError("Release plan must begin with a shadow stage.")
        if any(item.kind != ReleaseStageKind.CANARY for item in self.stages[1:]):
            raise ValueError("All stages after shadow must be Canary stages.")
        allocations = [item.candidate_traffic_percent for item in self.stages]
        if allocations != sorted(allocations) or len(set(allocations)) != len(allocations):
            raise ValueError("Release plan candidate allocations must increase strictly.")
        payload = self.model_dump(mode="json", exclude={"plan_hash"})
        validate_safe_content(payload)
        if self.plan_hash != canonical_sha256(payload):
            raise ValueError("Release plan hash mismatch.")
        return self


class SafeReleaseObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    pair_id: str = Field(pattern=_SAFE_ID_PATTERN)
    stage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    segment_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    success: bool
    error: bool
    safety_violation: bool
    latency_ms: float = Field(gt=0.0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    observed_at: datetime
    event_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("latency_ms", "cost_usd")
    @classmethod
    def validate_optional_finite(cls, value: float | None) -> float | None:
        if value is not None:
            return _require_finite(value, label="Release observation numeric value")
        return value

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Release observation time")

    @model_validator(mode="after")
    def validate_observation(self):
        if self.error and self.success:
            raise ValueError("Errored release observation cannot be successful.")
        if self.safety_violation and self.success:
            raise ValueError("Safety-violating observation cannot count as successful.")
        payload = self.model_dump(mode="json", exclude={"event_hash"})
        validate_safe_content(payload)
        if self.event_hash != canonical_sha256(payload):
            raise ValueError("Release observation hash mismatch.")
        return self


class ReleaseEvidenceBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: str = Field(pattern=_SAFE_ID_PATTERN)
    source_file_name: Literal["release-evidence.json"] = "release-evidence.json"
    source_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    stage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    incumbent_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    challenger_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_traffic_percent: float = Field(ge=0.0, le=100.0)
    window_start: datetime
    window_end: datetime
    producer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    events: tuple[SafeReleaseObservation, ...]
    pair_count: int = Field(gt=0)
    segment_pair_counts: dict[str, int]
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    external_execution_performed_by_evoagent: Literal[False] = False
    production_traffic_observed_by_evoagent: Literal[False] = False

    @field_validator("candidate_traffic_percent")
    @classmethod
    def validate_percent(cls, value: float) -> float:
        return _require_finite(value, label="Release evidence traffic percent")

    @field_validator("window_start", "window_end")
    @classmethod
    def validate_window_time(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Release evidence window")

    @model_validator(mode="after")
    def validate_batch(self):
        if self.window_end <= self.window_start:
            raise ValueError("Release evidence window end must follow its start.")
        if self.incumbent_snapshot_id == self.challenger_snapshot_id:
            raise ValueError("Release evidence snapshot pair must differ.")
        if not self.events:
            raise ValueError("Release evidence batch requires observations.")
        event_ids = [item.event_id for item in self.events]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("Release evidence contains duplicate event IDs.")
        by_pair: dict[str, list[SafeReleaseObservation]] = {}
        for event in self.events:
            if event.stage_id != self.stage_id:
                raise ValueError("Release observation stage differs from its batch.")
            if not self.window_start <= event.observed_at <= self.window_end:
                raise ValueError("Release observation falls outside its batch window.")
            by_pair.setdefault(event.pair_id, []).append(event)
        for pair_id, observations in by_pair.items():
            if len(observations) != 2:
                raise ValueError(f"Release pair {pair_id} must contain two observations.")
            snapshots = {item.snapshot_id for item in observations}
            if snapshots != {
                self.incumbent_snapshot_id,
                self.challenger_snapshot_id,
            }:
                raise ValueError("Release pair does not contain the exact snapshot pair.")
            if len({item.segment_id for item in observations}) != 1:
                raise ValueError("Release pair observations must share one segment.")
        expected_pair_count = len(by_pair)
        expected_segment_counts: dict[str, int] = {}
        for observations in by_pair.values():
            segment_id = observations[0].segment_id
            expected_segment_counts[segment_id] = (
                expected_segment_counts.get(segment_id, 0) + 1
            )
        if self.pair_count != expected_pair_count:
            raise ValueError("Release batch pair count is not derived.")
        if self.segment_pair_counts != dict(sorted(expected_segment_counts.items())):
            raise ValueError("Release segment pair counts are not derived.")
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        validate_safe_content(payload)
        if self.evidence_hash != canonical_sha256(payload):
            raise ValueError("Release evidence batch hash mismatch.")
        return self


class ReleaseMetricSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    sample_count: int = Field(gt=0)
    success_rate: float = Field(ge=0.0, le=1.0)
    error_rate: float = Field(ge=0.0, le=1.0)
    safety_violation_count: int = Field(ge=0)
    p95_latency_ms: float = Field(gt=0.0)
    average_input_tokens: float | None = Field(default=None, ge=0.0)
    average_output_tokens: float | None = Field(default=None, ge=0.0)
    average_cost_usd: float | None = Field(default=None, ge=0.0)
    summary_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "success_rate",
        "error_rate",
        "p95_latency_ms",
        "average_input_tokens",
        "average_output_tokens",
        "average_cost_usd",
    )
    @classmethod
    def validate_finite_numbers(cls, value: float | None) -> float | None:
        if value is not None:
            return _require_finite(value, label="Release metric summary")
        return value

    @model_validator(mode="after")
    def validate_summary(self):
        if self.safety_violation_count > self.sample_count:
            raise ValueError("Release safety count exceeds sample count.")
        payload = self.model_dump(mode="json", exclude={"summary_hash"})
        if self.summary_hash != canonical_sha256(payload):
            raise ValueError("Release metric summary hash mismatch.")
        return self


class ReleaseUsageComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: Literal["p95_latency_ms", "input_tokens", "output_tokens", "cost_usd"]
    incumbent_value: float | None = None
    challenger_value: float | None = None
    evidence_complete: bool
    unbounded_growth: bool
    growth_ratio: float | None = None
    comparison_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("incumbent_value", "challenger_value", "growth_ratio")
    @classmethod
    def validate_optional_finite(cls, value: float | None) -> float | None:
        if value is not None:
            return _require_finite(value, label="Release usage comparison")
        return value

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.evidence_complete:
            if self.incumbent_value is None or self.challenger_value is None:
                raise ValueError("Complete release comparison requires both values.")
            if self.incumbent_value < 0 or self.challenger_value < 0:
                raise ValueError("Release usage values cannot be negative.")
            if self.incumbent_value == 0:
                expected_unbounded = self.challenger_value > 0
                expected_ratio = 0.0 if self.challenger_value == 0 else None
            else:
                expected_unbounded = False
                expected_ratio = self.challenger_value / self.incumbent_value - 1.0
            if self.unbounded_growth != expected_unbounded:
                raise ValueError("Release unbounded-growth flag is not derived.")
            if expected_ratio is None:
                if self.growth_ratio is not None:
                    raise ValueError("Unbounded release growth has no finite ratio.")
            elif self.growth_ratio is None or abs(self.growth_ratio - expected_ratio) > 1e-12:
                raise ValueError("Release growth ratio is not derived.")
        elif any(
            value is not None
            for value in (
                self.incumbent_value,
                self.challenger_value,
                self.growth_ratio,
            )
        ) or self.unbounded_growth:
            raise ValueError("Incomplete release evidence must not expose derived usage.")
        payload = self.model_dump(mode="json", exclude={"comparison_hash"})
        if self.comparison_hash != canonical_sha256(payload):
            raise ValueError("Release usage comparison hash mismatch.")
        return self


class ReleaseSegmentAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(pattern=_SAFE_ID_PATTERN)
    protected: bool
    pair_count: int = Field(gt=0)
    incumbent_success_rate: float = Field(ge=0.0, le=1.0)
    challenger_success_rate: float = Field(ge=0.0, le=1.0)
    success_delta: float
    incumbent_error_rate: float = Field(ge=0.0, le=1.0)
    challenger_error_rate: float = Field(ge=0.0, le=1.0)
    challenger_safety_violations: int = Field(ge=0)
    regressed: bool
    assessment_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("success_delta")
    @classmethod
    def validate_delta(cls, value: float) -> float:
        return _require_finite(value, label="Release segment delta")

    @model_validator(mode="after")
    def validate_assessment(self):
        expected_delta = self.challenger_success_rate - self.incumbent_success_rate
        if abs(self.success_delta - expected_delta) > 1e-12:
            raise ValueError("Release segment success delta is not derived.")
        if self.regressed != (self.success_delta < -1e-12):
            raise ValueError("Release segment regression flag is not derived.")
        if self.challenger_safety_violations > self.pair_count:
            raise ValueError("Release segment safety count exceeds pairs.")
        payload = self.model_dump(mode="json", exclude={"assessment_hash"})
        if self.assessment_hash != canonical_sha256(payload):
            raise ValueError("Release segment assessment hash mismatch.")
        return self


class ReleaseBootstrapEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    confidence_level: float = Field(gt=0.50, lt=1.0)
    resamples: int = Field(ge=256)
    seed: int = Field(ge=0)
    observed_mean: float
    lower_bound: float
    upper_bound: float
    sample_means_hash: str = Field(pattern=_SHA256_PATTERN)
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("observed_mean", "lower_bound", "upper_bound")
    @classmethod
    def validate_finite_numbers(cls, value: float) -> float:
        return _require_finite(value, label="Release bootstrap value")

    @model_validator(mode="after")
    def validate_bootstrap(self):
        if self.lower_bound > self.upper_bound:
            raise ValueError("Release bootstrap lower bound exceeds upper bound.")
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != canonical_sha256(payload):
            raise ValueError("Release bootstrap evidence hash mismatch.")
        return self


class ReleaseStageAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    assessment_id: str = Field(pattern=_SAFE_ID_PATTERN)
    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    stage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    stage_index: int = Field(ge=0)
    stage_kind: ReleaseStageKind
    candidate_traffic_percent: float = Field(ge=0.0, le=100.0)
    batch_id: str = Field(pattern=_SAFE_ID_PATTERN)
    batch_hash: str = Field(pattern=_SHA256_PATTERN)
    evidence_producer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    incumbent_summary: ReleaseMetricSummary
    challenger_summary: ReleaseMetricSummary
    segment_assessments: tuple[ReleaseSegmentAssessment, ...]
    quality_delta: float
    error_rate_delta: float
    p95_latency: ReleaseUsageComparison
    input_tokens: ReleaseUsageComparison
    output_tokens: ReleaseUsageComparison
    cost: ReleaseUsageComparison
    regressed_segments: int = Field(ge=0)
    regressed_segment_fraction: float = Field(ge=0.0, le=1.0)
    protected_segment_regressions: int = Field(ge=0)
    challenger_safety_violations: int = Field(ge=0)
    bootstrap: ReleaseBootstrapEvidence
    status: ReleaseAssessmentStatus
    reasons: tuple[str, ...]
    assessment_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "candidate_traffic_percent",
        "quality_delta",
        "error_rate_delta",
        "regressed_segment_fraction",
    )
    @classmethod
    def validate_finite_numbers(cls, value: float) -> float:
        return _require_finite(value, label="Release stage assessment")

    @model_validator(mode="after")
    def validate_assessment(self):
        if not self.segment_assessments:
            raise ValueError("Release stage assessment requires segments.")
        expected_delta = (
            self.challenger_summary.success_rate - self.incumbent_summary.success_rate
        )
        expected_error_delta = (
            self.challenger_summary.error_rate - self.incumbent_summary.error_rate
        )
        if abs(self.quality_delta - expected_delta) > 1e-12:
            raise ValueError("Release quality delta is not derived.")
        if abs(self.error_rate_delta - expected_error_delta) > 1e-12:
            raise ValueError("Release error-rate delta is not derived.")
        regressed = sum(item.regressed for item in self.segment_assessments)
        protected = sum(
            item.regressed and item.protected for item in self.segment_assessments
        )
        fraction = regressed / len(self.segment_assessments)
        if (
            self.regressed_segments != regressed
            or self.protected_segment_regressions != protected
            or abs(self.regressed_segment_fraction - fraction) > 1e-12
        ):
            raise ValueError("Release segment regression aggregates are not derived.")
        if self.challenger_safety_violations != (
            self.challenger_summary.safety_violation_count
        ):
            raise ValueError("Release safety aggregate differs from summary.")
        if abs(self.bootstrap.observed_mean - self.quality_delta) > 1e-12:
            raise ValueError("Release bootstrap mean differs from quality delta.")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("Release assessment reasons must be unique.")
        if self.status == ReleaseAssessmentStatus.PASS and self.reasons:
            raise ValueError("Passing release assessment must not have failure reasons.")
        if self.status != ReleaseAssessmentStatus.PASS and not self.reasons:
            raise ValueError("Non-passing release assessment requires reasons.")
        payload = self.model_dump(mode="json", exclude={"assessment_hash"})
        validate_safe_content(payload)
        if self.assessment_hash != canonical_sha256(payload):
            raise ValueError("Release stage assessment hash mismatch.")
        return self


class ReleaseStageDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    stage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    assessment_hash: str = Field(pattern=_SHA256_PATTERN)
    action: ReleaseDecisionAction
    next_stage_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    decision_actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evidence_producer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    decided_at: datetime
    reason: str
    decision_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Release decision time")

    @model_validator(mode="after")
    def validate_decision(self):
        if self.action == ReleaseDecisionAction.ADVANCE:
            if self.next_stage_id is None:
                raise ValueError("Advance decision requires the exact next stage.")
        elif self.next_stage_id is not None:
            raise ValueError("Non-advance release decision must not name a next stage.")
        if not self.reason.strip():
            raise ValueError("Release stage decision reason is required.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        validate_safe_content(payload)
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Release stage decision hash mismatch.")
        return self


class ReleaseHead(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    state: ReleaseState
    incumbent_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    challenger_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    primary_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    active_stage_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    candidate_allocation_percent: float = Field(ge=0.0, le=100.0)
    revision: int = Field(ge=0)
    release_campaign_id: str | None = None
    rollback_campaign_id: str | None = None
    updated_at: datetime

    @field_validator("candidate_allocation_percent")
    @classmethod
    def validate_percent(cls, value: float) -> float:
        return _require_finite(value, label="Release head candidate allocation")

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Release head update time")

    @model_validator(mode="after")
    def validate_head(self):
        if self.primary_snapshot_id != self.incumbent_snapshot_id:
            raise ValueError(
                "Local release control plane must retain the incumbent as primary; "
                "real production deployment is outside this framework."
            )
        if self.state in {
            ReleaseState.PLANNED,
            ReleaseState.AUTHORIZED,
            ReleaseState.ROLLED_BACK,
            ReleaseState.CANCELLED,
        }:
            if self.active_stage_id is not None or self.candidate_allocation_percent != 0.0:
                raise ValueError("Inactive release state must have zero candidate allocation.")
        if self.state == ReleaseState.SHADOW and self.candidate_allocation_percent != 0.0:
            raise ValueError("Shadow release head must have zero candidate allocation.")
        if self.state in {
            ReleaseState.CANARY,
            ReleaseState.HOLD,
            ReleaseState.ROLLBACK_RECOMMENDED,
            ReleaseState.READY,
        } and self.active_stage_id is None:
            raise ValueError("Active release state requires an active stage ID.")
        return self


class ReleaseAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_type: ReleaseEventType
    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    stage_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)


class ReleaseRegistryCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


__all__ = [
    "ReleaseAssessmentStatus",
    "ReleaseAuditEvent",
    "ReleaseBootstrapEvidence",
    "ReleaseDecisionAction",
    "ReleaseEvidenceBatch",
    "ReleaseEvidenceError",
    "ReleaseEvidenceSource",
    "ReleaseEventType",
    "ReleaseHead",
    "ReleaseMetricSummary",
    "ReleasePlan",
    "ReleasePolicy",
    "ReleaseRegistryCheckpoint",
    "ReleaseSegment",
    "ReleaseSegmentAssessment",
    "ReleaseStageAssessment",
    "ReleaseStageDecision",
    "ReleaseStageKind",
    "ReleaseStageSpec",
    "ReleaseState",
    "ReleaseUsageComparison",
    "SafeReleaseObservation",
]