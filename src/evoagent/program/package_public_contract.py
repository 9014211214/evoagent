from __future__ import annotations

from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManifest,
)
from evoagent.program.package_provenance_hardened import (
    AuditHardenedEvolutionProgramPackageManager as _ProvenanceHardenedManager,
)


class AuditHardenedEvolutionProgramPackageManager(
    _ProvenanceHardenedManager
):
    """Single exported v2.0 package-verification contract."""

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        super().verify(manifest)
        self._verify_complete_role_partition(manifest)
        self._verify_decision_evidence_partition(manifest)
        return True

    @staticmethod
    def _verify_complete_role_partition(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if (
            len(manifest.generations) != 2
            or len(manifest.decisions) != 2
            or len(manifest.program_events) != 12
            or len(manifest.campaign_events) != 7
            or len(manifest.generation_approvals) != 2
        ):
            raise EvolutionProgramPackageError(
                "Program role partition requires complete immutable lifecycles."
            )
        plan = manifest.generations[1].plan
        if plan is None:
            raise EvolutionProgramPackageError(
                "Program role partition requires the successor GenerationPlan."
            )
        decision_actor = manifest.decisions[0].decided_by
        if plan.created_by != decision_actor:
            raise EvolutionProgramPackageError(
                "CONTINUE decision and successor planning actors differ."
            )
        evaluator_actors = {
            manifest.campaign_events[index].actor_id for index in (1, 2, 3)
        }
        if len(evaluator_actors) != 1:
            raise EvolutionProgramPackageError(
                "Generation Campaign requires one exact independent evaluator."
            )
        evaluator = next(iter(evaluator_actors))
        approval_actors = {
            item.actor_id for item in manifest.generation_approvals
        }
        execution_actors = {
            manifest.program_events[index].actor_id for index in (7, 8, 9)
        }
        governed_origins = {
            manifest.signal.evidence_producer_id,
            manifest.attribution.attributor_id,
            decision_actor,
            evaluator,
            *approval_actors,
        }
        if len(approval_actors) != 2:
            raise EvolutionProgramPackageError(
                "Generation Campaign requires two distinct approval actors."
            )
        if len(governed_origins) != 5:
            raise EvolutionProgramPackageError(
                "Evidence, attribution, planning, evaluation and approval roles overlap."
            )
        if execution_actors & governed_origins:
            raise EvolutionProgramPackageError(
                "Generation authorization or execution role overlaps governed review roles."
            )
        if manifest.program_events[6].actor_id != evaluator:
            raise EvolutionProgramPackageError(
                "Generation Campaign binding actor differs from its evaluator."
            )
        if (
            manifest.campaign_events[6].actor_id
            != manifest.program_events[9].actor_id
        ):
            raise EvolutionProgramPackageError(
                "Generation and Campaign completion actors differ."
            )

    @staticmethod
    def _verify_decision_evidence_partition(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        g0, g1 = manifest.generations
        d0, d1 = manifest.decisions
        plan = g1.plan
        if g0.outcome is None or g1.outcome is None or plan is None:
            raise EvolutionProgramPackageError(
                "Program decision evidence partition is incomplete."
            )
        if (
            manifest.signal.program_id != g0.program_id
            or manifest.signal.generation_index != g0.generation_index
            or manifest.attribution.signal_id != manifest.signal.signal_id
            or manifest.attribution.signal_hash != manifest.signal.signal_hash
            or plan.source_signal_id != manifest.signal.signal_id
            or plan.source_signal_hash != manifest.signal.signal_hash
            or plan.attribution_receipt_id != manifest.attribution.receipt_id
            or plan.attribution_receipt_hash != manifest.attribution.receipt_hash
            or d0.generation_id != g0.generation_id
            or d0.source_outcome_hash != g0.outcome.outcome_hash
            or d1.generation_id != g1.generation_id
            or d1.source_outcome_hash != g1.outcome.outcome_hash
        ):
            raise EvolutionProgramPackageError(
                "Program decisions and GenerationPlan do not share one evidence lineage."
            )


__all__ = ["AuditHardenedEvolutionProgramPackageManager"]
