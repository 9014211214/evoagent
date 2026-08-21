from __future__ import annotations

from datetime import datetime, timezone

from evoagent.integrated.package_hardened import (
    IntegratedEvolutionPackageManager,
)
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.local_policy import LocalPolicyPromotionPackageManager
from evoagent.skills import SkillStateBundleManager

from .integrated_multitrack import (
    IntegratedMultiTrackEvolutionLab as _BaseIntegratedMultiTrackEvolutionLab,
    IntegratedMultiTrackLabResult,
)
from .program_local_rl_acceptance_final import (
    ProgramLocalRLAcceptedEvidenceManager,
)


class IntegratedMultiTrackEvolutionLab(
    _BaseIntegratedMultiTrackEvolutionLab
):
    """Use one recursive final package verifier for build, load, and restart."""

    def run(self) -> IntegratedMultiTrackLabResult:
        package_manager = IntegratedEvolutionPackageManager()
        if self.package_path.exists():
            package = package_manager.load_file(self.package_path)
            self._verify_persistent_state(package)
            self._verify_child_resume(package)
            self._verify_persistent_state(package)
            return self._result(
                package,
                resumed=True,
                optimizer_invoked=False,
            )

        context = self._context()
        self._ensure_run_and_initial_snapshot(context)
        self._ensure_initial_evaluation_cases_and_decision(context)
        self._ensure_skill_round(context)
        self._ensure_skill_snapshot_evaluation_and_decision(context)
        optimizer_invoked = self._ensure_local_policy_round(context)
        self._ensure_policy_snapshot_evaluation_and_stop(context)
        self._ensure_integrated_completion(context)
        package = self._build_package(context)
        package_manager.export_file(package, self.package_path)
        self._verify_persistent_state(package)
        return self._result(
            package,
            resumed=False,
            optimizer_invoked=optimizer_invoked,
        )

    def _build_package(self, context):
        skill_child = context["skill_executor"].lab.run()
        accepted = ProgramLocalRLAcceptedEvidenceManager().load_file(
            context["policy_executor"].acceptance_lab.bundle_path
        )
        promotion = LocalPolicyPromotionPackageManager().load_file(
            context["policy_executor"].promotion_root
            / "local-policy-promotion-package.json"
        )
        skill_state = SkillStateBundleManager().build(
            context["skill_registry"]
        )
        integrated_repository = context["integrated_repository"]
        composite_registry = context["composite_registry"]
        evaluation_repository = context["evaluation_repository"]
        return IntegratedEvolutionPackageManager().build(
            package_id="integrated-evolution-package:v2.3",
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            run=integrated_repository.get_run(self.RUN_ID),
            cases=integrated_repository.list_cases(self.RUN_ID),
            track_results=integrated_repository.list_results(self.RUN_ID),
            integrated_events=integrated_repository.events(self.RUN_ID),
            integrated_checkpoint=integrated_repository.checkpoint(
                self.RUN_ID
            ),
            composite_snapshots=composite_registry.list_snapshots(
                self.LINEAGE_ID
            ),
            composite_head=composite_registry.head(self.LINEAGE_ID),
            composite_events=composite_registry.events(self.LINEAGE_ID),
            composite_checkpoint=composite_registry.checkpoint(
                self.LINEAGE_ID
            ),
            evaluation_policy=evaluation_repository.policy(
                self.LINEAGE_ID
            ),
            evaluations=evaluation_repository.list_evaluations(
                self.LINEAGE_ID
            ),
            stop_decisions=evaluation_repository.list_decisions(
                self.LINEAGE_ID
            ),
            evaluation_events=evaluation_repository.events(
                self.LINEAGE_ID
            ),
            evaluation_checkpoint=evaluation_repository.checkpoint(
                self.LINEAGE_ID
            ),
            skill_state=skill_state,
            skill_child_result=skill_child,
            accepted_program_local_rl=accepted,
            local_policy_promotion=promotion,
            created_at=datetime.now(timezone.utc),
        )

    def _verify_persistent_state(self, package) -> None:
        context = self._context()
        integrated_repository = context["integrated_repository"]
        composite_registry = context["composite_registry"]
        evaluation_repository = context["evaluation_repository"]
        integrated_repository.verify_state(self.RUN_ID)
        composite_registry.verify_state(self.LINEAGE_ID)
        evaluation_repository.verify_state(self.LINEAGE_ID)
        if (
            integrated_repository.get_run(self.RUN_ID) != package.run
            or integrated_repository.list_cases(self.RUN_ID) != package.cases
            or integrated_repository.list_results(self.RUN_ID)
            != package.track_results
            or integrated_repository.events(self.RUN_ID)
            != package.integrated_events
            or integrated_repository.checkpoint(self.RUN_ID)
            != package.integrated_checkpoint
            or composite_registry.list_snapshots(self.LINEAGE_ID)
            != package.composite_snapshots
            or composite_registry.head(self.LINEAGE_ID)
            != package.composite_head
            or composite_registry.events(self.LINEAGE_ID)
            != package.composite_events
            or composite_registry.checkpoint(self.LINEAGE_ID)
            != package.composite_checkpoint
            or evaluation_repository.policy(self.LINEAGE_ID)
            != package.evaluation_policy
            or evaluation_repository.list_evaluations(self.LINEAGE_ID)
            != package.evaluations
            or evaluation_repository.list_decisions(self.LINEAGE_ID)
            != package.stop_decisions
            or evaluation_repository.events(self.LINEAGE_ID)
            != package.evaluation_events
            or evaluation_repository.checkpoint(self.LINEAGE_ID)
            != package.evaluation_checkpoint
        ):
            raise RuntimeError(
                "Persistent integrated state differs from immutable package."
            )
        current_skill = SkillStateBundleManager().build(
            context["skill_registry"]
        )
        if (
            current_skill.records != package.skill_state.records
            or current_skill.active_versions
            != package.skill_state.active_versions
            or current_skill.active_revisions
            != package.skill_state.active_revisions
            or current_skill.events != package.skill_state.events
        ):
            raise RuntimeError(
                "Persistent Skill state differs from integrated package."
            )
        context["policy_view"].verify_actual_parent()
        IntegratedEvolutionPackageManager.verify(package)


__all__ = [
    "IntegratedMultiTrackEvolutionLab",
    "IntegratedMultiTrackLabResult",
]
