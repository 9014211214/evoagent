from __future__ import annotations

from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManifest,
)
from evoagent.program.package_provenance_hardened import (
    AuditHardenedEvolutionProgramPackageManager as _ProvenanceManager,
)


_NORMAL_COMPLETION_REASON = (
    "Exact authorized generation completed with verified child evidence."
)
_RECOVERY_COMPLETION_REASON = (
    "Recovered exact completed generation after partial cross-registry commit."
)


class AuditHardenedEvolutionProgramPackageManager(_ProvenanceManager):
    """Final verifier that preserves legitimate cross-registry recovery evidence."""

    @classmethod
    def _verify_causal_chronology(
        cls,
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if len(manifest.campaign_events) != 7 or len(manifest.program_events) != 12:
            return super()._verify_causal_chronology(manifest)
        campaign_completion = manifest.campaign_events[6]
        if campaign_completion.payload.get("reason") != _RECOVERY_COMPLETION_REASON:
            return super()._verify_causal_chronology(manifest)

        generation_completion = manifest.program_events[9]
        recovery_time = campaign_completion.created_at
        generation_time = generation_completion.created_at
        if recovery_time < generation_time:
            raise EvolutionProgramPackageError(
                "Recovered Campaign completion predates Generation completion."
            )
        if recovery_time > manifest.decisions[1].decided_at:
            raise EvolutionProgramPackageError(
                "Final Program decision predates recovered Campaign completion."
            )
        if recovery_time > manifest.created_at:
            raise EvolutionProgramPackageError(
                "Recovered Campaign completion occurs after package creation."
            )
        if manifest.generation_campaign.updated_at != recovery_time:
            raise EvolutionProgramPackageError(
                "Recovered Campaign record time differs from its audit event."
            )
        campaign_times = tuple(
            item.created_at for item in manifest.campaign_events
        )
        if campaign_times != tuple(sorted(campaign_times)):
            raise EvolutionProgramPackageError(
                "Recovered Generation Campaign chronology is not monotonic."
            )

        normalized_completion = campaign_completion.model_copy(
            update={"created_at": generation_time}
        )
        normalized_campaign = manifest.generation_campaign.model_copy(
            update={"updated_at": generation_time}
        )
        normalized_manifest = manifest.model_copy(
            update={
                "campaign_events": (
                    *manifest.campaign_events[:6],
                    normalized_completion,
                ),
                "generation_campaign": normalized_campaign,
            }
        )
        super()._verify_causal_chronology(normalized_manifest)

    @staticmethod
    def _verify_evaluator_role_separation(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if len(manifest.campaign_events) != 7 or len(manifest.program_events) != 12:
            raise EvolutionProgramPackageError(
                "Program role verification requires complete audit lifecycles."
            )
        evaluation_actors = {
            manifest.campaign_events[index].actor_id for index in (1, 2, 3)
        }
        if len(evaluation_actors) != 1:
            raise EvolutionProgramPackageError(
                "Controlled Generation Campaign requires one exact independent evaluator."
            )
        evaluator = next(iter(evaluation_actors))
        if manifest.program_events[6].actor_id != evaluator:
            raise EvolutionProgramPackageError(
                "Generation Campaign binding actor differs from evaluator."
            )

        approval_actors = {
            item.actor_id for item in manifest.generation_approvals
        }
        authorization_actor = manifest.program_events[7].actor_id
        start_actor = manifest.program_events[8].actor_id
        generation_completion = manifest.program_events[9]
        campaign_completion = manifest.campaign_events[6]
        campaign_completion_actor = campaign_completion.actor_id
        execution_actors = {
            authorization_actor,
            start_actor,
            generation_completion.actor_id,
            campaign_completion_actor,
        }
        if evaluation_actors & approval_actors:
            raise EvolutionProgramPackageError(
                "Generation evaluator also approved its own Campaign."
            )
        if evaluation_actors & execution_actors:
            raise EvolutionProgramPackageError(
                "Generation evaluator also authorized or executed the generation."
            )
        if approval_actors & execution_actors:
            raise EvolutionProgramPackageError(
                "Generation approver also authorized or executed the generation."
            )
        plan = manifest.generations[1].plan
        if plan is None:
            raise EvolutionProgramPackageError(
                "Program role verification requires the successor plan."
            )
        privileged = {
            manifest.signal.evidence_producer_id,
            manifest.attribution.attributor_id,
            plan.created_by,
            manifest.decisions[0].decided_by,
        }
        if evaluation_actors & privileged:
            raise EvolutionProgramPackageError(
                "Generation evaluator overlaps evidence, attribution or planning authority."
            )
        if approval_actors & privileged:
            raise EvolutionProgramPackageError(
                "Generation approval overlaps evidence, attribution or planning authority."
            )
        if execution_actors & privileged:
            raise EvolutionProgramPackageError(
                "Generation execution overlaps evidence, attribution or planning authority."
            )
        completion_reason = campaign_completion.payload.get("reason")
        if completion_reason == _NORMAL_COMPLETION_REASON:
            if campaign_completion_actor != generation_completion.actor_id:
                raise EvolutionProgramPackageError(
                    "Normal Campaign completion actor differs from Generation completion."
                )
            if campaign_completion.created_at != generation_completion.created_at:
                raise EvolutionProgramPackageError(
                    "Normal Campaign completion time differs from Generation completion."
                )
        elif completion_reason == _RECOVERY_COMPLETION_REASON:
            if campaign_completion.created_at < generation_completion.created_at:
                raise EvolutionProgramPackageError(
                    "Recovered Campaign completion predates Generation completion."
                )
        else:
            raise EvolutionProgramPackageError(
                "Generation Campaign completion reason is not governed."
            )
        program_bind_time = manifest.program_events[6].created_at
        campaign_evaluation_time = manifest.campaign_events[3].created_at
        first_approval_time = min(
            item.created_at for item in manifest.generation_approvals
        )
        last_approval_time = max(
            item.created_at for item in manifest.generation_approvals
        )
        if campaign_evaluation_time > program_bind_time:
            raise EvolutionProgramPackageError(
                "Generation Campaign was bound before independent evaluation completed."
            )
        if program_bind_time > first_approval_time:
            raise EvolutionProgramPackageError(
                "Generation approval predates Campaign binding."
            )
        if last_approval_time > manifest.program_events[7].created_at:
            raise EvolutionProgramPackageError(
                "Program authorization predates the final independent approval."
            )
        if manifest.program_events[7].created_at > manifest.program_events[8].created_at:
            raise EvolutionProgramPackageError(
                "Generation start predates Program authorization."
            )
        if manifest.program_events[8].created_at > generation_completion.created_at:
            raise EvolutionProgramPackageError(
                "Generation completion predates explicit start."
            )


__all__ = ["AuditHardenedEvolutionProgramPackageManager"]
