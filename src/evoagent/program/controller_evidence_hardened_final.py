from __future__ import annotations

from datetime import datetime

from evoagent.campaigns import CampaignState
from evoagent.program.controller_final_hardened import (
    RetryHardenedEvolutionProgramController as _IngressHardenedController,
)
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationPlan,
    ProgramDecision,
    ProgramEventType,
    ProgramLearningSignal,
)
from evoagent.program.repository import ProgramConflictError
from evoagent.release.package import ReleaseEvidencePackageManifest


class RetryHardenedEvolutionProgramController(_IngressHardenedController):
    """Combine release-ingress controls with one evidence set per generation."""

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
        if signal is not None and attribution is not None:
            self._persisted_decision_evidence(
                program_id=program_id,
                generation_index=generation.generation_index,
                signal=signal,
                attribution=attribution,
            )
        elif signal is not None:
            persisted_signal = self._persisted_signal_evidence(
                program_id=program_id,
                generation_index=generation.generation_index,
                signal=signal,
            )
            generation_signals = tuple(
                item
                for item in self.repository.list_signals(program_id)
                if item.generation_index == generation.generation_index
            )
            if generation_signals != (persisted_signal,):
                raise ValueError(
                    "Evidence-based Program decision requires one immutable generation signal."
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
        existing = tuple(
            item
            for item in self.repository.list_signals(program_id)
            if item.signal_id == signal_id
        )
        if existing:
            return super().store_feedback(
                package,
                program_id=program_id,
                generation_index=generation_index,
                signal_id=signal_id,
                actor_id=actor_id,
                created_at=created_at,
            )
        if any(
            decision.generation_index == generation_index
            for decision in self.repository.list_decisions(program_id)
        ):
            raise ValueError(
                "Program generation cannot accept new feedback after its decision."
            )
        if any(
            signal.generation_index == generation_index
            for signal in self.repository.list_signals(program_id)
        ):
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

    def store_attribution(
        self,
        program_id: str,
        attribution: AttributionReceipt,
        *,
        actor_id: str,
        created_at: datetime,
    ) -> tuple[AttributionReceipt, bool]:
        existing = tuple(
            item
            for item in self.repository.list_attributions(program_id)
            if item.receipt_id == attribution.receipt_id
        )
        if existing:
            return super().store_attribution(
                program_id,
                attribution,
                actor_id=actor_id,
                created_at=created_at,
            )
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
        signal_events = tuple(
            event
            for event in self.repository.events()
            if event.event_type == ProgramEventType.SIGNAL_STORED
            and event.payload.get("signal_id") == signal.signal_id
            and event.payload.get("signal_hash") == signal.signal_hash
        )
        if len(signal_events) != 1:
            raise ValueError(
                "Attribution requires one exact feedback-ingestion audit event."
            )
        if actor_id == signal_events[0].actor_id:
            raise ValueError(
                "Causal attributor must differ from feedback ingestor."
            )
        if any(
            decision.generation_index == signal.generation_index
            for decision in self.repository.list_decisions(program_id)
        ):
            raise ValueError(
                "Program generation cannot accept new attribution after its decision."
            )
        if any(
            item.signal_id == signal.signal_id
            and item.signal_hash == signal.signal_hash
            for item in self.repository.list_attributions(program_id)
        ):
            raise ProgramConflictError(
                "Program generation already has different immutable attribution."
            )
        return super().store_attribution(
            program_id,
            attribution,
            actor_id=actor_id,
            created_at=created_at,
        )

    def _persisted_decision_evidence(
        self,
        *,
        program_id: str,
        generation_index: int,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
    ) -> tuple[ProgramLearningSignal, AttributionReceipt]:
        signal = self._persisted_signal_evidence(
            program_id=program_id,
            generation_index=generation_index,
            signal=signal,
        )
        attribution = self._persisted_attribution_evidence(
            program_id=program_id,
            signal=signal,
            attribution=attribution,
        )
        generation_signals = tuple(
            item
            for item in self.repository.list_signals(program_id)
            if item.generation_index == generation_index
        )
        bound_attributions = tuple(
            item
            for item in self.repository.list_attributions(program_id)
            if item.signal_id == signal.signal_id
            and item.signal_hash == signal.signal_hash
        )
        if generation_signals != (signal,):
            raise ValueError(
                "Evidence-based Program decision requires one immutable generation signal."
            )
        if bound_attributions != (attribution,):
            raise ValueError(
                "Evidence-based Program decision requires one immutable attribution."
            )
        return signal, attribution

    def _persisted_generation_evidence(
        self,
        plan: GenerationPlan,
    ) -> tuple[
        EvolutionProgramPolicy,
        ProgramLearningSignal,
        AttributionReceipt,
    ]:
        policy, signal, attribution = super()._persisted_generation_evidence(plan)
        generation_signals = tuple(
            item
            for item in self.repository.list_signals(plan.program_id)
            if item.generation_index == signal.generation_index
        )
        bound_attributions = tuple(
            item
            for item in self.repository.list_attributions(plan.program_id)
            if item.signal_id == signal.signal_id
            and item.signal_hash == signal.signal_hash
        )
        if generation_signals != (signal,) or bound_attributions != (attribution,):
            raise ValueError(
                "GenerationPlan does not bind the unique decision evidence set."
            )
        return policy, signal, attribution

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
        if campaign.state in {
            CampaignState.OPEN,
            CampaignState.EVIDENCE_ACCUMULATING,
            CampaignState.CANDIDATE_READY,
            CampaignState.EVALUATION_PENDING,
        }:
            persisted_evaluators = self._generation_evaluation_actors(
                campaign.campaign_id
            )
            if len(persisted_evaluators) > 1:
                raise ValueError(
                    "Generation Campaign already contains conflicting evaluator identities."
                )
            if (
                persisted_evaluators
                and persisted_evaluators != {evaluation_actor_id}
            ):
                raise ValueError(
                    "Generation Campaign recovery must preserve its evaluator identity."
                )
        return super()._advance_or_validate_submission_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            evaluation_actor_id=evaluation_actor_id,
            phase_at=phase_at,
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
        if (
            record.outcome is not None
            and record.plan is not None
            and record.campaign_id is not None
        ):
            if completed_at < package.created_at:
                raise ValueError(
                    "Generation completion time precedes its verified release package."
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
            if record.outcome != expected_outcome:
                raise ProgramConflictError(
                    "Generation completion retry differs from immutable outcome."
                )
            campaign = self.campaign_governance.repository.get(record.campaign_id)
            if campaign.state == CampaignState.COMPLETED:
                self._validate_existing_campaign(campaign)
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


__all__ = ["RetryHardenedEvolutionProgramController"]
