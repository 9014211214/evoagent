from __future__ import annotations

from datetime import datetime

from evoagent.program.controller_attestation_governed_final import (
    RetryHardenedEvolutionProgramController as _GovernedAttestationController,
)
from evoagent.program.execution_attestation import (
    ProgramExecutionCheckpoint,
    RunningGenerationAttestation,
)


class RetryHardenedEvolutionProgramController(
    _GovernedAttestationController
):
    """Final public Controller requiring external Program/Campaign audit anchors."""

    def attest_running_generation(
        self,
        *,
        program_id: str,
        generation_id: str,
        expected_program_checkpoint: ProgramExecutionCheckpoint,
        expected_campaign_checkpoint: ProgramExecutionCheckpoint,
        attested_by: str,
        attested_at: datetime,
        attestation_id: str | None = None,
    ) -> RunningGenerationAttestation:
        current_program = self.repository.checkpoint()
        campaign_repository = self.campaign_governance.repository
        current_campaign = campaign_repository.checkpoint()
        if (
            current_program.event_count
            != expected_program_checkpoint.event_count
            or current_program.head_hash
            != expected_program_checkpoint.head_hash
        ):
            raise ValueError(
                "Program audit tail differs from the external running-generation anchor."
            )
        if (
            current_campaign.event_count
            != expected_campaign_checkpoint.event_count
            or current_campaign.head_hash
            != expected_campaign_checkpoint.head_hash
        ):
            raise ValueError(
                "Generation Campaign audit tail differs from the external anchor."
            )
        if self.repository.verify_audit(expected_program_checkpoint) is not True:
            raise RuntimeError("External Program audit checkpoint did not verify.")
        if (
            campaign_repository.verify_audit(expected_campaign_checkpoint)
            is not True
        ):
            raise RuntimeError(
                "External Generation Campaign audit checkpoint did not verify."
            )
        attestation = super().attest_running_generation(
            program_id=program_id,
            generation_id=generation_id,
            attested_by=attested_by,
            attested_at=attested_at,
            attestation_id=attestation_id,
        )
        if (
            attestation.program_checkpoint != expected_program_checkpoint
            or attestation.campaign_checkpoint != expected_campaign_checkpoint
        ):
            raise RuntimeError(
                "Running Generation attestation did not preserve external anchors."
            )
        return attestation


__all__ = ["RetryHardenedEvolutionProgramController"]
