from __future__ import annotations

from pathlib import Path

from evoagent.campaigns import SQLiteCampaignRepository
from evoagent.local_policy import (
    LocalPolicyPromotionPackageManager,
    LocalPolicyVersionStatus,
    SQLiteLocalPolicyRegistry,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.skills import SQLiteSkillRegistry, SkillVersionStatus

from .models import (
    IntegratedCaseRecord,
    IntegratedCaseStatus,
    IntegratedTrack,
    IntegratedTrackResult,
    build_integrated_track_result,
)


class IntegratedExecutorEvidenceError(RuntimeError):
    pass


def _claimed_cases(
    records: tuple[IntegratedCaseRecord, ...],
    *,
    track: IntegratedTrack,
    executor_id: str,
) -> tuple[IntegratedCaseRecord, ...]:
    normalized = tuple(
        sorted(records, key=lambda item: item.case.case_id)
    )
    if not normalized or any(
        item.status != IntegratedCaseStatus.CLAIMED
        or item.case.track != track
        or item.claimed_by != executor_id
        for item in normalized
    ):
        raise IntegratedExecutorEvidenceError(
            "Integrated executor received another claimed evidence batch."
        )
    if track == IntegratedTrack.SKILL and len(normalized) != 1:
        raise IntegratedExecutorEvidenceError(
            "Governed Skill executor requires one exact attributed case."
        )
    if track == IntegratedTrack.LOCAL_POLICY and len(normalized) < 2:
        raise IntegratedExecutorEvidenceError(
            "Governed local-policy executor requires multiple distinct cases."
        )
    return normalized


def _claim_started_at(cases: tuple[IntegratedCaseRecord, ...]):
    return max(item.updated_at for item in cases)


class GovernedSkillEvolutionExecutor:
    executor_id = "integrated-skill-executor"
    track = IntegratedTrack.SKILL

    def __init__(self, root: str | Path):
        from evoagent.lab.automatic_local_tool_final import (
            AutomaticLocalToolEvolutionLab,
        )

        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lab = AutomaticLocalToolEvolutionLab(self.root)

    def execute(
        self,
        run_id: str,
        records: tuple[IntegratedCaseRecord, ...],
    ) -> IntegratedTrackResult:
        cases = _claimed_cases(
            records,
            track=self.track,
            executor_id=self.executor_id,
        )
        started_at = _claim_started_at(cases)
        child = self.lab.run()
        skills = SQLiteSkillRegistry(self.lab.skill_database)
        campaigns = SQLiteCampaignRepository(self.lab.campaign_database)
        active = skills.active(child.skill_id)
        if (
            active.status != SkillVersionStatus.ACTIVE
            or active.spec.version != child.active_version
            or active.evaluation is None
            or not active.evaluation.promote
            or active.evaluation.regression_count != 0
        ):
            raise IntegratedExecutorEvidenceError(
                "Governed Skill executor lacks an active zero-regression promotion."
            )
        skill_events = tuple(skills.events(child.skill_id))
        campaign_events = tuple(campaigns.audit_events())
        if not skill_events or not campaign_events:
            raise IntegratedExecutorEvidenceError(
                "Governed Skill executor lacks persisted audit evidence."
            )
        completed_at = max(
            started_at,
            skill_events[-1].created_at,
            campaign_events[-1].created_at,
        )
        evaluation_hash = canonical_sha256(
            active.evaluation.model_dump(mode="json")
        )
        evidence_payload = {
            "child_run_id": child.run_id,
            "skill_id": child.skill_id,
            "active_version": child.active_version,
            "content_hash": active.content_hash,
            "evaluation_hash": evaluation_hash,
            "skill_checkpoint": child.skill_checkpoint,
            "campaign_checkpoint": child.campaign_checkpoint,
            "trace_checkpoint": child.trace_checkpoint,
            "initial_score": child.summary.initial_score,
            "final_score": child.summary.final_score,
            "regression_count": child.regression_count,
        }
        evidence_hash = canonical_sha256(evidence_payload)
        return build_integrated_track_result(
            result_id=f"integrated-result:skill:{run_id}",
            run_id=run_id,
            track=self.track,
            case_ids=tuple(item.case.case_id for item in cases),
            source_decision_hashes=tuple(
                sorted(
                    {
                        *(item.case.attribution_hash for item in cases),
                        evaluation_hash,
                    }
                )
            ),
            source_package_hashes=(evidence_hash,),
            component_ref=(
                f"skill:{active.spec.skill_id}:{active.spec.version}"
            ),
            component_hash=active.content_hash,
            executor_id=self.executor_id,
            started_at=started_at,
            completed_at=completed_at,
            metrics={
                "initial_score": child.summary.initial_score,
                "final_score": child.summary.final_score,
                "evolution_gain": child.summary.evolution_gain,
                "regression_count": float(child.regression_count),
            },
            skill_promoted=True,
        )


class GovernedLocalPolicyEvolutionExecutor:
    executor_id = "integrated-local-policy-executor"
    track = IntegratedTrack.LOCAL_POLICY

    def __init__(
        self,
        root: str | Path,
        *,
        source_commit: str = "0" * 40,
        source_repository: str = (
            "https://github.com/9014211214/evoagent"
        ),
    ):
        from evoagent.lab.program_local_rl_acceptance_final import (
            ProgramLocalRLAcceptanceLab,
        )

        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository
        self.acceptance_lab = ProgramLocalRLAcceptanceLab(
            self.root / "accepted-program-local-rl",
            source_commit=source_commit,
            source_repository=source_repository,
        )

    @property
    def promotion_root(self) -> Path:
        return self.root / "accepted-local-policy-promotion"

    def execute(
        self,
        run_id: str,
        records: tuple[IntegratedCaseRecord, ...],
    ) -> IntegratedTrackResult:
        from evoagent.lab.local_policy_promotion_final import (
            AcceptedLocalPolicyPromotionLab,
        )
        from evoagent.lab.program_local_rl_acceptance_final import (
            ProgramLocalRLAcceptedEvidenceManager,
        )

        cases = _claimed_cases(
            records,
            track=self.track,
            executor_id=self.executor_id,
        )
        started_at = _claim_started_at(cases)
        accepted = self.acceptance_lab.run()
        bundle = ProgramLocalRLAcceptedEvidenceManager().load_file(
            accepted.bundle_path
        )
        promotion = AcceptedLocalPolicyPromotionLab(
            self.promotion_root,
            accepted_program_package=bundle.fully_attested_package,
            trusted_anchors=bundle.trusted_anchors,
            acceptance_receipt=bundle.acceptance_receipt,
            source_commit=self.source_commit,
            perform_rollback=False,
        )
        promoted = promotion.run()
        package = LocalPolicyPromotionPackageManager().load_file(
            promoted.package_path
        )
        registry = SQLiteLocalPolicyRegistry(promotion.registry_path)
        active = registry.active(promoted.family_id)
        candidate = package.candidate_record
        if (
            active.policy_id != promotion.candidate_policy_id
            or active.status != LocalPolicyVersionStatus.ACTIVE
            or candidate.policy_id != active.policy_id
            or candidate.parent_policy_id != promotion.initial_policy_id
            or candidate.promotion_decision is None
            or not candidate.promotion_decision.promote
            or package.rollback_campaign is not None
            or package.final_head.revision != 1
        ):
            raise IntegratedExecutorEvidenceError(
                "Governed local-policy executor lacks exact Promotion-only evidence."
            )
        base = (
            bundle.fully_attested_package.runtime_attested_package
            .schema_attested_package.attested_package.base_package
        )
        result = base.result
        local_events = tuple(registry.events())
        if not local_events:
            raise IntegratedExecutorEvidenceError(
                "Governed local-policy executor lacks Registry audit evidence."
            )
        completed_at = max(
            started_at,
            package.created_at,
            local_events[-1].created_at,
        )
        return build_integrated_track_result(
            result_id=f"integrated-result:local-policy:{run_id}",
            run_id=run_id,
            track=self.track,
            case_ids=tuple(item.case.case_id for item in cases),
            source_decision_hashes=tuple(
                sorted(
                    {
                        *(item.case.attribution_hash for item in cases),
                        candidate.promotion_decision.decision_hash,
                    }
                )
            ),
            source_package_hashes=tuple(
                sorted({bundle.bundle_hash, package.package_hash})
            ),
            component_ref=(
                f"local-policy:{active.family_id}:{active.policy_id}"
            ),
            component_hash=result.selected_checkpoint_hash,
            executor_id=self.executor_id,
            started_at=started_at,
            completed_at=completed_at,
            metrics={
                "heldout_reward_delta": result.heldout_reward_delta,
                "heldout_success_delta": result.heldout_success_delta,
                "unsafe_action_count": float(result.unsafe_action_count),
                "regression_count": float(result.regression_count),
                "optimizer_iterations": float(result.usage.iterations),
                "optimizer_rollouts": float(result.usage.rollouts),
            },
            local_policy_optimized=True,
            local_policy_promoted=True,
            local_policy_activated=True,
            rollback_ready=True,
        )


__all__ = [
    "GovernedLocalPolicyEvolutionExecutor",
    "GovernedSkillEvolutionExecutor",
    "IntegratedExecutorEvidenceError",
]
