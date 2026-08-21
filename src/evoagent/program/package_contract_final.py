from __future__ import annotations

from evoagent.program.models import ProgramAction
from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManifest,
    ProgramControlEvidence,
)
from evoagent.program.package_provenance_hardened import (
    AuditHardenedEvolutionProgramPackageManager as _ProvenanceHardenedManager,
)


class AuditHardenedEvolutionProgramPackageManager(
    _ProvenanceHardenedManager
):
    """Final public Package Manager with one evidence set per generation."""

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        super().verify(manifest)
        self._verify_main_evidence_cardinality(manifest)
        self._verify_control_evidence_cardinality(manifest.budget_control)
        self._verify_control_evidence_cardinality(manifest.ambiguous_control)
        return True

    @staticmethod
    def _verify_main_evidence_cardinality(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if len(manifest.generations) != 2 or len(manifest.decisions) != 2:
            raise EvolutionProgramPackageError(
                "Program package requires exactly two controlled generations and decisions."
            )
        g0, g1 = manifest.generations
        d0, _ = manifest.decisions
        if g1.plan is None:
            raise EvolutionProgramPackageError(
                "Program package lacks the exact successor GenerationPlan."
            )
        if (
            manifest.signal.program_id != g0.program_id
            or manifest.signal.generation_index != g0.generation_index
            or manifest.attribution.signal_id != manifest.signal.signal_id
            or manifest.attribution.signal_hash != manifest.signal.signal_hash
            or g1.plan.source_signal_id != manifest.signal.signal_id
            or g1.plan.source_signal_hash != manifest.signal.signal_hash
            or g1.plan.attribution_receipt_id
            != manifest.attribution.receipt_id
            or g1.plan.attribution_receipt_hash
            != manifest.attribution.receipt_hash
            or d0.generation_id != g0.generation_id
        ):
            raise EvolutionProgramPackageError(
                "Program package main lifecycle does not bind one exact evidence set."
            )

    @staticmethod
    def _verify_control_evidence_cardinality(
        control: ProgramControlEvidence,
    ) -> None:
        if (
            len(control.generations) != 1
            or len(control.signals) != 1
            or len(control.decisions) != 1
        ):
            raise EvolutionProgramPackageError(
                "Program control requires one generation, signal and decision."
            )
        generation = control.generations[0]
        signal = control.signals[0]
        decision = control.decisions[0]
        expected_attribution_count = {
            ProgramAction.STOP_BUDGET: 0,
            ProgramAction.ESCALATE: 1,
        }.get(decision.action)
        if (
            signal.program_id != generation.program_id
            or signal.generation_index != generation.generation_index
            or decision.generation_id != generation.generation_id
            or expected_attribution_count is None
            or len(control.attributions) != expected_attribution_count
        ):
            raise EvolutionProgramPackageError(
                "Program control does not bind the exact action-specific evidence set."
            )
        if control.attributions:
            attribution = control.attributions[0]
            if (
                attribution.signal_id != signal.signal_id
                or attribution.signal_hash != signal.signal_hash
            ):
                raise EvolutionProgramPackageError(
                    "Program control Attribution differs from its unique signal."
                )


__all__ = ["AuditHardenedEvolutionProgramPackageManager"]
