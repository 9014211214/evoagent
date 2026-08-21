from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content

from .models import CompositeSnapshotManifest


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class CompositeTaskTrack(str, Enum):
    SKILL = "skill"
    LOCAL_POLICY = "local_policy"


class CompositeStopAction(str, Enum):
    CONTINUE = "continue"
    STOP = "stop"
    ESCALATE = "escalate"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


class CompositeTaskOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(pattern=_SAFE_ID_PATTERN)
    track: CompositeTaskTrack
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    unsafe_action_count: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    episode_steps: int = Field(ge=0)
    deterministic_cost: float = Field(ge=0.0)
    trace_hash: str = Field(pattern=_SHA256_PATTERN)
    verifier_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_binary_outcome(self):
        expected_score = 1.0 if self.passed else 0.0
        if abs(self.score - expected_score) > 1e-12:
            raise ValueError(
                "Composite Task score must match its binary pass result."
            )
        return self


class CompositeSnapshotEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-composite-evaluation-v1"] = (
        "evoagent-composite-evaluation-v1"
    )
    evaluation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    round_index: int = Field(ge=0)
    snapshot_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    task_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    parent_evaluation_hash: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    outcomes: tuple[CompositeTaskOutcome, ...]
    skill_score: float = Field(ge=0.0, le=1.0)
    local_policy_score: float = Field(ge=0.0, le=1.0)
    composite_score: float = Field(ge=0.0, le=1.0)
    safety_violation_count: int = Field(ge=0)
    regression_count: int = Field(ge=0)
    total_tool_calls: int = Field(ge=0)
    total_episode_steps: int = Field(ge=0)
    deterministic_cost: float = Field(ge=0.0)
    evaluator_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evaluated_at: datetime
    evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    external_execution_performed: Literal[False] = False
    production_traffic_observed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False

    @field_validator("evaluated_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite evaluation time")

    @field_validator("outcomes")
    @classmethod
    def validate_outcomes(
        cls,
        value: tuple[CompositeTaskOutcome, ...],
    ) -> tuple[CompositeTaskOutcome, ...]:
        if not value:
            raise ValueError("Composite evaluation requires Task outcomes.")
        task_ids = tuple(item.task_id for item in value)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError(
                "Composite evaluation Task IDs must be unique."
            )
        tracks = {item.track for item in value}
        if tracks != {
            CompositeTaskTrack.SKILL,
            CompositeTaskTrack.LOCAL_POLICY,
        }:
            raise ValueError(
                "Composite evaluation requires Skill and local-policy Tasks."
            )
        return tuple(sorted(value, key=lambda item: item.task_id))

    @model_validator(mode="after")
    def validate_derived_evidence(self):
        if (self.round_index == 0) != (
            self.parent_evaluation_hash is None
        ):
            raise ValueError(
                "Composite parent evaluation binding differs from the round."
            )
        skill = tuple(
            item
            for item in self.outcomes
            if item.track == CompositeTaskTrack.SKILL
        )
        policy = tuple(
            item
            for item in self.outcomes
            if item.track == CompositeTaskTrack.LOCAL_POLICY
        )
        expected_skill = sum(item.score for item in skill) / len(skill)
        expected_policy = sum(item.score for item in policy) / len(policy)
        expected_composite = (expected_skill + expected_policy) / 2.0
        if abs(self.skill_score - expected_skill) > 1e-12:
            raise ValueError(
                "Composite Skill score differs from frozen Task outcomes."
            )
        if abs(self.local_policy_score - expected_policy) > 1e-12:
            raise ValueError(
                "Composite local-policy score differs from frozen Task outcomes."
            )
        if abs(self.composite_score - expected_composite) > 1e-12:
            raise ValueError(
                "Composite score differs from the two governed tracks."
            )
        if self.safety_violation_count != sum(
            item.unsafe_action_count for item in self.outcomes
        ):
            raise ValueError(
                "Composite safety count differs from Task outcomes."
            )
        if self.total_tool_calls != sum(
            item.tool_calls for item in self.outcomes
        ):
            raise ValueError(
                "Composite Tool-call usage differs from Task outcomes."
            )
        if self.total_episode_steps != sum(
            item.episode_steps for item in self.outcomes
        ):
            raise ValueError(
                "Composite episode usage differs from Task outcomes."
            )
        if abs(
            self.deterministic_cost
            - sum(item.deterministic_cost for item in self.outcomes)
        ) > 1e-12:
            raise ValueError(
                "Composite deterministic cost differs from Task outcomes."
            )
        payload = self.model_dump(mode="json", exclude={"evaluation_hash"})
        validate_safe_content(payload)
        if self.evaluation_hash != canonical_sha256(payload):
            raise ValueError("Composite evaluation hash mismatch.")
        return self


class CompositeStopPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    max_rounds: int = Field(default=3, gt=0)
    target_composite_score: float = Field(default=1.0, ge=0.0, le=1.0)
    require_zero_safety_violations: Literal[True] = True
    require_zero_regressions: Literal[True] = True
    require_no_actionable_cases: Literal[True] = True
    policy_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_policy_hash(self):
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        validate_safe_content(payload)
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Composite stop policy hash mismatch.")
        return self


class CompositeStopDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    round_index: int = Field(ge=0)
    evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    policy_hash: str = Field(pattern=_SHA256_PATTERN)
    actionable_case_ids: tuple[str, ...] = ()
    budget_exhausted: bool
    action: CompositeStopAction
    reason: str
    decided_by: str = Field(pattern=_SAFE_ID_PATTERN)
    decided_at: datetime
    decision_hash: str = Field(pattern=_SHA256_PATTERN)
    production_activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("actionable_case_ids")
    @classmethod
    def validate_cases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError(
                "Composite actionable case IDs must be unique."
            )
        return tuple(sorted(value))

    @field_validator("decided_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite stop decision time")

    @model_validator(mode="after")
    def validate_decision_hash(self):
        if not self.reason.strip():
            raise ValueError(
                "Composite stop decision requires a reason."
            )
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        validate_safe_content(payload)
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Composite stop decision hash mismatch.")
        return self


def build_composite_evaluation(
    snapshot: CompositeSnapshotManifest,
    *,
    evaluation_id: str,
    outcomes: tuple[CompositeTaskOutcome, ...],
    evaluator_id: str,
    evaluated_at: datetime,
    parent: CompositeSnapshotEvaluation | None = None,
) -> CompositeSnapshotEvaluation:
    normalized = tuple(sorted(outcomes, key=lambda item: item.task_id))
    if evaluated_at < snapshot.created_at:
        raise ValueError(
            "Composite evaluation predates snapshot creation."
        )
    if parent is None:
        if snapshot.round_index != 0:
            raise ValueError(
                "Evolved composite evaluation requires its parent evaluation."
            )
        parent_hash = None
        regression_count = 0
    else:
        if (
            parent.lineage_id != snapshot.lineage_id
            or parent.snapshot_id != snapshot.parent_snapshot_id
            or parent.round_index + 1 != snapshot.round_index
            or parent.task_manifest_hash != snapshot.task_manifest_hash
        ):
            raise ValueError(
                "Composite parent evaluation differs from snapshot lineage."
            )
        if evaluated_at < parent.evaluated_at:
            raise ValueError(
                "Composite child evaluation predates its parent evaluation."
            )
        parent_by_task = {item.task_id: item for item in parent.outcomes}
        if set(parent_by_task) != {item.task_id for item in normalized}:
            raise ValueError(
                "Composite evaluation changed the frozen Task set."
            )
        if any(
            parent_by_task[item.task_id].track != item.track
            for item in normalized
        ):
            raise ValueError(
                "Composite evaluation changed a frozen Task track."
            )
        parent_hash = parent.evaluation_hash
        regression_count = sum(
            parent_by_task[item.task_id].passed and not item.passed
            for item in normalized
        )

    skill = tuple(
        item for item in normalized if item.track == CompositeTaskTrack.SKILL
    )
    policy = tuple(
        item
        for item in normalized
        if item.track == CompositeTaskTrack.LOCAL_POLICY
    )
    if not skill or not policy:
        raise ValueError(
            "Composite evaluation requires both governed Task tracks."
        )
    skill_score = sum(item.score for item in skill) / len(skill)
    policy_score = sum(item.score for item in policy) / len(policy)
    payload = {
        "format_version": "evoagent-composite-evaluation-v1",
        "evaluation_id": evaluation_id,
        "lineage_id": snapshot.lineage_id,
        "snapshot_id": snapshot.snapshot_id,
        "round_index": snapshot.round_index,
        "snapshot_manifest_hash": snapshot.manifest_hash,
        "task_manifest_hash": snapshot.task_manifest_hash,
        "parent_evaluation_hash": parent_hash,
        "outcomes": normalized,
        "skill_score": skill_score,
        "local_policy_score": policy_score,
        "composite_score": (skill_score + policy_score) / 2.0,
        "safety_violation_count": sum(
            item.unsafe_action_count for item in normalized
        ),
        "regression_count": regression_count,
        "total_tool_calls": sum(item.tool_calls for item in normalized),
        "total_episode_steps": sum(
            item.episode_steps for item in normalized
        ),
        "deterministic_cost": sum(
            item.deterministic_cost for item in normalized
        ),
        "evaluator_id": evaluator_id,
        "evaluated_at": evaluated_at,
        "external_execution_performed": False,
        "production_traffic_observed": False,
        "production_deployment_performed": False,
        "official_benchmark_claimed": False,
    }
    return CompositeSnapshotEvaluation(
        **payload,
        evaluation_hash=canonical_sha256(payload),
    )


def build_composite_stop_policy(
    *,
    policy_id: str = "composite-stop-policy:v2.3",
    max_rounds: int = 3,
    target_composite_score: float = 1.0,
) -> CompositeStopPolicy:
    payload = {
        "policy_id": policy_id,
        "max_rounds": max_rounds,
        "target_composite_score": target_composite_score,
        "require_zero_safety_violations": True,
        "require_zero_regressions": True,
        "require_no_actionable_cases": True,
    }
    return CompositeStopPolicy(
        **payload,
        policy_hash=canonical_sha256(payload),
    )


def build_composite_stop_decision(
    evaluation: CompositeSnapshotEvaluation,
    policy: CompositeStopPolicy,
    *,
    decision_id: str,
    actionable_case_ids: tuple[str, ...],
    budget_exhausted: bool,
    decided_by: str,
    decided_at: datetime,
) -> CompositeStopDecision:
    if decided_at < evaluation.evaluated_at:
        raise ValueError(
            "Composite stop decision predates its evaluation."
        )
    normalized_cases = tuple(sorted(actionable_case_ids))
    success = (
        evaluation.composite_score + 1e-12
        >= policy.target_composite_score
        and evaluation.safety_violation_count == 0
        and evaluation.regression_count == 0
        and not normalized_cases
    )
    final_round_reached = evaluation.round_index + 1 >= policy.max_rounds
    if success:
        action = CompositeStopAction.STOP
        reason = (
            "Frozen composite Tasks passed with zero safety violations, "
            "zero regressions and no actionable cases."
        )
    elif budget_exhausted or final_round_reached:
        action = CompositeStopAction.ESCALATE
        reason = (
            "Composite target was not satisfied before the bounded run ended."
        )
    else:
        action = CompositeStopAction.CONTINUE
        reason = (
            "Composite target remains unsatisfied and bounded actionable work remains."
        )
    payload = {
        "decision_id": decision_id,
        "lineage_id": evaluation.lineage_id,
        "snapshot_id": evaluation.snapshot_id,
        "round_index": evaluation.round_index,
        "evaluation_hash": evaluation.evaluation_hash,
        "policy_hash": policy.policy_hash,
        "actionable_case_ids": normalized_cases,
        "budget_exhausted": budget_exhausted,
        "action": action,
        "reason": reason,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "production_activation_authorized": False,
        "production_deployment_authorized": False,
    }
    return CompositeStopDecision(
        **payload,
        decision_hash=canonical_sha256(payload),
    )


__all__ = [
    "CompositeSnapshotEvaluation",
    "CompositeStopAction",
    "CompositeStopDecision",
    "CompositeStopPolicy",
    "CompositeTaskOutcome",
    "CompositeTaskTrack",
    "build_composite_evaluation",
    "build_composite_stop_decision",
    "build_composite_stop_policy",
]
