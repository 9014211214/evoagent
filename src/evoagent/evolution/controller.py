from __future__ import annotations

from evoagent.diagnosis.counterfactual import AttributionReport
from evoagent.domain.models import (
    EvolutionAction,
    EvolutionDecision,
    EvolutionTicket,
    FailureReport,
)


class EvolutionController:
    def decide(self, report: FailureReport) -> EvolutionDecision:
        if report.recommended_action == EvolutionAction.UPDATE_SKILL:
            return EvolutionDecision(
                action=EvolutionAction.UPDATE_SKILL,
                confidence=report.confidence,
                rationale=(
                    "Verified skill-layer failure; prefer low-cost skill intervention "
                    "before model training."
                ),
                estimated_cost=1.0,
                estimated_risk="low",
            )
        if report.recommended_action == EvolutionAction.NO_ACTION:
            return EvolutionDecision(
                action=EvolutionAction.NO_ACTION,
                confidence=1.0,
                rationale="No verified failure.",
                estimated_cost=0,
                estimated_risk="low",
            )
        return EvolutionDecision(
            action=EvolutionAction.ESCALATE,
            confidence=report.confidence,
            rationale="v0.1 refuses automatic mutation for unverified cross-layer failures.",
            estimated_cost=0,
            estimated_risk="medium",
        )

    def decide_attribution(self, report: AttributionReport) -> EvolutionDecision:
        if not report.actionable:
            return EvolutionDecision(
                action=EvolutionAction.ESCALATE,
                confidence=report.confidence,
                rationale=report.reason,
                estimated_cost=0.0,
                estimated_risk="medium",
            )
        risk = "high" if report.recommended_action == EvolutionAction.TRAIN_MODEL else "low"
        cost = 100.0 if report.recommended_action == EvolutionAction.TRAIN_MODEL else 1.0
        return EvolutionDecision(
            action=report.recommended_action,
            confidence=report.confidence,
            rationale=report.reason,
            estimated_cost=cost,
            estimated_risk=risk,
        )

    def create_ticket(
        self,
        report: AttributionReport,
        *,
        ticket_id: str,
        target_id: str | None,
        evidence_trace_ids: list[str],
    ) -> EvolutionTicket:
        if not report.actionable:
            raise ValueError("Cannot create an evolution ticket from non-actionable attribution.")
        return EvolutionTicket(
            ticket_id=ticket_id,
            target_layer=report.root_cause_layer,
            target_id=target_id,
            evidence_trace_ids=evidence_trace_ids,
            proposed_action=report.recommended_action,
            expected_benefit=(
                f"Resolve verified {report.root_cause_layer.value} failure pattern."
            ),
            required_evaluations=["held_out", "regression", "safety"],
        )
