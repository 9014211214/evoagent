from __future__ import annotations

from datetime import datetime

from evoagent.campaigns import CampaignGovernanceService
from evoagent.program.constraints import validate_hardened_program_policy
from evoagent.program.controller_evidence_hardened import (
    RetryHardenedEvolutionProgramController as _EvidenceHardenedController,
)
from evoagent.program.gate_final_hardened import HardenedEvolutionProgramGate
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    ProgramLearningSignal,
)
from evoagent.program.repository import SQLiteEvolutionProgramRepository
from evoagent.release.package import ReleaseEvidencePackageManifest


class RetryHardenedEvolutionProgramController(
    _EvidenceHardenedController
):
    """Final public Controller with exact release-ingress binding."""

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
        validate_hardened_program_policy(policy)
        if created_at < package.created_at:
            raise ValueError(
                "Observed Program generation time precedes its verified "
                "release package."
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
        outcome = generations[0].outcome
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
        if created_at != attribution.created_at:
            raise ValueError(
                "Attribution storage time differs from its immutable receipt."
            )
        if created_at < signals[0].created_at:
            raise ValueError(
                "Attribution time precedes its persisted learning signal."
            )
        return super().store_attribution(
            program_id,
            attribution,
            actor_id=actor_id,
            created_at=created_at,
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
        if completed_at < package.created_at:
            raise ValueError(
                "Generation completion time precedes its verified release package."
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


__all__ = ["RetryHardenedEvolutionProgramController"]
