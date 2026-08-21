from __future__ import annotations

from evoagent.lab.automatic_local_tool import AutomaticLocalToolEvolutionLab
from evoagent.lab.closed_loop_supervisor import (
    ClosedLoopEvolutionLabResult,
    ClosedLoopEvolutionSupervisorLab as _BaseClosedLoopEvolutionSupervisorLab,
)
from evoagent.lab.model_candidate_admission import ModelCandidateAdmissionLab
from evoagent.supervisor import (
    SupervisorCaseRecord,
    SupervisorTrack,
    canonical_sha256,
)


class ClosedLoopEvolutionSupervisorLab(
    _BaseClosedLoopEvolutionSupervisorLab
):
    """Closed-loop lab with restart-stable child evidence verification.

    A child lab's ``resumed`` flag and phase list intentionally change on its
    second invocation. They are control-plane observations, not artifact
    identity. Resume verification therefore binds immutable registries,
    checkpoints, scores, identifiers, and package hashes rather than hashing
    the complete presentation result.
    """

    def _verify_child_resume(
        self,
        records: tuple[SupervisorCaseRecord, ...],
    ) -> None:
        skill = AutomaticLocalToolEvolutionLab(self.skill_root).run()
        model = ModelCandidateAdmissionLab(
            self.model_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        ).run()
        if not skill.resumed or not model.resumed:
            raise RuntimeError(
                "Child governed lifecycle did not report read-only resume."
            )

        skill_record = next(
            item for item in records if item.track == SupervisorTrack.SKILL
        )
        model_record = next(
            item for item in records if item.track == SupervisorTrack.MODEL
        )
        skill_outcome = skill_record.outcome
        model_outcome = model_record.outcome
        if skill_outcome is None or model_outcome is None:
            raise RuntimeError(
                "Completed child tracks are missing persisted outcomes."
            )

        stable_skill_hashes = {
            "skill_checkpoint": canonical_sha256(skill.skill_checkpoint),
            "campaign_checkpoint": canonical_sha256(
                skill.campaign_checkpoint
            ),
            "trace_checkpoint": canonical_sha256(skill.trace_checkpoint),
        }
        for name, digest in stable_skill_hashes.items():
            if skill_outcome.artifact_hashes.get(name) != digest:
                raise RuntimeError(
                    f"Resumed Skill {name} differs from Supervisor evidence."
                )
        if (
            skill_outcome.child_run_id != skill.run_id
            or skill_outcome.metrics.get("initial_score")
            != skill.summary.initial_score
            or skill_outcome.metrics.get("final_score")
            != skill.summary.final_score
            or skill_outcome.metrics.get("evolution_gain")
            != skill.summary.evolution_gain
            or skill_outcome.metrics.get("regression_count")
            != float(skill.regression_count)
            or not skill.restart_verified
        ):
            raise RuntimeError(
                "Resumed Skill lifecycle metrics or identity differ."
            )

        if (
            model_outcome.child_run_id != model.run_id
            or model_outcome.artifact_hashes.get(
                "training_intent_package"
            )
            != model.training_intent_package_hash
            or model_outcome.artifact_hashes.get(
                "model_admission_package"
            )
            != model.package_hash
            or model_outcome.metrics.get("held_out_base_score")
            != model.held_out_base_score
            or model_outcome.metrics.get("held_out_candidate_score")
            != model.held_out_candidate_score
            or model_outcome.metrics.get("held_out_improvement")
            != model.held_out_improvement
            or model_outcome.metrics.get("replay_candidate_score")
            != model.replay_candidate_score
            or model_outcome.metrics.get("retention_candidate_score")
            != model.retention_candidate_score
            or model_outcome.metrics.get("safety_candidate_score")
            != model.safety_candidate_score
            or model_outcome.metrics.get("regression_count")
            != float(model.regression_count)
            or model_outcome.metrics.get("forgetting_rate")
            != model.forgetting_rate
            or not model.restart_verified
        ):
            raise RuntimeError(
                "Resumed Model lifecycle package, metrics, or identity differ."
            )


__all__ = [
    "ClosedLoopEvolutionLabResult",
    "ClosedLoopEvolutionSupervisorLab",
]
