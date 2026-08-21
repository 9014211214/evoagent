from __future__ import annotations

from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManifest,
)
from evoagent.program.package_public_contract_final import (
    AuditHardenedEvolutionProgramPackageManager as _PublicPackageManager,
)


class AuditHardenedEvolutionProgramPackageManager(_PublicPackageManager):
    """Final public Package Manager with seven independent review origins."""

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
        feedback_ingestor = manifest.program_events[2].actor_id
        execution_actors = {
            manifest.program_events[index].actor_id for index in (7, 8, 9)
        }
        governed_origins = {
            manifest.signal.evidence_producer_id,
            feedback_ingestor,
            manifest.attribution.attributor_id,
            decision_actor,
            evaluator,
            *approval_actors,
        }
        if len(approval_actors) != 2 or len(governed_origins) != 7:
            raise EvolutionProgramPackageError(
                "Evidence production, feedback ingestion, attribution, planning, evaluation and approval roles overlap."
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


__all__ = ["AuditHardenedEvolutionProgramPackageManager"]
