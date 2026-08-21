from __future__ import annotations

from datetime import datetime

from evoagent.program.controller_attestation_final import (
    RetryHardenedEvolutionProgramController as _AttestationController,
)
from evoagent.program.models import (
    AttributionReceipt,
    ProgramDecision,
    ProgramEventType,
    ProgramLearningSignal,
)


class RetryHardenedEvolutionProgramController(_AttestationController):
    """Final public Controller with seven-way review-role separation."""

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
        if signal is not None:
            ingestor = self._feedback_ingestor_id(program_id, signal.signal_id)
            if decided_by == ingestor:
                raise ValueError(
                    "Program decision/planning actor must differ from feedback ingestor."
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
        plan,
        *,
        evaluation_actor_id: str,
        submitted_at: datetime,
    ):
        ingestor = self._feedback_ingestor_id(
            plan.program_id,
            plan.source_signal_id,
        )
        if evaluation_actor_id == ingestor:
            raise ValueError(
                "Generation evaluator must differ from feedback ingestor."
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
        _, signal, _, _ = self._campaign_evidence(campaign)
        if actor_id == self._feedback_ingestor_id(
            signal.program_id,
            signal.signal_id,
        ):
            raise ValueError(
                "Generation approver must differ from feedback ingestor."
            )
        return super().approve_generation(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
        )

    def _validate_operation_actor(self, campaign, actor_id: str) -> None:
        super()._validate_operation_actor(campaign, actor_id)
        _, signal, _, _ = self._campaign_evidence(campaign)
        if actor_id == self._feedback_ingestor_id(
            signal.program_id,
            signal.signal_id,
        ):
            raise ValueError(
                "Generation execution actor must differ from feedback ingestor."
            )

    def _feedback_ingestor_id(self, program_id: str, signal_id: str) -> str:
        matches = tuple(
            item
            for item in self.repository.events()
            if item.program_id == program_id
            and item.event_type == ProgramEventType.SIGNAL_STORED
            and item.payload.get("signal_id") == signal_id
        )
        if len(matches) != 1:
            raise ValueError(
                "Program governance requires one exact feedback-ingestion audit event."
            )
        return matches[0].actor_id


__all__ = ["RetryHardenedEvolutionProgramController"]
