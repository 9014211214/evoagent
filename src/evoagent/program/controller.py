from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApproval,
    CampaignGovernanceService,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
    fingerprint_payload,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.program.feedback import ReleaseFeedbackExtractor
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationOutcome,
    GenerationPlan,
    GenerationRecord,
    GenerationStatus,
    ProgramAction,
    ProgramDecision,
    ProgramHead,
    ProgramLearningSignal,
    ProgramState,
)
from evoagent.program.repository import SQLiteEvolutionProgramRepository
from evoagent.release.models import ReleaseDecisionAction
from evoagent.release.package import (
    ReleaseEvidencePackageManager,
    ReleaseEvidencePackageManifest,
)


class ProgramGenerationSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    generation: GenerationRecord
    campaign: CampaignRecord
    reused: bool


class EvolutionProgramGate:
    """Deterministic stop/continue boundary; release metrics are not attribution."""

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
    ) -> ProgramDecision:
        if outcome.release_action == ReleaseDecisionAction.READY:
            action = ProgramAction.STOP_SUCCESS
            reason = "Generation reached verified local release readiness."
        elif self._budget_exhausted(policy, head):
            action = ProgramAction.STOP_BUDGET
            reason = "Program budget prevents another generation."
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
            decision_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _budget_exhausted(
        policy: EvolutionProgramPolicy,
        head: ProgramHead,
    ) -> bool:
        budget = policy.budget
        return any(
            (
                head.current_generation_index + 1 >= budget.max_generations,
                head.rollback_count > budget.max_rollbacks,
                head.hold_count > budget.max_holds,
                head.generation_campaign_count >= budget.max_generation_campaigns,
                head.total_pairs >= budget.max_total_pairs,
                head.total_tokens >= budget.max_total_tokens,
                head.total_cost_usd >= budget.max_total_cost_usd - 1e-12,
            )
        )

    @staticmethod
    def _attribution_failure(
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
    ) -> str | None:
        if (
            attribution.signal_id != signal.signal_id
            or attribution.signal_hash != signal.signal_hash
        ):
            return "Attribution receipt is bound to another learning signal."
        if policy.require_independent_attributor and (
            not attribution.independent
            or attribution.attributor_id == signal.evidence_producer_id
        ):
            return "Attributor is not independent from the release evidence producer."
        if attribution.confidence < policy.minimum_attribution_confidence:
            return "Attribution confidence is below Program policy."
        if attribution.failure_layer not in policy.allowed_automatic_layers:
            return "Attributed layer is outside the Program automatic intervention allowlist."
        if policy.require_single_supported_experiment and len(
            attribution.supported_experiment_hashes
        ) != 1:
            return "Attribution is ambiguous because it lacks exactly one supported experiment."
        if (
            signal.safety_violation_count > 0
            and policy.safety_feedback_requires_attribution
            and not attribution.supported_experiment_hashes
        ):
            return "Safety feedback lacks a supported causal experiment."
        return None


class EvolutionProgramController:
    """Govern Program registration, exact next-generation authorization and completion."""

    def __init__(
        self,
        *,
        repository: SQLiteEvolutionProgramRepository,
        campaign_governance: CampaignGovernanceService,
        gate: EvolutionProgramGate | None = None,
    ):
        self.repository = repository
        self.campaign_governance = campaign_governance
        self.gate = gate or EvolutionProgramGate()
        self.feedback = ReleaseFeedbackExtractor()

    def register_from_release(
        self,
        package: ReleaseEvidencePackageManifest,
        *,
        program_id: str,
        policy: EvolutionProgramPolicy,
        generation_id: str,
        outcome_id: str,
        created_by: str,
        created_at: datetime,
    ):
        outcome = self.feedback.generation_outcome(
            package,
            program_id=program_id,
            generation_id=generation_id,
            generation_index=0,
            outcome_id=outcome_id,
            completed_at=created_at,
        )
        record, reused = self.repository.register(
            program_id=program_id,
            policy=policy,
            initial_outcome=outcome,
            created_by=created_by,
            now=created_at,
        )
        return record, outcome, reused

    def store_feedback(
        self,
        package: ReleaseEvidencePackageManifest,
        *,
        program_id: str,
        generation_index: int,
        signal_id: str,
        actor_id: str,
        created_at: datetime,
    ) -> tuple[ProgramLearningSignal, bool]:
        signal = self.feedback.extract(
            package,
            program_id=program_id,
            generation_index=generation_index,
            signal_id=signal_id,
            created_at=created_at,
        )
        return self.repository.store_signal(
            signal,
            actor_id=actor_id,
            reason=(
                "Verified release rollback/hold evidence stored without claiming a root cause."
            ),
            now=created_at,
        )

    def store_attribution(
        self,
        program_id: str,
        attribution: AttributionReceipt,
        *,
        actor_id: str,
        created_at: datetime,
    ) -> tuple[AttributionReceipt, bool]:
        return self.repository.store_attribution(
            program_id,
            attribution,
            actor_id=actor_id,
            reason="Independent causal attribution receipt stored.",
            now=created_at,
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
        submitted_at: datetime,
    ) -> ProgramGenerationSubmission:
        program = self.repository.get_program(plan.program_id)
        head = self.repository.head(plan.program_id)
        decisions = self.repository.list_decisions(plan.program_id)
        if not decisions or (
            decisions[-1].action != ProgramAction.CONTINUE
            or decisions[-1].next_generation_index != plan.generation_index
        ):
            raise ValueError("Program has no exact CONTINUE decision for this generation.")
        signal = next(
            item
            for item in self.repository.list_signals(plan.program_id)
            if item.signal_id == plan.source_signal_id
        )
        attribution = next(
            item
            for item in self.repository.list_attributions(plan.program_id)
            if item.receipt_id == plan.attribution_receipt_id
        )
        failure = self.gate._attribution_failure(program.policy, signal, attribution)
        if failure is not None:
            raise ValueError(failure)
        generation, generation_reused = self.repository.plan_generation(
            plan,
            expected_revision=head.revision,
            actor_id=plan.created_by,
            reason="Exact successor GenerationPlan persisted.",
            now=submitted_at,
        )
        reservation = self.campaign_governance.reserve(
            campaign_type=CampaignType.EVOLUTION_GENERATION,
            target_key=self._target_key(plan),
            fingerprint_source=self._fingerprint_source(
                program.policy, signal, attribution, plan
            ),
            risk=CampaignRisk.HIGH,
            generated_by=plan.created_by,
            metadata=self._metadata(program.policy, signal, attribution, plan),
        )
        campaign = reservation.campaign
        if reservation.reused:
            self._validate_campaign(
                campaign,
                policy=program.policy,
                signal=signal,
                attribution=attribution,
                plan=plan,
                require_authorized=False,
            )
        else:
            campaign = self.campaign_governance.attach_candidate(
                campaign,
                candidate_ref=self._candidate_ref(plan),
                artifact_payload=self._artifact_payload(
                    program.policy, signal, attribution, plan
                ),
                actor_id=evaluation_actor_id,
            )
            campaign = self.campaign_governance.submit_evaluation(
                campaign.campaign_id,
                passed=True,
                expected_revision=campaign.revision,
                actor_id=evaluation_actor_id,
                reason=(
                    "GenerationPlan matches verified feedback, attribution and Program budget."
                ),
            )
        head = self.repository.head(plan.program_id)
        generation = self.repository.bind_campaign(
            plan.program_id,
            plan.generation_id,
            campaign.campaign_id,
            expected_revision=head.revision,
            actor_id=evaluation_actor_id,
            reason="High-risk Generation Campaign bound to exact plan.",
            now=submitted_at,
        )
        return ProgramGenerationSubmission(
            generation=generation,
            campaign=campaign,
            reused=generation_reused or reservation.reused,
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
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
        }
        if actor_id in forbidden:
            raise ValueError(
                "Evidence producer, attributor or generation planner cannot approve."
            )
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=False,
        )
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            decision=ApprovalDecision.APPROVE,
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
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Generation Campaign is not AUTHORIZED.")
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        if plan.program_id != program_id or plan.generation_id != generation_id:
            raise ValueError("Generation Campaign targets another Program generation.")
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=True,
        )
        self._validate_approvals(campaign, signal, attribution, plan)
        before = self.repository.head(program_id)
        authorized = self.repository.authorize_generation(
            program_id,
            generation_id,
            campaign_id,
            expected_revision=before.revision,
            actor_id=actor_id,
            reason="Exact Generation Campaign authorization synchronized locally.",
        )
        after = self.repository.head(program_id)
        if after.active_generation_id != before.active_generation_id:
            raise RuntimeError("Generation authorization silently started execution.")
        return authorized

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
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Generation Campaign is not AUTHORIZED.")
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=True,
        )
        self._validate_approvals(campaign, signal, attribution, plan)
        return self.repository.start_generation(
            program_id,
            generation_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason="Explicit local start of the exact authorized generation.",
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
        if generation.plan is None or generation.campaign_id is None:
            raise ValueError("Generation lacks plan or Campaign binding.")
        campaign = self.campaign_governance.repository.get(generation.campaign_id)
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Generation Campaign is not AUTHORIZED at completion.")
        outcome = self.feedback.generation_outcome(
            package,
            program_id=program_id,
            generation_id=generation_id,
            generation_index=generation.generation_index,
            outcome_id=outcome_id,
            completed_at=completed_at,
            plan=generation.plan,
        )
        completed = self.repository.complete_generation(
            outcome,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason="Authorized child release evidence completed the Program generation.",
            now=completed_at,
        )
        campaign = self.campaign_governance.repository.get(campaign.campaign_id)
        if campaign.state == CampaignState.AUTHORIZED:
            campaign = self.campaign_governance.repository.transition(
                campaign.campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id=actor_id,
                reason="Exact authorized generation completed with verified child evidence.",
            )
        if campaign.state != CampaignState.COMPLETED:
            raise RuntimeError("Generation Campaign did not reach COMPLETED.")
        return completed

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
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
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

    @staticmethod
    def _target_key(plan: GenerationPlan) -> str:
        return (
            f"evolution-generation:{plan.program_id}:"
            f"{plan.parent_generation_id}->{plan.generation_id}"
        )

    @staticmethod
    def _candidate_ref(plan: GenerationPlan) -> str:
        return f"program-generation:{plan.program_id}:{plan.generation_id}"

    @staticmethod
    def _fingerprint_source(
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
    ) -> dict:
        return {
            "program_policy_hash": policy.policy_hash,
            "signal_hash": signal.signal_hash,
            "attribution_receipt_hash": attribution.receipt_hash,
            "generation_plan_hash": plan.plan_hash,
            "expected_release_package_hash": plan.expected_release_package_hash,
            "generation_budget": plan.budget.model_dump(mode="json"),
        }

    @staticmethod
    def _metadata(
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
    ) -> dict:
        return {
            "program_id": plan.program_id,
            "generation_id": plan.generation_id,
            "generation_index": plan.generation_index,
            "parent_generation_id": plan.parent_generation_id,
            "policy_hash": policy.policy_hash,
            "signal_id": signal.signal_id,
            "attribution_receipt_id": attribution.receipt_id,
            "external_execution_performed": False,
            "production_deployment_performed": False,
        }

    @staticmethod
    def _artifact_payload(
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
    ) -> dict:
        return {
            "kind": "evolution_generation_candidate",
            "policy": policy.model_dump(mode="json"),
            "signal": signal.model_dump(mode="json"),
            "attribution": attribution.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "external_execution_performed": False,
            "production_deployment_performed": False,
        }

    @staticmethod
    def _campaign_evidence(campaign: CampaignRecord):
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "evolution_generation_candidate":
            raise ValueError("Campaign lacks Evolution Generation evidence.")
        return (
            EvolutionProgramPolicy.model_validate(payload.get("policy")),
            ProgramLearningSignal.model_validate(payload.get("signal")),
            AttributionReceipt.model_validate(payload.get("attribution")),
            GenerationPlan.model_validate(payload.get("plan")),
        )

    def _validate_campaign(
        self,
        campaign: CampaignRecord,
        *,
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
        require_authorized: bool,
    ) -> None:
        expected_fingerprint = fingerprint_payload(
            self._fingerprint_source(policy, signal, attribution, plan)
        )
        if (
            campaign.campaign_type != CampaignType.EVOLUTION_GENERATION
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != plan.created_by
            or campaign.target_key != self._target_key(plan)
            or campaign.fingerprint != expected_fingerprint
            or campaign.candidate_ref != self._candidate_ref(plan)
            or campaign.metadata != self._metadata(policy, signal, attribution, plan)
            or campaign.artifact_payload
            != self._artifact_payload(policy, signal, attribution, plan)
        ):
            raise ValueError("Generation Campaign differs from exact Program evidence.")
        if require_authorized and campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Generation Campaign is not AUTHORIZED.")


__all__ = [
    "EvolutionProgramController",
    "EvolutionProgramGate",
    "ProgramGenerationSubmission",
]
