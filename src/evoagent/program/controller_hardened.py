from __future__ import annotations

from datetime import datetime

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApproval,
    CampaignGovernanceService,
    CampaignRecord,
)
from evoagent.program.constraints import BOUNDED_AUTOMATIC_INTERVENTION_LAYERS
from evoagent.program.controller import (
    EvolutionProgramController,
    EvolutionProgramGate,
    ProgramGenerationSubmission,
)
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationOutcome,
    GenerationPlan,
    GenerationRecord,
    ProgramAction,
    ProgramDecision,
    ProgramHead,
    ProgramLearningSignal,
)
from evoagent.program.repository import SQLiteEvolutionProgramRepository
from evoagent.release.models import ReleaseDecisionAction
from evoagent.release.package import ReleaseEvidencePackageManifest


class HardenedEvolutionProgramGate(EvolutionProgramGate):
    """Apply every immutable Program stop and continuation policy field."""

    def decide(
        self,
        *,
        policy: EvolutionProgramPolicy,
        head: ProgramHead,
        outcome: GenerationOutcome,
        decision_id: str,
        decided_by: str,
        decided_at: datetime,
        signal: ProgramLearningSignal | None = None,
        attribution: AttributionReceipt | None = None,
        consecutive_non_improving_count: int = 0,
    ) -> ProgramDecision:
        if not policy.require_generation_approvals:
            raise ValueError(
                "High-risk successor generations cannot disable independent approvals."
            )
        if outcome.release_action == ReleaseDecisionAction.READY:
            if policy.stop_on_ready:
                action = ProgramAction.STOP_SUCCESS
                reason = "Generation reached verified local release readiness."
            else:
                action = ProgramAction.PAUSE
                reason = (
                    "Generation reached readiness, but policy requires an explicit owner "
                    "decision before any further optimization."
                )
        elif self._budget_exhausted(
            policy,
            head,
            consecutive_non_improving_count=consecutive_non_improving_count,
        ):
            action = ProgramAction.STOP_BUDGET
            reason = "Program budget or non-improvement limit prevents another generation."
        elif signal is None or attribution is None:
            action = ProgramAction.PAUSE
            reason = (
                "Rollback/hold metrics are observable feedback, not causal attribution; "
                "an independent attribution receipt is required."
            )
        else:
            failure = self._attribution_failure(policy, signal, attribution)
            if failure is not None:
                action = ProgramAction.ESCALATE
                reason = failure
            else:
                action = ProgramAction.CONTINUE
                reason = (
                    "Verified single-layer attribution and remaining budget authorize "
                    "planning exactly one successor generation."
                )
        payload = {
            "decision_id": decision_id,
            "program_id": outcome.program_id,
            "generation_id": outcome.generation_id,
            "generation_index": outcome.generation_index,
            "source_outcome_hash": outcome.outcome_hash,
            "action": action,
            "reason": reason,
            "next_generation_index": (
                outcome.generation_index + 1
                if action == ProgramAction.CONTINUE
                else None
            ),
            "decided_by": decided_by,
            "decided_at": decided_at,
        }
        return ProgramDecision(
            **payload,
            decision_hash=program_payload_hash(payload),
        )

    @staticmethod
    def _budget_exhausted(
        policy: EvolutionProgramPolicy,
        head: ProgramHead,
        *,
        consecutive_non_improving_count: int = 0,
    ) -> bool:
        budget = policy.budget
        return any(
            (
                head.current_generation_index + 1 >= budget.max_generations,
                (
                    head.rollback_count > 0
                    and head.rollback_count >= budget.max_rollbacks
                ),
                head.hold_count > 0 and head.hold_count >= budget.max_holds,
                head.generation_campaign_count >= budget.max_generation_campaigns,
                head.total_pairs >= budget.max_total_pairs,
                head.total_tokens >= budget.max_total_tokens,
                head.total_cost_usd
                > budget.max_total_cost_usd + 1e-12,
                consecutive_non_improving_count
                > policy.maximum_consecutive_non_improving,
            )
        )

    @staticmethod
    def _attribution_failure(
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
    ) -> str | None:
        if attribution.failure_layer not in BOUNDED_AUTOMATIC_INTERVENTION_LAYERS:
            return (
                "Attributed layer is outside the immutable bounded Program "
                "intervention set."
            )
        return EvolutionProgramGate._attribution_failure(
            policy,
            signal,
            attribution,
        )


class HardenedEvolutionProgramController(EvolutionProgramController):
    """Bind decisions, plans, budgets, approvals, authorization and execution."""

    def __init__(
        self,
        *,
        repository: SQLiteEvolutionProgramRepository,
        campaign_governance: CampaignGovernanceService,
        gate: HardenedEvolutionProgramGate | None = None,
    ):
        super().__init__(
            repository=repository,
            campaign_governance=campaign_governance,
            gate=gate or HardenedEvolutionProgramGate(),
        )

    def decide(
        self,
        *,
        program_id: str,
        generation_id: str,
        decision_id: str,
        decided_by: str,
        decided_at: datetime,
        signal: ProgramLearningSignal | None = None,
        attribution: AttributionReceipt | None = None,
    ) -> tuple[ProgramDecision, bool]:
        program = self.repository.get_program(program_id)
        head = self.repository.head(program_id)
        generation = self.repository.get_generation(program_id, generation_id)
        if generation.outcome is None:
            raise ValueError("Program cannot decide before a generation outcome exists.")
        decision = self.gate.decide(
            policy=program.policy,
            head=head,
            outcome=generation.outcome,
            decision_id=decision_id,
            decided_by=decided_by,
            decided_at=decided_at,
            signal=signal,
            attribution=attribution,
            consecutive_non_improving_count=(
                self._consecutive_non_improving_count(program_id)
            ),
        )
        return self.repository.store_decision(
            decision,
            expected_revision=head.revision,
            actor_id=decided_by,
            now=decided_at,
        )

    def submit_generation(
        self,
        plan: GenerationPlan,
        *,
        evaluation_actor_id: str,
        submitted_at,
    ) -> ProgramGenerationSubmission:
        decision = self._continue_decision(plan)
        if plan.created_by != decision.decided_by:
            raise ValueError(
                "Generation planner must be the actor bound to the exact CONTINUE decision."
            )
        signal, attribution = self._persisted_plan_evidence(plan)
        self._validate_evaluation_actor(
            evaluation_actor_id,
            signal=signal,
            attribution=attribution,
            plan=plan,
            decision=decision,
        )
        return super().submit_generation(
            plan,
            evaluation_actor_id=evaluation_actor_id,
            submitted_at=submitted_at,
        )

    def approve_generation(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ) -> CampaignRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        _, signal, attribution, plan = self._campaign_evidence(campaign)
        decision = self._continue_decision(plan)
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
            *self._generation_evaluation_actors(campaign_id),
        }
        if actor_id in forbidden:
            raise ValueError(
                "Evidence producer, attributor, evaluator or decision/planning "
                "actor cannot approve."
            )
        return super().approve_generation(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
        )

    def synchronize_authorization(
        self,
        *,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        actor_id: str,
    ) -> GenerationRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        self._validate_operation_actor(campaign, actor_id)
        return super().synchronize_authorization(
            program_id=program_id,
            generation_id=generation_id,
            campaign_id=campaign_id,
            actor_id=actor_id,
        )

    def start_generation(
        self,
        *,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        expected_revision: int,
        actor_id: str,
    ) -> GenerationRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        self._validate_operation_actor(campaign, actor_id)
        return super().start_generation(
            program_id=program_id,
            generation_id=generation_id,
            campaign_id=campaign_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )

    def complete_generation(
        self,
        package: ReleaseEvidencePackageManifest,
        *,
        program_id: str,
        generation_id: str,
        outcome_id: str,
        expected_revision: int,
        actor_id: str,
        completed_at: datetime,
    ) -> GenerationRecord:
        generation = self.repository.get_generation(program_id, generation_id)
        if generation.campaign_id is None:
            raise ValueError("Generation lacks Campaign binding.")
        campaign = self.campaign_governance.repository.get(generation.campaign_id)
        self._validate_operation_actor(campaign, actor_id)
        return super().complete_generation(
            package,
            program_id=program_id,
            generation_id=generation_id,
            outcome_id=outcome_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            completed_at=completed_at,
        )

    def _persisted_plan_evidence(
        self,
        plan: GenerationPlan,
    ) -> tuple[ProgramLearningSignal, AttributionReceipt]:
        signals = tuple(
            item
            for item in self.repository.list_signals(plan.program_id)
            if item.signal_id == plan.source_signal_id
            and item.signal_hash == plan.source_signal_hash
        )
        attributions = tuple(
            item
            for item in self.repository.list_attributions(plan.program_id)
            if item.receipt_id == plan.attribution_receipt_id
            and item.receipt_hash == plan.attribution_receipt_hash
        )
        if len(signals) != 1 or len(attributions) != 1:
            raise ValueError(
                "GenerationPlan does not bind one exact persisted signal and attribution."
            )
        signal = signals[0]
        attribution = attributions[0]
        if (
            attribution.signal_id != signal.signal_id
            or attribution.signal_hash != signal.signal_hash
        ):
            raise ValueError(
                "Persisted Generation attribution differs from its signal."
            )
        return signal, attribution

    @staticmethod
    def _validate_evaluation_actor(
        evaluation_actor_id: str,
        *,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
        decision: ProgramDecision,
    ) -> None:
        if evaluation_actor_id in {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
        }:
            raise ValueError(
                "Generation evaluator must differ from evidence producer, "
                "attributor and decision/planning actor."
            )

    def _generation_evaluation_actors(self, campaign_id: str) -> set[str]:
        actors: set[str] = set()
        for event in self.campaign_governance.repository.audit_events():
            if event.campaign_id != campaign_id:
                continue
            if event.event_type == "candidate_attached":
                actors.add(event.actor_id)
            elif (
                event.event_type == "campaign_transitioned"
                and event.payload.get("to_state")
                in {"evaluation_pending", "approval_pending"}
            ):
                actors.add(event.actor_id)
        return actors

    def _validate_operation_actor(
        self,
        campaign: CampaignRecord,
        actor_id: str,
    ) -> None:
        _, signal, attribution, plan = self._campaign_evidence(campaign)
        decision = self._continue_decision(plan)
        approvals = tuple(
            self.campaign_governance.repository.approvals(campaign.campaign_id)
        )
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
            *(item.actor_id for item in approvals),
        }
        if actor_id in forbidden:
            raise ValueError(
                "Generation authorization or execution actor must differ from "
                "evidence, attribution, decision/planning and approval actors."
            )

    def _validate_approvals(
        self,
        campaign: CampaignRecord,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
    ) -> tuple[CampaignApproval, ...]:
        approvals = tuple(
            self.campaign_governance.repository.approvals(campaign.campaign_id)
        )
        approving = tuple(
            item for item in approvals if item.decision == ApprovalDecision.APPROVE
        )
        actors = tuple(item.actor_id for item in approving)
        decision = self._continue_decision(plan)
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
            *self._generation_evaluation_actors(campaign.campaign_id),
        }
        if (
            campaign.required_approvals != 2
            or len(approvals) != 2
            or len(approving) != 2
            or len(set(actors)) != 2
            or set(actors) & forbidden
        ):
            raise ValueError(
                "Generation requires exactly two independent approving actors."
            )
        return approvals

    def _continue_decision(self, plan: GenerationPlan) -> ProgramDecision:
        matches = [
            item
            for item in self.repository.list_decisions(plan.program_id)
            if item.action == ProgramAction.CONTINUE
            and item.next_generation_index == plan.generation_index
            and item.generation_id == plan.parent_generation_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "GenerationPlan must bind exactly one persisted CONTINUE decision."
            )
        return matches[0]

    def _consecutive_non_improving_count(self, program_id: str) -> int:
        count = 0
        for generation in reversed(self.repository.list_generations(program_id)):
            outcome = generation.outcome
            if outcome is None:
                continue
            if outcome.release_action == ReleaseDecisionAction.READY:
                break
            count += 1
        return count


__all__ = [
    "HardenedEvolutionProgramController",
    "HardenedEvolutionProgramGate",
]
