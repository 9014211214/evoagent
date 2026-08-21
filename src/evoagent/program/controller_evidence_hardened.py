from __future__ import annotations

from datetime import datetime, timedelta

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignRisk,
    CampaignState,
    CampaignType,
    StaleCampaignRevision,
)
from evoagent.program.controller import ProgramGenerationSubmission
from evoagent.program.controller_retry_hardened import (
    RetryHardenedEvolutionProgramController as _RetryHardenedController,
)
from evoagent.program.models import (
    AttributionReceipt,
    GenerationPlan,
    GenerationStatus,
    ProgramAction,
    ProgramDecision,
    ProgramLearningSignal,
)
from evoagent.program.repository import ProgramConflictError
from evoagent.release.package import ReleaseEvidencePackageManifest


_TERMINAL_GENERATION_STATUSES = {
    GenerationStatus.COMPLETED,
    GenerationStatus.ROLLED_BACK,
    GenerationStatus.HELD,
}


class RetryHardenedEvolutionProgramController(_RetryHardenedController):
    """Final public Controller with retry, evidence, role and time hardening."""

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
        generation = self.repository.get_generation(program_id, generation_id)
        if generation.outcome is None:
            raise ValueError(
                "Program cannot decide before a generation outcome exists."
            )
        if decided_at < generation.outcome.completed_at:
            raise ValueError(
                "Program decision time precedes its generation outcome."
            )
        if attribution is not None and signal is None:
            raise ValueError(
                "Program decision attribution requires its learning signal."
            )

        persisted_signal = None
        persisted_attribution = None
        if signal is not None:
            persisted_signal = self._persisted_signal_evidence(
                program_id=program_id,
                generation_index=generation.generation_index,
                signal=signal,
            )
            if decided_at < persisted_signal.created_at:
                raise ValueError(
                    "Program decision time precedes its learning signal."
                )
            if decided_by == persisted_signal.evidence_producer_id:
                raise ValueError(
                    "Program decision actor must differ from evidence producer."
                )
        if attribution is not None:
            persisted_attribution = self._persisted_attribution_evidence(
                program_id=program_id,
                signal=persisted_signal,
                attribution=attribution,
            )
            if decided_at < persisted_attribution.created_at:
                raise ValueError(
                    "Program decision time precedes its causal attribution."
                )
            if decided_by == persisted_attribution.attributor_id:
                raise ValueError(
                    "Program decision actor must differ from causal attributor."
                )

        matches = tuple(
            item
            for item in self.repository.list_decisions(program_id)
            if item.decision_id == decision_id
        )
        if matches:
            existing = matches[0]
            if (
                len(matches) != 1
                or existing.program_id != program_id
                or existing.generation_id != generation_id
                or existing.generation_index != generation.generation_index
                or existing.source_outcome_hash
                != generation.outcome.outcome_hash
                or existing.decided_by != decided_by
                or existing.decided_at != decided_at
            ):
                raise ProgramConflictError(
                    "Program decision retry conflicts with immutable evidence."
                )
            if (
                existing.action == ProgramAction.CONTINUE
                and (
                    persisted_signal is None
                    or persisted_attribution is None
                )
            ):
                raise ValueError(
                    "CONTINUE decision retry requires exact persisted "
                    "signal and attribution."
                )
            return existing, True

        return super().decide(
            program_id=program_id,
            generation_id=generation_id,
            decision_id=decision_id,
            decided_by=decided_by,
            decided_at=decided_at,
            signal=persisted_signal,
            attribution=persisted_attribution,
        )

    def submit_generation(
        self,
        plan: GenerationPlan,
        *,
        evaluation_actor_id: str,
        submitted_at: datetime,
    ) -> ProgramGenerationSubmission:
        decision = self._continue_decision(plan)
        if plan.created_by != decision.decided_by:
            raise ValueError(
                "Generation planner must be the actor bound to the exact "
                "CONTINUE decision."
            )
        if plan.created_at < decision.decided_at:
            raise ValueError(
                "GenerationPlan time precedes its CONTINUE decision."
            )
        if submitted_at < plan.created_at:
            raise ValueError(
                "Generation submission time precedes its immutable plan."
            )

        policy, signal, attribution = self._persisted_generation_evidence(plan)
        self._validate_evaluation_actor(
            evaluation_actor_id,
            signal=signal,
            attribution=attribution,
            plan=plan,
            decision=decision,
        )

        try:
            generation = self.repository.get_generation(
                plan.program_id,
                plan.generation_id,
            )
            generation_reused = True
            if generation.plan != plan:
                raise ProgramConflictError(
                    "Generation ID has conflicting immutable plan."
                )
        except KeyError:
            head = self.repository.head(plan.program_id)
            generation, generation_reused = (
                self.repository.plan_generation(
                    plan,
                    expected_revision=head.revision,
                    actor_id=plan.created_by,
                    reason="Exact successor GenerationPlan persisted.",
                    now=submitted_at,
                )
            )

        phase_at = max(submitted_at, generation.updated_at)
        if generation.campaign_id is not None:
            campaign = self.campaign_governance.repository.get(
                generation.campaign_id
            )
            campaign = self._advance_or_validate_submission_campaign(
                campaign,
                policy=policy,
                signal=signal,
                attribution=attribution,
                plan=plan,
                evaluation_actor_id=evaluation_actor_id,
                phase_at=phase_at,
            )
            return ProgramGenerationSubmission(
                generation=generation,
                campaign=campaign,
                reused=True,
            )

        campaign = self.campaign_governance.repository.find_open_by_target(
            self._target_key(plan)
        )
        campaign_reused = campaign is not None
        if campaign is None:
            reservation = self.campaign_governance.reserve(
                campaign_type=CampaignType.EVOLUTION_GENERATION,
                target_key=self._target_key(plan),
                fingerprint_source=self._fingerprint_source(
                    policy,
                    signal,
                    attribution,
                    plan,
                ),
                risk=CampaignRisk.HIGH,
                generated_by=plan.created_by,
                metadata=self._metadata(
                    policy,
                    signal,
                    attribution,
                    plan,
                ),
                now=phase_at,
            )
            campaign = reservation.campaign
            campaign_reused = reservation.reused

        campaign = self._advance_or_validate_submission_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            evaluation_actor_id=evaluation_actor_id,
            phase_at=max(phase_at, campaign.updated_at),
        )
        bind_at = max(phase_at, campaign.updated_at)
        head = self.repository.head(plan.program_id)
        generation = self.repository.bind_campaign(
            plan.program_id,
            plan.generation_id,
            campaign.campaign_id,
            expected_revision=head.revision,
            actor_id=evaluation_actor_id,
            reason=(
                "Recovered and bound the exact partially created "
                "Generation Campaign."
                if generation_reused or campaign_reused
                else "High-risk Generation Campaign bound to exact plan."
            ),
            now=bind_at,
        )
        return ProgramGenerationSubmission(
            generation=generation,
            campaign=campaign,
            reused=generation_reused or campaign_reused,
        )

    def approve_generation(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ):
        campaign = self.campaign_governance.repository.get(campaign_id)
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        decision = self._continue_decision(plan)
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=False,
        )
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
            *self._generation_evaluation_actors(campaign_id),
        }
        if actor_id in forbidden:
            raise ValueError(
                "Evidence producer, attributor, evaluator or "
                "decision/planning actor cannot approve."
            )

        existing = tuple(
            item
            for item in self.campaign_governance.repository.approvals(
                campaign_id
            )
            if item.actor_id == actor_id
        )
        if existing:
            if (
                len(existing) != 1
                or existing[0].decision != ApprovalDecision.APPROVE
                or existing[0].reason != reason
            ):
                raise ValueError(
                    "Campaign approval retry conflicts with the immutable "
                    "decision."
                )
            return campaign

        approved_at = campaign.updated_at + timedelta(seconds=1)
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            expected_revision=expected_revision,
            now=approved_at,
        )

    def synchronize_authorization(
        self,
        *,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        actor_id: str,
    ):
        record = self.repository.get_generation(program_id, generation_id)
        campaign = self.campaign_governance.repository.get(campaign_id)
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        if (
            record.campaign_id != campaign_id
            or plan.program_id != program_id
            or plan.generation_id != generation_id
        ):
            raise ValueError(
                "Generation Campaign targets another Program generation."
            )
        if campaign.state not in {
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            raise ValueError("Generation Campaign is not AUTHORIZED.")
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=(
                campaign.state == CampaignState.AUTHORIZED
            ),
        )
        self._validate_approvals(campaign, signal, attribution, plan)
        self._validate_operation_actor(campaign, actor_id)

        if record.status in {
            GenerationStatus.AUTHORIZED,
            GenerationStatus.RUNNING,
            *_TERMINAL_GENERATION_STATUSES,
        }:
            return record
        if record.status != GenerationStatus.PLANNED:
            raise ValueError(
                "Only a planned generation may synchronize authorization."
            )
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError(
                "Completed Campaign cannot newly authorize a generation."
            )

        before = self.repository.head(program_id)
        authorized_at = max(
            before.updated_at,
            campaign.updated_at,
        ) + timedelta(seconds=1)
        authorized = self.repository.authorize_generation(
            program_id,
            generation_id,
            campaign_id,
            expected_revision=before.revision,
            actor_id=actor_id,
            reason=(
                "Exact Generation Campaign authorization synchronized locally."
            ),
            now=authorized_at,
        )
        after = self.repository.head(program_id)
        if (
            after.active_generation_id != before.active_generation_id
            or after.current_generation_index
            != before.current_generation_index
        ):
            raise RuntimeError(
                "Generation authorization silently started execution."
            )
        return authorized

    def start_generation(
        self,
        *,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        expected_revision: int,
        actor_id: str,
    ):
        record = self.repository.get_generation(program_id, generation_id)
        campaign = self.campaign_governance.repository.get(campaign_id)
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        if (
            record.campaign_id != campaign_id
            or plan.program_id != program_id
            or plan.generation_id != generation_id
        ):
            raise ValueError(
                "Generation Campaign targets another Program generation."
            )
        if campaign.state not in {
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            raise ValueError("Generation Campaign is not AUTHORIZED.")
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=(
                campaign.state == CampaignState.AUTHORIZED
            ),
        )
        self._validate_approvals(campaign, signal, attribution, plan)
        self._validate_operation_actor(campaign, actor_id)

        if record.status in {
            GenerationStatus.RUNNING,
            *_TERMINAL_GENERATION_STATUSES,
        }:
            return record
        if record.status != GenerationStatus.AUTHORIZED:
            raise ValueError(
                "Only an authorized generation may start."
            )
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError(
                "Completed Campaign cannot newly start a generation."
            )

        head = self.repository.head(program_id)
        started_at = max(
            head.updated_at,
            campaign.updated_at,
        ) + timedelta(seconds=1)
        return self.repository.start_generation(
            program_id,
            generation_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason="Explicit local start of the exact authorized generation.",
            now=started_at,
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
    ):
        record = self.repository.get_generation(program_id, generation_id)
        if record.plan is None or record.campaign_id is None:
            raise ValueError("Generation lacks plan or Campaign binding.")
        campaign = self.campaign_governance.repository.get(
            record.campaign_id
        )
        expected_outcome = self.feedback.generation_outcome(
            package,
            program_id=program_id,
            generation_id=generation_id,
            generation_index=record.generation_index,
            outcome_id=outcome_id,
            completed_at=completed_at,
            plan=record.plan,
        )

        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        if (
            plan.program_id != program_id
            or plan.generation_id != generation_id
            or record.campaign_id != campaign.campaign_id
        ):
            raise ValueError(
                "Generation Campaign targets another Program generation."
            )
        if campaign.state not in {
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            raise ValueError(
                "Generation Campaign is not AUTHORIZED at completion."
            )
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=(
                campaign.state == CampaignState.AUTHORIZED
            ),
        )
        self._validate_approvals(campaign, signal, attribution, plan)

        if record.outcome is not None:
            if record.outcome != expected_outcome:
                raise ProgramConflictError(
                    "Generation completion retry differs from immutable "
                    "outcome."
                )
            if campaign.state == CampaignState.AUTHORIZED:
                self._validate_operation_actor(campaign, actor_id)
                if completed_at < campaign.updated_at:
                    raise ValueError(
                        "Generation completion time precedes Campaign "
                        "authorization."
                    )
                campaign = self._complete_campaign_at(
                    campaign,
                    actor_id=actor_id,
                    completed_at=completed_at,
                    reason=(
                        "Recovered exact completed generation after partial "
                        "cross-registry commit."
                    ),
                )
            return record

        if record.status != GenerationStatus.RUNNING:
            raise ValueError("Only a running generation may complete.")
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError(
                "Completed Campaign cannot newly complete a generation."
            )
        self._validate_operation_actor(campaign, actor_id)
        head = self.repository.head(program_id)
        if completed_at < max(
            head.updated_at,
            campaign.updated_at,
            record.updated_at,
        ):
            raise ValueError(
                "Generation completion time precedes authorization or "
                "execution evidence."
            )

        completed = self.repository.complete_generation(
            expected_outcome,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=(
                "Authorized child release evidence completed the Program "
                "generation."
            ),
            now=completed_at,
        )
        campaign = self._complete_campaign_at(
            campaign,
            actor_id=actor_id,
            completed_at=completed_at,
            reason=(
                "Exact authorized generation completed with verified child "
                "evidence."
            ),
        )
        if campaign.state != CampaignState.COMPLETED:
            raise RuntimeError(
                "Generation Campaign did not reach COMPLETED."
            )
        return completed

    def _persisted_signal_evidence(
        self,
        *,
        program_id: str,
        generation_index: int,
        signal: ProgramLearningSignal,
    ) -> ProgramLearningSignal:
        matches = tuple(
            item
            for item in self.repository.list_signals(program_id)
            if item.signal_id == signal.signal_id
        )
        if (
            len(matches) != 1
            or matches[0] != signal
            or signal.program_id != program_id
            or signal.generation_index != generation_index
        ):
            raise ValueError(
                "Program decision signal is not the exact persisted "
                "generation evidence."
            )
        return matches[0]

    def _persisted_attribution_evidence(
        self,
        *,
        program_id: str,
        signal: ProgramLearningSignal | None,
        attribution: AttributionReceipt,
    ) -> AttributionReceipt:
        if signal is None:
            raise ValueError(
                "Program decision attribution requires its learning signal."
            )
        matches = tuple(
            item
            for item in self.repository.list_attributions(program_id)
            if item.receipt_id == attribution.receipt_id
        )
        if (
            len(matches) != 1
            or matches[0] != attribution
            or attribution.signal_id != signal.signal_id
            or attribution.signal_hash != signal.signal_hash
        ):
            raise ValueError(
                "Program decision attribution is not the exact persisted "
                "signal binding."
            )
        return matches[0]

    def _validate_operation_actor(self, campaign, actor_id: str) -> None:
        super()._validate_operation_actor(campaign, actor_id)
        if actor_id in self._generation_evaluation_actors(
            campaign.campaign_id
        ):
            raise ValueError(
                "Generation execution actor must differ from independent "
                "evaluator."
            )

    def _advance_or_validate_submission_campaign(
        self,
        campaign,
        *,
        policy,
        signal,
        attribution,
        plan,
        evaluation_actor_id: str,
        phase_at: datetime,
    ):
        self._validate_campaign_shell(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
        )
        if campaign.created_at < plan.created_at:
            raise ValueError(
                "Generation Campaign time precedes its immutable plan."
            )
        phase_at = max(phase_at, campaign.updated_at)
        if campaign.state in {
            CampaignState.OPEN,
            CampaignState.EVIDENCE_ACCUMULATING,
        }:
            if (
                campaign.candidate_ref is not None
                or campaign.artifact_payload is not None
            ):
                raise ValueError(
                    "Pre-candidate Generation Campaign already exposes "
                    "candidate evidence."
                )
            campaign = self.campaign_governance.attach_candidate(
                campaign,
                candidate_ref=self._candidate_ref(plan),
                artifact_payload=self._artifact_payload(
                    policy,
                    signal,
                    attribution,
                    plan,
                ),
                actor_id=evaluation_actor_id,
                now=phase_at,
            )
        if campaign.state in {
            CampaignState.CANDIDATE_READY,
            CampaignState.EVALUATION_PENDING,
        }:
            self._validate_campaign(
                campaign,
                policy=policy,
                signal=signal,
                attribution=attribution,
                plan=plan,
                require_authorized=False,
            )
            campaign = self.campaign_governance.submit_evaluation(
                campaign.campaign_id,
                passed=True,
                expected_revision=campaign.revision,
                actor_id=evaluation_actor_id,
                reason=(
                    "Recovered exact GenerationPlan evaluation after partial "
                    "submission."
                ),
                now=max(phase_at, campaign.updated_at),
            )
        if campaign.state == CampaignState.APPROVAL_PENDING:
            self._validate_campaign(
                campaign,
                policy=policy,
                signal=signal,
                attribution=attribution,
                plan=plan,
                require_authorized=False,
            )
            return campaign
        if campaign.state in {
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            self._validate_existing_campaign(campaign)
            return campaign
        raise ValueError(
            "Generation Campaign cannot resume from its persisted lifecycle "
            "state."
        )

    def _complete_campaign_at(
        self,
        campaign,
        *,
        actor_id: str,
        completed_at: datetime,
        reason: str,
    ):
        try:
            recovered = self.campaign_governance.repository.transition(
                campaign.campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id=actor_id,
                reason=reason,
                now=completed_at,
            )
        except StaleCampaignRevision:
            recovered = self.campaign_governance.repository.get(
                campaign.campaign_id
            )
        if recovered.state != CampaignState.COMPLETED:
            raise RuntimeError(
                "Concurrent Campaign completion did not converge."
            )
        self._validate_existing_campaign(recovered)
        return recovered


__all__ = ["RetryHardenedEvolutionProgramController"]
