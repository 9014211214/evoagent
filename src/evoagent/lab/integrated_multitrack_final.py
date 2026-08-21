from __future__ import annotations

from datetime import datetime, timezone

from evoagent.integrated.models import IntegratedTrack
from evoagent.integrated.service import IntegratedDispatchAction
from evoagent.lab.automatic_local_tool_final import (
    AutomaticLocalToolEvolutionLab,
)
from evoagent.lab.local_policy_promotion_final import (
    AcceptedLocalPolicyPromotionLab,
)
from evoagent.lab.program_local_rl_acceptance_final import (
    ProgramLocalRLAcceptedEvidenceManager,
)

from .integrated_multitrack_hardened import (
    IntegratedMultiTrackEvolutionLab as _HardenedIntegratedMultiTrackEvolutionLab,
    IntegratedMultiTrackLabResult,
)


class IntegratedMultiTrackEvolutionLab(
    _HardenedIntegratedMultiTrackEvolutionLab
):
    """Final Lab with exact per-invocation optimizer and resume evidence."""

    def _ensure_local_policy_round(self, context) -> bool:
        repository = context["integrated_repository"]
        run = repository.get_run(self.RUN_ID)
        optimizer_invoked = False
        if run.policy_execution_count == 0:
            acceptance_path = context[
                "policy_executor"
            ].acceptance_lab.bundle_path
            optimizer_invoked = not acceptance_path.exists()
            plan = context["supervisor"].plan_next(
                self.RUN_ID,
                plan_id="integrated-dispatch:real-local-policy",
                planned_at=datetime.now(timezone.utc),
            )
            if plan.action not in {
                IntegratedDispatchAction.CLAIM_LOCAL_POLICY,
                IntegratedDispatchAction.RESUME_LOCAL_POLICY,
            }:
                raise RuntimeError(
                    f"Integrated policy round received {plan.action.value}."
                )
            claimed = context["supervisor"].claim_plan(
                plan,
                now=datetime.now(timezone.utc),
            )
            result = context["policy_executor"].execute(
                self.RUN_ID,
                claimed,
            )
            current = repository.get_run(self.RUN_ID)
            context["supervisor"].record_result(
                result,
                expected_run_revision=current.revision,
                now=datetime.now(timezone.utc),
            )
        results = tuple(
            item
            for item in repository.list_results(self.RUN_ID)
            if item.track == IntegratedTrack.LOCAL_POLICY
        )
        if len(results) != 1:
            raise RuntimeError(
                "Integrated local-policy round lacks one exact result."
            )
        context["policy_view"].verify_actual_parent()
        active = context["composite_registry"].active(self.LINEAGE_ID)
        if active.manifest.round_index == 1:
            result = results[0]
            manifest = context["snapshot_service"].build_child_from_components(
                lineage_id=self.LINEAGE_ID,
                snapshot_id=self.A2,
                expected_component="local_policy",
                source_case_ids=result.case_ids,
                source_decision_hashes=result.source_decision_hashes,
                source_package_hashes=result.source_package_hashes,
                created_by=self.A2_BUILDER,
                created_at=datetime.now(timezone.utc),
            )
            context["snapshot_service"].commit(
                manifest,
                expected_active_revision=1,
                actor_id=self.A2_COMMITTER,
                now=datetime.now(timezone.utc),
            )
        return optimizer_invoked

    def _verify_child_resume(self, package) -> None:
        skill = AutomaticLocalToolEvolutionLab(self.skill_root).run()
        accepted = ProgramLocalRLAcceptedEvidenceManager().load_file(
            self.policy_root
            / "accepted-program-local-rl"
            / "program-local-rl-accepted-evidence.json"
        )
        context = self._context()
        policy_acceptance = context[
            "policy_executor"
        ].acceptance_lab.run()
        promotion = AcceptedLocalPolicyPromotionLab(
            self.policy_root / "accepted-local-policy-promotion",
            accepted_program_package=accepted.fully_attested_package,
            trusted_anchors=accepted.trusted_anchors,
            acceptance_receipt=accepted.acceptance_receipt,
            source_commit=self.source_commit,
            perform_rollback=False,
        ).run()
        if (
            not skill.resumed
            or not policy_acceptance.resumed
            or policy_acceptance.optimizer_invoked
            or not promotion.resumed
            or skill.active_version
            != package.skill_child_result.active_version
            or policy_acceptance.bundle_hash
            != package.accepted_program_local_rl.bundle_hash
            or promotion.package_hash
            != package.local_policy_promotion.package_hash
        ):
            raise RuntimeError(
                "Integrated child lifecycle did not resume read-only."
            )


__all__ = [
    "IntegratedMultiTrackEvolutionLab",
    "IntegratedMultiTrackLabResult",
]
