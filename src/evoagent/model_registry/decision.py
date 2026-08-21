from __future__ import annotations

from datetime import datetime

from evoagent.model_registry.models import (
    ModelActivationDecision,
    ModelActivationThresholds,
    ModelCandidateEvaluationReport,
    canonical_sha256,
)


class ModelActivationPolicy:
    def decide(
        self,
        report: ModelCandidateEvaluationReport,
        *,
        thresholds: ModelActivationThresholds,
        decided_by: str,
        decided_at: datetime,
    ) -> ModelActivationDecision:
        failures: list[str] = []
        if report.held_out_improvement < thresholds.minimum_held_out_improvement:
            failures.append("held-out improvement below threshold")
        if report.replay_candidate_score < thresholds.minimum_replay_score:
            failures.append("replay score below threshold")
        if report.retention_candidate_score < thresholds.minimum_retention_score:
            failures.append("retention score below threshold")
        if report.safety_candidate_score < thresholds.minimum_safety_score:
            failures.append("safety score below threshold")
        if report.regression_count > thresholds.maximum_regressions:
            failures.append("regression count exceeds threshold")
        if report.forgetting_rate > thresholds.maximum_forgetting_rate:
            failures.append("forgetting rate exceeds threshold")
        if report.safety_violation_count > thresholds.maximum_safety_violations:
            failures.append("safety violations exceed threshold")
        if not report.candidate_budget_ok:
            failures.append("candidate evaluation exceeded the frozen budget")
        activate = not failures
        reason = (
            "Independent frozen evaluation passed all activation gates."
            if activate
            else "Activation rejected: " + "; ".join(failures) + "."
        )
        payload = {
            "decision_id": f"model-activation-decision:{report.candidate_id}",
            "family_id": report.family_id,
            "base_model_id": report.base_model_id,
            "candidate_id": report.candidate_id,
            "evaluation_report_hash": report.report_hash,
            "activate": activate,
            "reason": reason,
            "thresholds": thresholds,
            "decided_by": decided_by,
            "decided_at": decided_at,
        }
        return ModelActivationDecision(
            **payload,
            decision_hash=canonical_sha256(payload),
        )


__all__ = ["ModelActivationPolicy"]
