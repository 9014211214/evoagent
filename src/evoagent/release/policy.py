from __future__ import annotations

import math
import random
from datetime import datetime

from evoagent.model_registry.models import canonical_sha256
from evoagent.release.models import (
    ReleaseAssessmentStatus,
    ReleaseBootstrapEvidence,
    ReleaseDecisionAction,
    ReleaseEvidenceBatch,
    ReleaseMetricSummary,
    ReleasePlan,
    ReleaseSegmentAssessment,
    ReleaseStageAssessment,
    ReleaseStageDecision,
    ReleaseStageKind,
    ReleaseUsageComparison,
)


class ReleasePolicyError(ValueError):
    pass


class ReleaseStageGate:
    """Derive hold, advance, rollback, or ready from immutable paired evidence."""

    def assess(
        self,
        plan: ReleasePlan,
        batch: ReleaseEvidenceBatch,
        *,
        assessment_id: str,
    ) -> ReleaseStageAssessment:
        if batch.plan_id != plan.plan_id or batch.plan_hash != plan.plan_hash:
            raise ReleasePolicyError("Release batch differs from its plan.")
        try:
            stage = next(item for item in plan.stages if item.stage_id == batch.stage_id)
        except StopIteration as exc:
            raise ReleasePolicyError("Release batch stage is not in the plan.") from exc
        if (
            batch.incumbent_snapshot_id != plan.incumbent_snapshot_id
            or batch.challenger_snapshot_id != plan.challenger_snapshot_id
            or abs(batch.candidate_traffic_percent - stage.candidate_traffic_percent) > 1e-12
        ):
            raise ReleasePolicyError("Release batch snapshot or allocation drifted.")

        pairs = self._pairs(batch)
        incumbent_events = tuple(
            observations[plan.incumbent_snapshot_id]
            for observations in pairs.values()
        )
        challenger_events = tuple(
            observations[plan.challenger_snapshot_id]
            for observations in pairs.values()
        )
        incumbent_summary = self._summary(
            plan.incumbent_snapshot_id,
            incumbent_events,
        )
        challenger_summary = self._summary(
            plan.challenger_snapshot_id,
            challenger_events,
        )
        segments_by_id = {item.segment_id: item for item in plan.segments}
        segment_assessments = tuple(
            self._segment_assessment(
                segment_id,
                segments_by_id[segment_id].protected,
                tuple(
                    observations
                    for observations in pairs.values()
                    if next(iter(observations.values())).segment_id == segment_id
                ),
                incumbent_snapshot_id=plan.incumbent_snapshot_id,
                challenger_snapshot_id=plan.challenger_snapshot_id,
            )
            for segment_id in sorted(batch.segment_pair_counts)
        )
        pair_deltas = tuple(
            float(
                observations[plan.challenger_snapshot_id].success
                - observations[plan.incumbent_snapshot_id].success
            )
            for observations in pairs.values()
        )
        bootstrap = self._bootstrap(
            pair_deltas,
            confidence=plan.policy.bootstrap_confidence,
            resamples=plan.policy.bootstrap_resamples,
            seed=plan.policy.bootstrap_seed + stage.stage_index * 1_000_003,
        )
        p95_latency = self._usage(
            "p95_latency_ms",
            incumbent_summary.p95_latency_ms,
            challenger_summary.p95_latency_ms,
        )
        input_tokens = self._usage(
            "input_tokens",
            incumbent_summary.average_input_tokens,
            challenger_summary.average_input_tokens,
        )
        output_tokens = self._usage(
            "output_tokens",
            incumbent_summary.average_output_tokens,
            challenger_summary.average_output_tokens,
        )
        cost = self._usage(
            "cost_usd",
            incumbent_summary.average_cost_usd,
            challenger_summary.average_cost_usd,
        )
        quality_delta = challenger_summary.success_rate - incumbent_summary.success_rate
        error_delta = challenger_summary.error_rate - incumbent_summary.error_rate
        regressed = sum(item.regressed for item in segment_assessments)
        protected_regressions = sum(
            item.regressed and item.protected for item in segment_assessments
        )
        fraction = regressed / len(plan.segments)

        insufficient: list[str] = []
        if batch.pair_count < stage.minimum_pairs:
            insufficient.append("minimum_stage_pairs_not_met")
        for segment in plan.segments:
            if batch.segment_pair_counts.get(segment.segment_id, 0) < (
                stage.minimum_pairs_per_segment
            ):
                insufficient.append(
                    f"minimum_segment_pairs_not_met:{segment.segment_id}"
                )
        if plan.policy.require_token_evidence and (
            not input_tokens.evidence_complete
            or not output_tokens.evidence_complete
        ):
            insufficient.append("token_evidence_incomplete")
        if plan.policy.require_cost_evidence and not cost.evidence_complete:
            insufficient.append("cost_evidence_incomplete")

        hard: list[str] = []
        if (
            challenger_summary.safety_violation_count
            > plan.policy.maximum_safety_violations
        ):
            hard.append("maximum_safety_violations_exceeded")
        if plan.policy.protected_segment_zero_regression:
            hard.extend(
                f"protected_segment_regression:{item.segment_id}"
                for item in segment_assessments
                if item.protected and item.regressed
            )
        if regressed > plan.policy.maximum_regressed_segments:
            hard.append("maximum_regressed_segments_exceeded")
        if fraction > plan.policy.maximum_regressed_segment_fraction + 1e-12:
            hard.append("maximum_regressed_segment_fraction_exceeded")

        thresholds: list[str] = []
        if quality_delta < plan.policy.minimum_success_rate_delta - 1e-12:
            thresholds.append("minimum_success_rate_delta_not_met")
        if bootstrap.lower_bound < plan.policy.minimum_delta_lower_bound - 1e-12:
            thresholds.append("minimum_bootstrap_lower_bound_not_met")
        if error_delta > plan.policy.maximum_error_rate_delta + 1e-12:
            thresholds.append("maximum_error_rate_delta_exceeded")
        self._append_growth_reason(
            thresholds,
            p95_latency,
            plan.policy.maximum_p95_latency_growth_ratio,
            "maximum_p95_latency_growth_exceeded",
        )
        self._append_growth_reason(
            thresholds,
            input_tokens,
            plan.policy.maximum_input_token_growth_ratio,
            "maximum_input_token_growth_exceeded",
        )
        self._append_growth_reason(
            thresholds,
            output_tokens,
            plan.policy.maximum_output_token_growth_ratio,
            "maximum_output_token_growth_exceeded",
        )
        self._append_growth_reason(
            thresholds,
            cost,
            plan.policy.maximum_cost_growth_ratio,
            "maximum_cost_growth_exceeded",
        )

        if insufficient:
            status = ReleaseAssessmentStatus.HOLD
            reasons = tuple(sorted(set(insufficient)))
        elif hard:
            status = ReleaseAssessmentStatus.ROLLBACK
            reasons = tuple(sorted(set(hard + thresholds)))
        elif thresholds:
            status = (
                ReleaseAssessmentStatus.HOLD
                if stage.kind == ReleaseStageKind.SHADOW
                else ReleaseAssessmentStatus.ROLLBACK
            )
            reasons = tuple(sorted(set(thresholds)))
        else:
            status = ReleaseAssessmentStatus.PASS
            reasons = ()

        assessment_payload = {
            "assessment_id": assessment_id,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "stage_id": stage.stage_id,
            "stage_index": stage.stage_index,
            "stage_kind": stage.kind,
            "candidate_traffic_percent": stage.candidate_traffic_percent,
            "batch_id": batch.batch_id,
            "batch_hash": batch.evidence_hash,
            "evidence_producer_id": batch.producer_id,
            "incumbent_summary": incumbent_summary,
            "challenger_summary": challenger_summary,
            "segment_assessments": segment_assessments,
            "quality_delta": quality_delta,
            "error_rate_delta": error_delta,
            "p95_latency": p95_latency,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "regressed_segments": regressed,
            "regressed_segment_fraction": fraction,
            "protected_segment_regressions": protected_regressions,
            "challenger_safety_violations": (
                challenger_summary.safety_violation_count
            ),
            "bootstrap": bootstrap,
            "status": status,
            "reasons": reasons,
        }
        return ReleaseStageAssessment(
            **assessment_payload,
            assessment_hash=canonical_sha256(assessment_payload),
        )

    def decide(
        self,
        plan: ReleasePlan,
        assessment: ReleaseStageAssessment,
        *,
        decision_id: str,
        decision_actor_id: str,
        decided_at: datetime,
    ) -> ReleaseStageDecision:
        if assessment.plan_id != plan.plan_id or assessment.plan_hash != plan.plan_hash:
            raise ReleasePolicyError("Release assessment differs from its plan.")
        stage = plan.stages[assessment.stage_index]
        if stage.stage_id != assessment.stage_id:
            raise ReleasePolicyError("Release assessment stage index drifted.")
        if assessment.status == ReleaseAssessmentStatus.HOLD:
            action = ReleaseDecisionAction.HOLD
            next_stage_id = None
            reason = "Release stage held: " + "; ".join(assessment.reasons) + "."
        elif assessment.status == ReleaseAssessmentStatus.ROLLBACK:
            action = ReleaseDecisionAction.ROLLBACK
            next_stage_id = None
            reason = "Release rollback required: " + "; ".join(assessment.reasons) + "."
        elif stage.stage_index == len(plan.stages) - 1:
            action = ReleaseDecisionAction.READY
            next_stage_id = None
            reason = (
                "All frozen shadow and Canary release gates passed; the local "
                "control plane is ready, but no production deployment was performed."
            )
        else:
            action = ReleaseDecisionAction.ADVANCE
            next_stage_id = plan.stages[stage.stage_index + 1].stage_id
            reason = f"Release stage passed; advance to {next_stage_id}."
        payload = {
            "decision_id": decision_id,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "stage_id": stage.stage_id,
            "assessment_hash": assessment.assessment_hash,
            "action": action,
            "next_stage_id": next_stage_id,
            "decision_actor_id": decision_actor_id,
            "evidence_producer_id": assessment.evidence_producer_id,
            "decided_at": decided_at,
            "reason": reason,
        }
        return ReleaseStageDecision(
            **payload,
            decision_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _pairs(batch: ReleaseEvidenceBatch):
        pairs: dict[str, dict[str, object]] = {}
        for event in batch.events:
            pairs.setdefault(event.pair_id, {})[event.snapshot_id] = event
        return dict(sorted(pairs.items()))

    @staticmethod
    def _summary(snapshot_id: str, events) -> ReleaseMetricSummary:
        count = len(events)
        latencies = sorted(float(item.latency_ms) for item in events)
        p95_index = max(0, math.ceil(0.95 * count) - 1)
        inputs = [item.input_tokens for item in events]
        outputs = [item.output_tokens for item in events]
        costs = [item.cost_usd for item in events]
        payload = {
            "snapshot_id": snapshot_id,
            "sample_count": count,
            "success_rate": sum(item.success for item in events) / count,
            "error_rate": sum(item.error for item in events) / count,
            "safety_violation_count": sum(item.safety_violation for item in events),
            "p95_latency_ms": latencies[p95_index],
            "average_input_tokens": (
                sum(inputs) / count if all(value is not None for value in inputs) else None
            ),
            "average_output_tokens": (
                sum(outputs) / count if all(value is not None for value in outputs) else None
            ),
            "average_cost_usd": (
                sum(costs) / count if all(value is not None for value in costs) else None
            ),
        }
        return ReleaseMetricSummary(
            **payload,
            summary_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _segment_assessment(
        segment_id: str,
        protected: bool,
        pairs,
        *,
        incumbent_snapshot_id: str,
        challenger_snapshot_id: str,
    ) -> ReleaseSegmentAssessment:
        incumbent = [item[incumbent_snapshot_id] for item in pairs]
        challenger = [item[challenger_snapshot_id] for item in pairs]
        count = len(pairs)
        payload = {
            "segment_id": segment_id,
            "protected": protected,
            "pair_count": count,
            "incumbent_success_rate": sum(item.success for item in incumbent) / count,
            "challenger_success_rate": sum(item.success for item in challenger) / count,
            "success_delta": (
                sum(item.success for item in challenger) / count
                - sum(item.success for item in incumbent) / count
            ),
            "incumbent_error_rate": sum(item.error for item in incumbent) / count,
            "challenger_error_rate": sum(item.error for item in challenger) / count,
            "challenger_safety_violations": sum(
                item.safety_violation for item in challenger
            ),
        }
        payload["regressed"] = payload["success_delta"] < -1e-12
        return ReleaseSegmentAssessment(
            **payload,
            assessment_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _usage(metric: str, incumbent: float | None, challenger: float | None):
        complete = incumbent is not None and challenger is not None
        if complete:
            unbounded = incumbent == 0 and challenger > 0
            ratio = (
                0.0
                if incumbent == 0 and challenger == 0
                else None
                if unbounded
                else challenger / incumbent - 1.0
            )
        else:
            unbounded = False
            ratio = None
        payload = {
            "metric": metric,
            "incumbent_value": incumbent if complete else None,
            "challenger_value": challenger if complete else None,
            "evidence_complete": complete,
            "unbounded_growth": unbounded,
            "growth_ratio": ratio,
        }
        return ReleaseUsageComparison(
            **payload,
            comparison_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _append_growth_reason(reasons, comparison, maximum, reason):
        if not comparison.evidence_complete:
            return
        if comparison.unbounded_growth or (
            comparison.growth_ratio is not None
            and comparison.growth_ratio > maximum + 1e-12
        ):
            reasons.append(reason)

    @staticmethod
    def _bootstrap(deltas, *, confidence: float, resamples: int, seed: int):
        values = tuple(float(value) for value in deltas)
        if not values:
            raise ReleasePolicyError("Release bootstrap requires paired deltas.")
        observed = sum(values) / len(values)
        rng = random.Random(seed)
        sample_means = [
            sum(values[rng.randrange(len(values))] for _ in values) / len(values)
            for _ in range(resamples)
        ]
        ordered = sorted(sample_means)
        tail = (1.0 - confidence) / 2.0
        lower_index = max(0, min(resamples - 1, math.floor(tail * (resamples - 1))))
        upper_index = max(
            0,
            min(resamples - 1, math.ceil((1.0 - tail) * (resamples - 1))),
        )
        payload = {
            "confidence_level": confidence,
            "resamples": resamples,
            "seed": seed,
            "observed_mean": observed,
            "lower_bound": ordered[lower_index],
            "upper_bound": ordered[upper_index],
            "sample_means_hash": canonical_sha256(sample_means),
        }
        return ReleaseBootstrapEvidence(
            **payload,
            evidence_hash=canonical_sha256(payload),
        )


__all__ = ["ReleasePolicyError", "ReleaseStageGate"]