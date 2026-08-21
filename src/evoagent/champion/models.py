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


class ChampionError(ValueError):
    pass


def _require_timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _require_finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class ChampionRoundStatus(str, Enum):
    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ChampionDecisionAction(str, Enum):
    PROMOTE = "promote"
    HOLD = "hold"
    REJECT = "reject"


class ChampionStopRecommendation(str, Enum):
    STOP = "stop"
    CONTINUE = "continue"
    HOLD = "hold"


class ChampionVersionStatus(str, Enum):
    CHAMPION = "champion"
    CHALLENGER = "challenger"
    EVALUATED = "evaluated"
    AUTHORIZED = "authorized"
    RETIRED = "retired"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class ChampionEventType(str, Enum):
    REGISTERED = "registered"
    DECISION_STORED = "decision_stored"
    CHALLENGER_ADMITTED = "challenger_admitted"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    AUTHORIZED = "authorized"
    ACTIVATED = "activated"
    ROLLED_BACK = "rolled_back"


class ChampionPromotionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    minimum_score_gain: float = 0.10
    bootstrap_confidence: float = Field(default=0.80, gt=0.50, lt=1.0)
    bootstrap_resamples: int = Field(default=4096, ge=256, le=100_000)
    bootstrap_seed: int = Field(default=17, ge=0)
    minimum_gain_lower_bound: float = 0.0
    maximum_regressed_tasks: int = Field(default=0, ge=0)
    maximum_regression_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_error_rate_delta: float = Field(default=0.0, ge=0.0)
    maximum_input_token_growth_ratio: float = Field(default=0.50, ge=0.0)
    maximum_output_token_growth_ratio: float = Field(default=0.50, ge=0.0)
    maximum_cost_growth_ratio: float = Field(default=0.50, ge=0.0)
    require_token_evidence: bool = True
    require_cost_evidence: bool = True
    allow_non_final_round: bool = True
    patience_rounds: int = Field(default=1, ge=0)
    require_same_model_comparator: bool = False
    maximum_anchor_rank: int = Field(default=2, ge=1)
    minimum_pairwise_score_delta: float = -1.0
    maximum_pairwise_losses: int = Field(default=1_000_000, ge=0)
    policy_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator(
        "minimum_score_gain",
        "minimum_gain_lower_bound",
        "maximum_error_rate_delta",
        "maximum_input_token_growth_ratio",
        "maximum_output_token_growth_ratio",
        "maximum_cost_growth_ratio",
        "minimum_pairwise_score_delta",
    )
    @classmethod
    def validate_finite_numbers(cls, value: float) -> float:
        return _require_finite(value, label="Champion policy numeric value")

    @model_validator(mode="after")
    def validate_policy(self):
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        validate_safe_content(payload)
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Champion promotion policy hash mismatch.")
        return self


class ChampionTaskDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_name: str = Field(pattern=_SAFE_ID_PATTERN)
    baseline_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    delta: float
    delta_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("delta")
    @classmethod
    def validate_delta_number(cls, value: float) -> float:
        return _require_finite(value, label="Champion Task delta")

    @model_validator(mode="after")
    def validate_delta(self):
        if abs(self.delta - (self.candidate_score - self.baseline_score)) > 1e-12:
            raise ValueError("Champion Task delta is not derived.")
        payload = self.model_dump(mode="json", exclude={"delta_hash"})
        if self.delta_hash != canonical_sha256(payload):
            raise ValueError("Champion Task delta hash mismatch.")
        return self


class ChampionBootstrapEvidence(BaseModel):
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
        return _require_finite(value, label="Champion bootstrap value")

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.lower_bound > self.upper_bound:
            raise ValueError("Champion bootstrap lower bound exceeds upper bound.")
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != canonical_sha256(payload):
            raise ValueError("Champion bootstrap evidence hash mismatch.")
        return self


class ChampionUsageComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: Literal["input_tokens", "output_tokens", "cost_usd"]
    baseline_value: float | None = None
    candidate_value: float | None = None
    evidence_complete: bool
    unbounded_growth: bool
    growth_ratio: float | None = None
    comparison_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("baseline_value", "candidate_value", "growth_ratio")
    @classmethod
    def validate_optional_finite(cls, value: float | None) -> float | None:
        if value is not None:
            _require_finite(value, label="Champion usage comparison")
        return value

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.evidence_complete:
            if self.baseline_value is None or self.candidate_value is None:
                raise ValueError("Complete usage evidence requires both values.")
            if self.baseline_value < 0 or self.candidate_value < 0:
                raise ValueError("Usage values cannot be negative.")
            if self.baseline_value == 0:
                expected_unbounded = self.candidate_value > 0
                expected_ratio = 0.0 if self.candidate_value == 0 else None
            else:
                expected_unbounded = False
                expected_ratio = self.candidate_value / self.baseline_value - 1.0
            if self.unbounded_growth != expected_unbounded:
                raise ValueError("Usage unbounded-growth flag is not derived.")
            if expected_ratio is None:
                if self.growth_ratio is not None:
                    raise ValueError("Unbounded usage growth must not expose a finite ratio.")
            elif self.growth_ratio is None or abs(self.growth_ratio - expected_ratio) > 1e-12:
                raise ValueError("Usage growth ratio is not derived.")
        else:
            if (
                self.baseline_value is not None
                or self.candidate_value is not None
                or self.growth_ratio is not None
                or self.unbounded_growth
            ):
                raise ValueError("Incomplete usage evidence must not expose derived values.")
        payload = self.model_dump(mode="json", exclude={"comparison_hash"})
        if self.comparison_hash != canonical_sha256(payload):
            raise ValueError("Champion usage comparison hash mismatch.")
        return self


class ChampionComparatorEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_hash: str = Field(pattern=_SHA256_PATTERN)
    anchor_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    anchor_rank: int = Field(gt=0)
    comparator_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    score_delta: float
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    ties: int = Field(ge=0)
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("score_delta")
    @classmethod
    def validate_score_delta(cls, value: float) -> float:
        return _require_finite(value, label="Champion comparator score delta")

    @model_validator(mode="after")
    def validate_evidence(self):
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != canonical_sha256(payload):
            raise ValueError("Champion comparator evidence hash mismatch.")
        return self


class ChampionRoundAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evolution_round: int = Field(ge=1)
    score: float = Field(ge=0.0, le=1.0)
    gain: float
    task_deltas: tuple[ChampionTaskDelta, ...]
    improved_tasks: int = Field(ge=0)
    regressed_tasks: int = Field(ge=0)
    tied_tasks: int = Field(ge=0)
    regression_fraction: float = Field(ge=0.0, le=1.0)
    error_rate_delta: float
    input_tokens: ChampionUsageComparison
    output_tokens: ChampionUsageComparison
    cost: ChampionUsageComparison
    bootstrap: ChampionBootstrapEvidence
    comparator: ChampionComparatorEvidence | None = None
    status: ChampionRoundStatus
    reasons: tuple[str, ...]
    assessment_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("gain", "regression_fraction", "error_rate_delta")
    @classmethod
    def validate_finite_numbers(cls, value: float) -> float:
        return _require_finite(value, label="Champion round assessment")

    @model_validator(mode="after")
    def validate_assessment(self):
        if not self.task_deltas:
            raise ValueError("Champion round assessment requires Task deltas.")
        improved = sum(item.delta > 1e-12 for item in self.task_deltas)
        regressed = sum(item.delta < -1e-12 for item in self.task_deltas)
        tied = len(self.task_deltas) - improved - regressed
        if (self.improved_tasks, self.regressed_tasks, self.tied_tasks) != (
            improved,
            regressed,
            tied,
        ):
            raise ValueError("Champion Task outcome counts are not derived.")
        expected_fraction = regressed / len(self.task_deltas)
        if abs(self.regression_fraction - expected_fraction) > 1e-12:
            raise ValueError("Champion regression fraction is not derived.")
        expected_gain = sum(item.delta for item in self.task_deltas) / len(
            self.task_deltas
        )
        if abs(self.gain - expected_gain) > 1e-12:
            raise ValueError("Champion aggregate gain differs from Task deltas.")
        if abs(self.bootstrap.observed_mean - self.gain) > 1e-12:
            raise ValueError("Champion bootstrap mean differs from aggregate gain.")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("Champion assessment reasons must be unique.")
        if self.status == ChampionRoundStatus.ELIGIBLE and self.reasons:
            raise ValueError("Eligible Champion round must not contain failure reasons.")
        if self.status != ChampionRoundStatus.ELIGIBLE and not self.reasons:
            raise ValueError("Non-eligible Champion round requires reasons.")
        payload = self.model_dump(mode="json", exclude={"assessment_hash"})
        validate_safe_content(payload)
        if self.assessment_hash != canonical_sha256(payload):
            raise ValueError("Champion round assessment hash mismatch.")
        return self


class ChampionSelectionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    benchmark_package_hash: str = Field(pattern=_SHA256_PATTERN)
    longitudinal_report_hash: str = Field(pattern=_SHA256_PATTERN)
    policy: ChampionPromotionPolicy
    baseline_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    baseline_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    assessments: tuple[ChampionRoundAssessment, ...]
    selected_run_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    selected_snapshot_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    selected_round: int | None = Field(default=None, ge=1)
    action: ChampionDecisionAction
    stop_recommendation: ChampionStopRecommendation
    continue_evolution: bool
    reason: str
    decision_actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    decided_at: datetime
    decision_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Champion decision time")

    @model_validator(mode="after")
    def validate_decision(self):
        if not self.assessments:
            raise ValueError("Champion selection decision requires evolved rounds.")
        rounds = [item.evolution_round for item in self.assessments]
        if rounds != sorted(rounds) or len(set(rounds)) != len(rounds):
            raise ValueError("Champion assessments must be unique and round-ordered.")
        selected = (
            self.selected_run_id,
            self.selected_snapshot_id,
            self.selected_round,
        )
        if self.action == ChampionDecisionAction.PROMOTE:
            if any(value is None for value in selected):
                raise ValueError("Promotion decision requires an exact selected round.")
            matches = [
                item
                for item in self.assessments
                if item.run_id == self.selected_run_id
                and item.snapshot_id == self.selected_snapshot_id
                and item.evolution_round == self.selected_round
            ]
            if len(matches) != 1 or matches[0].status != ChampionRoundStatus.ELIGIBLE:
                raise ValueError("Selected Champion round is not exactly one eligible assessment.")
        elif any(value is not None for value in selected):
            raise ValueError("Non-promotion decision must not select a Challenger.")
        if self.continue_evolution != (
            self.stop_recommendation == ChampionStopRecommendation.CONTINUE
        ):
            raise ValueError("Champion continue flag differs from stop recommendation.")
        if not self.reason.strip():
            raise ValueError("Champion decision reason is required.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        validate_safe_content(payload)
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Champion selection decision hash mismatch.")
        return self


class ChampionSnapshotRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    benchmark_evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    benchmark_package_hash: str = Field(pattern=_SHA256_PATTERN)
    parent_snapshot_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    status: ChampionVersionStatus
    decision_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    decision_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    policy_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    campaign_id: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Champion record time")

    @model_validator(mode="after")
    def validate_record(self):
        if self.parent_snapshot_id is None:
            if any(
                value is not None
                for value in (
                    self.decision_id,
                    self.decision_hash,
                    self.policy_hash,
                    self.campaign_id,
                )
            ):
                raise ValueError("Initial Champion must not contain Challenger governance fields.")
        else:
            if any(
                value is None
                for value in (
                    self.decision_id,
                    self.decision_hash,
                    self.policy_hash,
                    self.campaign_id,
                )
            ):
                raise ValueError("Challenger requires complete governance bindings.")
        return self


class ChampionAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_type: ChampionEventType
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    from_snapshot_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    to_snapshot_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)


class ChampionRegistryCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


__all__ = [
    "ChampionAuditEvent",
    "ChampionBootstrapEvidence",
    "ChampionComparatorEvidence",
    "ChampionDecisionAction",
    "ChampionError",
    "ChampionEventType",
    "ChampionPromotionPolicy",
    "ChampionRegistryCheckpoint",
    "ChampionRoundAssessment",
    "ChampionRoundStatus",
    "ChampionSelectionDecision",
    "ChampionSnapshotRecord",
    "ChampionStopRecommendation",
    "ChampionTaskDelta",
    "ChampionUsageComparison",
    "ChampionVersionStatus",
]
