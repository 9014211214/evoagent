from __future__ import annotations

from datetime import datetime

from evoagent.campaigns import ApprovalDecision, CampaignState
from evoagent.program.controller_final_hardened import (
    RetryHardenedEvolutionProgramController as _IngressHardenedController,
)
from evoagent.program.models import (
    AttributionReceipt,
    GenerationPlan,
    ProgramLearningSignal,
)
from evoagent.program.repository import ProgramConflictError
from evoagent.release.package import ReleaseEvidencePackageManifest


class RetryHardenedEvolutionProgramController(
    _IngressHardenedController
):
    """Final public Program Controller with one immutable evidence set per generation."""

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
        expected = self.feedback.extract(
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
        if existing:
            if len(existing) != 1 or existing[0] != expected:
                raise ProgramConflictError(
                    "Program generation already has different immutable feedback."
                )
            return super().store_feedback(
                package,
                program_id=program_id,
                generation_index=generation_index,
                signal_id=signal_id,
                actor_id=actor_id,
                created_at=created_at,
            )
        if any(
            item.generation_index == generation_index
            for item in self.repository.list_decisions(program_id)
        ):
            raise ProgramConflictError(
                "Program generation cannot acquire feedback after its decision."
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
        existing = tuple(
            item
            for item in self.repository.list_attributions(program_id)
            if item.signal_id == signal.signal_id
            and item.signal_hash == signal.signal_hash
        )
        if existing:
            if len(existing) != 1 or existing[0] != attribution:
                raise ProgramConflictError(
                    "Program signal already has different immutable attribution."
                )
            return super().store_attribution(
                program_id,
                attribution,
                actor_id=actor_id,
                created_at=created_at,
            )
        if any(
            item.generation_index == signal.generation_index
            for item in self.repository.list_decisions(program_id)
        ):
            raise ProgramConflictError(
                "Program generation cannot acquire attribution after its decision."
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
    ):
        if (signal is None) != (attribution is None):
            raise ValueError(
                "Program decision evidence requires both signal and attribution or neither."
            )
        if signal is not None and attribution is not None:
            generation = self.repository.get_generation(
                program_id,
                generation_id,
            )
            exact_signal, exact_attribution = self._exact_generation_evidence(
                program_id,
                generation.generation_index,
            )
            if signal != exact_signal or attribution != exact_attribution:
                raise ValueError(
                    "Program decision differs from the generation's unique persisted evidence."
                )
            signal = exact_signal
            attribution = exact_attribution
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
        signal, attribution = self._exact_generation_evidence(
            plan.program_id,
            plan.generation_index - 1,
        )
        if (
            plan.source_signal_id != signal.signal_id
            or plan.source_signal_hash != signal.signal_hash
            or plan.attribution_receipt_id != attribution.receipt_id
            or plan.attribution_receipt_hash != attribution.receipt_hash
        ):
            raise ValueError(
                "GenerationPlan differs from its parent's unique persisted evidence."
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
                    campaign.state
                    in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}
                ),
            )
            if campaign.state in {
                CampaignState.AUTHORIZED,
                CampaignState.COMPLETED,
            }:
                self._validate_approvals(
                    campaign,
                    signal,
                    attribution,
                    plan,
                )
            elif campaign.state != CampaignState.APPROVAL_PENDING:
                raise ValueError(
                    "Exact approval retry found an invalid Campaign state."
                )
            return campaign
        return super().approve_generation(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
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
        if generation.outcome is not None:
            if generation.campaign_id is None:
                raise ValueError("Completed Generation lacks Campaign binding.")
            campaign = self.campaign_governance.repository.get(
                generation.campaign_id
            )
            self._validate_existing_campaign(campaign)
        return super().complete_generation(
            package,
            program_id=program_id,
            generation_id=generation_id,
            outcome_id=outcome_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            completed_at=completed_at,
        )

    def _exact_generation_evidence(
        self,
        program_id: str,
        generation_index: int,
    ) -> tuple[ProgramLearningSignal, AttributionReceipt]:
        signals = tuple(
            item
            for item in self.repository.list_signals(program_id)
            if item.generation_index == generation_index
        )
        if len(signals) != 1:
            raise ValueError(
                "Program generation requires exactly one persisted learning signal."
            )
        signal = signals[0]
        attributions = tuple(
            item
            for item in self.repository.list_attributions(program_id)
            if item.signal_id == signal.signal_id
            and item.signal_hash == signal.signal_hash
        )
        if len(attributions) != 1:
            raise ValueError(
                "Program generation requires exactly one signal-bound attribution."
            )
        return signal, attributions[0]


__all__ = ["RetryHardenedEvolutionProgramController"]
