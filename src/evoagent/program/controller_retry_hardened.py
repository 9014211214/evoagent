from __future__ import annotations

from datetime import datetime

from evoagent.campaigns import (
    CampaignRisk,
    CampaignState,
    CampaignType,
    StaleCampaignRevision,
    fingerprint_payload,
)
from evoagent.program.controller import ProgramGenerationSubmission
from evoagent.program.controller_hardened import (
    HardenedEvolutionProgramController,
    HardenedEvolutionProgramGate,
)
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationPlan,
    GenerationRecord,
    GenerationStatus,
    ProgramLearningSignal,
)
from evoagent.program.repository import ProgramConflictError
from evoagent.release.package import ReleaseEvidencePackageManifest


_TERMINAL_OR_RUNNING = {
    GenerationStatus.RUNNING,
    GenerationStatus.COMPLETED,
    GenerationStatus.ROLLED_BACK,
    GenerationStatus.HELD,
}


class RetryHardenedEvolutionProgramController(
    HardenedEvolutionProgramController
):
    """Allow exact retries and repair bounded cross-registry partial commits."""

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
        if actor_id == signal.evidence_producer_id:
            raise ValueError(
                "Release evidence producer cannot ingest its own Program feedback."
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
        if actor_id != attribution.attributor_id:
            raise ValueError(
                "Attribution receipt must be stored by its declared attributor."
            )
        return super().store_attribution(
            program_id,
            attribution,
            actor_id=actor_id,
            created_at=created_at,
        )

    def submit_generation(
        self,
        plan: GenerationPlan,
        *,
        evaluation_actor_id: str,
        submitted_at: datetime,
    ) -> ProgramGenerationSubmission:
        try:
            generation = self.repository.get_generation(
                plan.program_id,
                plan.generation_id,
            )
        except KeyError:
            return super().submit_generation(
                plan,
                evaluation_actor_id=evaluation_actor_id,
                submitted_at=submitted_at,
            )
        if generation.plan != plan:
            raise ProgramConflictError(
                "Generation ID has conflicting immutable plan."
            )
        decision = self._continue_decision(plan)
        if plan.created_by != decision.decided_by:
            raise ValueError(
                "Generation planner must be the actor bound to the exact CONTINUE decision."
            )
        policy, signal, attribution = self._persisted_generation_evidence(plan)
        self._validate_evaluation_actor(
            evaluation_actor_id,
            signal=signal,
            attribution=attribution,
            plan=plan,
            decision=decision,
        )

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
            )
            return ProgramGenerationSubmission(
                generation=generation,
                campaign=campaign,
                reused=True,
            )

        campaign = self.campaign_governance.repository.find_open_by_target(
            self._target_key(plan)
        )
        if campaign is None:
            return super().submit_generation(
                plan,
                evaluation_actor_id=evaluation_actor_id,
                submitted_at=submitted_at,
            )
        campaign = self._advance_or_validate_submission_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            evaluation_actor_id=evaluation_actor_id,
        )
        head = self.repository.head(plan.program_id)
        generation = self.repository.bind_campaign(
            plan.program_id,
            plan.generation_id,
            campaign.campaign_id,
            expected_revision=head.revision,
            actor_id=evaluation_actor_id,
            reason=(
                "Recovered and bound the exact partially created Generation Campaign."
            ),
            now=submitted_at,
        )
        return ProgramGenerationSubmission(
            generation=generation,
            campaign=campaign,
            reused=True,
        )

    def synchronize_authorization(
        self,
        *,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        actor_id: str,
    ) -> GenerationRecord:
        record = self.repository.get_generation(program_id, generation_id)
        campaign = self.campaign_governance.repository.get(campaign_id)
        if (
            record.campaign_id == campaign_id
            and record.status
            in {
                GenerationStatus.AUTHORIZED,
                *_TERMINAL_OR_RUNNING,
            }
            and campaign.state
            in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}
        ):
            self._validate_existing_campaign(campaign)
            return record
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
        record = self.repository.get_generation(program_id, generation_id)
        campaign = self.campaign_governance.repository.get(campaign_id)
        if (
            record.campaign_id == campaign_id
            and record.status in _TERMINAL_OR_RUNNING
            and campaign.state
            in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}
        ):
            self._validate_existing_campaign(campaign)
            return record
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
        record = self.repository.get_generation(program_id, generation_id)
        if record.plan is None or record.campaign_id is None:
            raise ValueError("Generation lacks plan or Campaign binding.")
        campaign = self.campaign_governance.repository.get(record.campaign_id)
        expected_outcome = self.feedback.generation_outcome(
            package,
            program_id=program_id,
            generation_id=generation_id,
            generation_index=record.generation_index,
            outcome_id=outcome_id,
            completed_at=completed_at,
            plan=record.plan,
        )
        if record.outcome is not None:
            if record.outcome != expected_outcome:
                raise ProgramConflictError(
                    "Generation completion retry differs from immutable outcome."
                )
            if campaign.state not in {
                CampaignState.AUTHORIZED,
                CampaignState.COMPLETED,
            }:
                raise ValueError(
                    "Completed Generation is bound to an invalid Campaign state."
                )
            self._validate_existing_campaign(campaign)
            if campaign.state == CampaignState.AUTHORIZED:
                self._validate_operation_actor(campaign, actor_id)
                campaign = self._recover_partial_campaign_completion(
                    campaign,
                    actor_id=actor_id,
                )
            if campaign.state != CampaignState.COMPLETED:
                raise RuntimeError(
                    "Exact Generation completion retry did not close its Campaign."
                )
            return record
        return super().complete_generation(
            package,
            program_id=program_id,
            generation_id=generation_id,
            outcome_id=outcome_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            completed_at=completed_at,
        )

    def _persisted_generation_evidence(
        self,
        plan: GenerationPlan,
    ) -> tuple[
        EvolutionProgramPolicy,
        ProgramLearningSignal,
        AttributionReceipt,
    ]:
        program = self.repository.get_program(plan.program_id)
        signal, attribution = self._persisted_plan_evidence(plan)
        failure = self.gate._attribution_failure(
            program.policy,
            signal,
            attribution,
        )
        if failure is not None:
            raise ValueError(failure)
        return program.policy, signal, attribution

    def _advance_or_validate_submission_campaign(
        self,
        campaign,
        *,
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
        evaluation_actor_id: str,
    ):
        self._validate_campaign_shell(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
        )
        if campaign.state in {
            CampaignState.OPEN,
            CampaignState.EVIDENCE_ACCUMULATING,
        }:
            if campaign.candidate_ref is not None or campaign.artifact_payload is not None:
                raise ValueError(
                    "Pre-candidate Generation Campaign already exposes candidate evidence."
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
                    "Recovered exact GenerationPlan evaluation after partial submission."
                ),
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
            "Generation Campaign cannot resume from its persisted lifecycle state."
        )

    def _validate_campaign_shell(
        self,
        campaign,
        *,
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        plan: GenerationPlan,
    ) -> None:
        expected_fingerprint = fingerprint_payload(
            self._fingerprint_source(policy, signal, attribution, plan)
        )
        if (
            campaign.campaign_type != CampaignType.EVOLUTION_GENERATION
            or campaign.target_key != self._target_key(plan)
            or campaign.fingerprint != expected_fingerprint
            or campaign.risk != CampaignRisk.HIGH
            or campaign.generated_by != plan.created_by
            or campaign.required_approvals != 2
            or campaign.metadata
            != self._metadata(policy, signal, attribution, plan)
        ):
            raise ValueError(
                "Persisted Generation Campaign shell differs from exact plan evidence."
            )

    def _recover_partial_campaign_completion(self, campaign, *, actor_id: str):
        try:
            recovered = self.campaign_governance.repository.transition(
                campaign.campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id=actor_id,
                reason=(
                    "Recovered exact completed generation after partial "
                    "cross-registry commit."
                ),
            )
        except StaleCampaignRevision:
            recovered = self.campaign_governance.repository.get(
                campaign.campaign_id
            )
        if recovered.state != CampaignState.COMPLETED:
            raise RuntimeError(
                "Concurrent Campaign completion recovery did not converge."
            )
        self._validate_existing_campaign(recovered)
        return recovered

    def _validate_existing_campaign(self, campaign) -> None:
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=False,
        )
        self._validate_approvals(campaign, signal, attribution, plan)


__all__ = [
    "HardenedEvolutionProgramGate",
    "RetryHardenedEvolutionProgramController",
]
