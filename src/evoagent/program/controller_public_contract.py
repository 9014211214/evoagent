from __future__ import annotations

from datetime import datetime

from evoagent.campaigns import ApprovalDecision, CampaignState
from evoagent.program.controller_evidence_hardened import (
    RetryHardenedEvolutionProgramController as _EvidenceHardenedController,
)
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationPlan,
    ProgramDecision,
    ProgramLearningSignal,
)
from evoagent.program.repository import ProgramConflictError
from evoagent.release.package import ReleaseEvidencePackageManifest


class RetryHardenedEvolutionProgramController(_EvidenceHardenedController):
    """Single public v2.0 Controller contract.

    The historical hardening layers remain internal implementation details.  This
    facade binds release ingress, one evidence set per generation, actor
    separation, exact retries and cross-registry recovery before downstream
    callers can mutate the Program lifecycle.
    """

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
        if created_at < package.created_at:
            raise ValueError(
                "Observed Program generation time precedes its verified release package."
            )
        return super().register_from_release(
            package,
            program_id=program_id,
            policy=policy,
            generation_id=generation_id,
            outcome_id=outcome_id,
            created_by=created_by,
            created_at=created_at,
        )

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
        generations = tuple(
            item
            for item in self.repository.list_generations(program_id)
            if item.generation_index == generation_index
        )
        if len(generations) != 1 or generations[0].outcome is None:
            raise ValueError(
                "Program feedback requires one completed observed generation."
            )
        generation = generations[0]
        outcome = generation.outcome
        if (
            outcome.release_package_hash != package.package_hash
            or outcome.release_plan_hash != package.plan.plan_hash
        ):
            raise ValueError(
                "Program feedback release package differs from the observed generation."
            )
        if created_at < max(outcome.completed_at, package.created_at):
            raise ValueError(
                "Program feedback time precedes its observed release evidence."
            )
        signal = self.feedback.extract(
            package,
            program_id=program_id,
            generation_index=generation_index,
            signal_id=signal_id,
            created_at=created_at,
        )
        existing = tuple(
            item
            for item in self.repository.list_signals(program_id)
            if item.generation_index == generation_index
        )
        if existing and (len(existing) != 1 or existing[0] != signal):
            raise ProgramConflictError(
                "Program generation already has a different immutable learning signal."
            )
        if not existing and any(
            item.generation_index == generation_index
            for item in self.repository.list_decisions(program_id)
        ):
            raise ProgramConflictError(
                "Program generation cannot add feedback after its decision."
            )
        return super().store_feedback(
            package,
            program_id=program_id,
            generation_index=generation_index,
            signal_id=signal_id,
            actor_id=actor_id,
            created_at=created_at,
        )

    def store_attribution(
        self,
        program_id: str,
        attribution: AttributionReceipt,
        *,
        actor_id: str,
        created_at: datetime,
    ) -> tuple[AttributionReceipt, bool]:
        signals = tuple(
            item
            for item in self.repository.list_signals(program_id)
            if item.signal_id == attribution.signal_id
            and item.signal_hash == attribution.signal_hash
        )
        if len(signals) != 1:
            raise ValueError(
                "Attribution does not bind one exact persisted Program signal."
            )
        signal = signals[0]
        if created_at != attribution.created_at:
            raise ValueError(
                "Attribution storage time differs from its immutable receipt."
            )
        if created_at < signal.created_at:
            raise ValueError(
                "Attribution time precedes its persisted learning signal."
            )
        existing = tuple(
            item
            for item in self.repository.list_attributions(program_id)
            if item.signal_id == signal.signal_id
            and item.signal_hash == signal.signal_hash
        )
        if existing and (len(existing) != 1 or existing[0] != attribution):
            raise ProgramConflictError(
                "Program learning signal already has a different Attribution receipt."
            )
        if not existing and any(
            item.generation_index == signal.generation_index
            for item in self.repository.list_decisions(program_id)
        ):
            raise ProgramConflictError(
                "Program generation cannot add Attribution after its decision."
            )
        return super().store_attribution(
            program_id,
            attribution,
            actor_id=actor_id,
            created_at=created_at,
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
        if (signal is None) != (attribution is None):
            raise ValueError(
                "Program decision evidence requires both signal and attribution or neither."
            )
        generation = self.repository.get_generation(program_id, generation_id)
        if signal is not None and attribution is not None:
            signals, attributions = self._generation_evidence_set(
                program_id,
                generation.generation_index,
            )
            if (
                len(signals) != 1
                or len(attributions) != 1
                or signals[0] != signal
                or attributions[0] != attribution
            ):
                raise ValueError(
                    "Program decision requires one exact persisted generation evidence set."
                )
        return super().decide(
            program_id=program_id,
            generation_id=generation_id,
            decision_id=decision_id,
            decided_by=decided_by,
            decided_at=decided_at,
            signal=signal,
            attribution=attribution,
        )

    def submit_generation(
        self,
        plan: GenerationPlan,
        *,
        evaluation_actor_id: str,
        submitted_at: datetime,
    ):
        signals, attributions = self._generation_evidence_set(
            plan.program_id,
            plan.generation_index - 1,
        )
        if (
            len(signals) != 1
            or len(attributions) != 1
            or signals[0].signal_id != plan.source_signal_id
            or signals[0].signal_hash != plan.source_signal_hash
            or attributions[0].receipt_id != plan.attribution_receipt_id
            or attributions[0].receipt_hash != plan.attribution_receipt_hash
        ):
            raise ValueError(
                "GenerationPlan does not bind the unique persisted parent evidence set."
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
    ):
        campaign = self.campaign_governance.repository.get(campaign_id)
        existing = tuple(
            item
            for item in self.campaign_governance.repository.approvals(campaign_id)
            if item.actor_id == actor_id
        )
        if existing:
            if (
                len(existing) != 1
                or existing[0].decision != ApprovalDecision.APPROVE
                or existing[0].reason != reason
            ):
                raise ValueError(
                    "Campaign approval retry conflicts with the immutable decision."
                )
            policy, signal, attribution, plan = self._campaign_evidence(campaign)
            self._validate_campaign(
                campaign,
                policy=policy,
                signal=signal,
                attribution=attribution,
                plan=plan,
                require_authorized=(
                    campaign.state in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}
                ),
            )
            evaluation_actors = self._generation_evaluation_actors(campaign_id)
            if actor_id in {
                signal.evidence_producer_id,
                attribution.attributor_id,
                plan.created_by,
                *evaluation_actors,
            }:
                raise ValueError(
                    "Campaign approval retry violates independent role separation."
                )
            approvals = tuple(
                self.campaign_governance.repository.approvals(campaign_id)
            )
            if campaign.state == CampaignState.APPROVAL_PENDING:
                if len(approvals) != 1:
                    raise ValueError(
                        "Partially approved Campaign has inconsistent approval cardinality."
                    )
            else:
                self._validate_approvals(campaign, signal, attribution, plan)
            return campaign
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
    ):
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
    ):
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
    ):
        generation = self.repository.get_generation(program_id, generation_id)
        if generation.campaign_id is None:
            raise ValueError("Generation lacks Campaign binding.")
        campaign = self.campaign_governance.repository.get(generation.campaign_id)
        self._validate_operation_actor(campaign, actor_id)
        if generation.outcome is None and completed_at < max(
            generation.updated_at,
            campaign.updated_at,
        ):
            raise ValueError(
                "Generation completion time precedes authorization or execution evidence."
            )
        return super().complete_generation(
            package,
            program_id=program_id,
            generation_id=generation_id,
            outcome_id=outcome_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            completed_at=completed_at,
        )

    def _generation_evidence_set(
        self,
        program_id: str,
        generation_index: int,
    ) -> tuple[tuple[ProgramLearningSignal, ...], tuple[AttributionReceipt, ...]]:
        signals = tuple(
            item
            for item in self.repository.list_signals(program_id)
            if item.generation_index == generation_index
        )
        signal_keys = {(item.signal_id, item.signal_hash) for item in signals}
        attributions = tuple(
            item
            for item in self.repository.list_attributions(program_id)
            if (item.signal_id, item.signal_hash) in signal_keys
        )
        return signals, attributions


__all__ = ["RetryHardenedEvolutionProgramController"]
