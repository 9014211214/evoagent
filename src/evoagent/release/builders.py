from __future__ import annotations

from datetime import datetime

from evoagent.model_registry.models import canonical_sha256
from evoagent.release.models import (
    ReleaseEvidenceSource,
    ReleasePlan,
    ReleasePolicy,
    ReleaseSegment,
    ReleaseStageKind,
    ReleaseStageSpec,
    SafeReleaseObservation,
)


def build_release_segment(
    segment_id: str,
    *,
    protected: bool = False,
) -> ReleaseSegment:
    payload = {
        "segment_id": segment_id,
        "protected": protected,
    }
    return ReleaseSegment(**payload, segment_hash=canonical_sha256(payload))


def build_release_stage(
    *,
    stage_id: str,
    stage_index: int,
    kind: ReleaseStageKind,
    candidate_traffic_percent: float,
    minimum_pairs: int,
    minimum_pairs_per_segment: int,
    observation_window_seconds: int,
) -> ReleaseStageSpec:
    payload = {
        "stage_id": stage_id,
        "stage_index": stage_index,
        "kind": kind,
        "candidate_traffic_percent": candidate_traffic_percent,
        "minimum_pairs": minimum_pairs,
        "minimum_pairs_per_segment": minimum_pairs_per_segment,
        "observation_window_seconds": observation_window_seconds,
    }
    return ReleaseStageSpec(**payload, stage_hash=canonical_sha256(payload))


def build_release_policy(
    *,
    policy_id: str = "release-policy:strict-v1",
    minimum_success_rate_delta: float = -0.02,
    bootstrap_confidence: float = 0.80,
    bootstrap_resamples: int = 4096,
    bootstrap_seed: int = 29,
    minimum_delta_lower_bound: float = -0.25,
    maximum_error_rate_delta: float = 0.02,
    maximum_safety_violations: int = 0,
    maximum_p95_latency_growth_ratio: float = 0.50,
    maximum_input_token_growth_ratio: float = 0.50,
    maximum_output_token_growth_ratio: float = 0.50,
    maximum_cost_growth_ratio: float = 0.50,
    maximum_regressed_segments: int = 0,
    maximum_regressed_segment_fraction: float = 0.0,
    protected_segment_zero_regression: bool = True,
    require_token_evidence: bool = True,
    require_cost_evidence: bool = True,
    hold_on_insufficient_evidence: bool = True,
) -> ReleasePolicy:
    payload = {
        "policy_id": policy_id,
        "minimum_success_rate_delta": minimum_success_rate_delta,
        "bootstrap_confidence": bootstrap_confidence,
        "bootstrap_resamples": bootstrap_resamples,
        "bootstrap_seed": bootstrap_seed,
        "minimum_delta_lower_bound": minimum_delta_lower_bound,
        "maximum_error_rate_delta": maximum_error_rate_delta,
        "maximum_safety_violations": maximum_safety_violations,
        "maximum_p95_latency_growth_ratio": maximum_p95_latency_growth_ratio,
        "maximum_input_token_growth_ratio": maximum_input_token_growth_ratio,
        "maximum_output_token_growth_ratio": maximum_output_token_growth_ratio,
        "maximum_cost_growth_ratio": maximum_cost_growth_ratio,
        "maximum_regressed_segments": maximum_regressed_segments,
        "maximum_regressed_segment_fraction": maximum_regressed_segment_fraction,
        "protected_segment_zero_regression": protected_segment_zero_regression,
        "require_token_evidence": require_token_evidence,
        "require_cost_evidence": require_cost_evidence,
        "hold_on_insufficient_evidence": hold_on_insufficient_evidence,
    }
    return ReleasePolicy(**payload, policy_hash=canonical_sha256(payload))


def build_release_plan(
    *,
    plan_id: str,
    champion_package_hash: str,
    family_id: str,
    incumbent_snapshot_id: str,
    challenger_snapshot_id: str,
    champion_decision_hash: str,
    runtime_config_sha256: str,
    tool_contract_sha256: str,
    segments: tuple[ReleaseSegment, ...],
    stages: tuple[ReleaseStageSpec, ...],
    policy: ReleasePolicy,
    evidence_source: ReleaseEvidenceSource,
    created_by: str,
    created_at: datetime,
    source_commit: str,
) -> ReleasePlan:
    payload = {
        "plan_id": plan_id,
        "champion_package_hash": champion_package_hash,
        "family_id": family_id,
        "incumbent_snapshot_id": incumbent_snapshot_id,
        "challenger_snapshot_id": challenger_snapshot_id,
        "champion_decision_hash": champion_decision_hash,
        "runtime_config_sha256": runtime_config_sha256,
        "tool_contract_sha256": tool_contract_sha256,
        "segments": segments,
        "stages": stages,
        "policy": policy,
        "evidence_source": evidence_source,
        "created_by": created_by,
        "created_at": created_at,
        "source_commit": source_commit,
        "production_deployment_authorized": False,
    }
    return ReleasePlan(**payload, plan_hash=canonical_sha256(payload))


def build_release_observation(
    *,
    event_id: str,
    pair_id: str,
    stage_id: str,
    segment_id: str,
    snapshot_id: str,
    success: bool,
    error: bool,
    safety_violation: bool,
    latency_ms: float,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    observed_at: datetime,
) -> SafeReleaseObservation:
    payload = {
        "event_id": event_id,
        "pair_id": pair_id,
        "stage_id": stage_id,
        "segment_id": segment_id,
        "snapshot_id": snapshot_id,
        "success": success,
        "error": error,
        "safety_violation": safety_violation,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "observed_at": observed_at,
    }
    return SafeReleaseObservation(**payload, event_hash=canonical_sha256(payload))


__all__ = [
    "build_release_observation",
    "build_release_plan",
    "build_release_policy",
    "build_release_segment",
    "build_release_stage",
]