from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApproval,
    CampaignGovernanceService,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
    fingerprint_payload,
)
from evoagent.champion import ChampionDecisionPackageManager
from evoagent.champion.package_models import ChampionDecisionPackageManifest
from evoagent.release.models import (
    ReleaseDecisionAction,
    ReleaseEvidenceBatch,
    ReleaseHead,
    ReleasePlan,
    ReleaseStageAssessment,
    ReleaseStageDecision,
    ReleaseState,
)
from evoagent.release.policy import ReleaseStageGate
from evoagent.release.repository import SQLiteReleaseRegistry


class ReleasePlanSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: ReleasePlan
    campaign: CampaignRecord
    head: ReleaseHead
    reused: bool


class ReleaseStageEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch: ReleaseEvidenceBatch
    assessment: ReleaseStageAssessment
    decision: ReleaseStageDecision
    reused: bool


class ReleaseRollbackSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: ReleaseStageDecision
    campaign: CampaignRecord
    head: ReleaseHead
    reused: bool


class ReleaseLifecycleService:
    """Keep evidence, approval, local stage state, and external deployment separate."""

    def __init__(
        self,
        *,
        registry: SQLiteReleaseRegistry,
        campaign_governance: CampaignGovernanceService,
        gate: ReleaseStageGate | None = None,
    ):
        self.registry = registry
        self.campaign_governance = campaign_governance
        self.gate = gate or ReleaseStageGate()

    def submit_plan(
        self,
        champion_package: ChampionDecisionPackageManifest,
        plan: ReleasePlan,
    ) -> ReleasePlanSubmission:
        ChampionDecisionPackageManager().verify(champion_package)
        self._validate_plan_binding(champion_package, plan)
        _, plan_reused = self.registry.register_plan(plan)
        reservation = self.campaign_governance.reserve(
            campaign_type=CampaignType.CHAMPION_RELEASE,
            target_key=self._release_target(plan),
            fingerprint_source=self._release_fingerprint(champion_package, plan),
            risk=CampaignRisk.HIGH,
            generated_by=plan.created_by,
            metadata=self._release_metadata(champion_package, plan),
        )
        campaign = reservation.campaign
        if reservation.reused:
            self._validate_release_campaign(
                campaign,
                champion_package=champion_package,
                plan=plan,
                require_authorized=False,
            )
        else:
            campaign = self.campaign_governance.attach_candidate(
                campaign,
                candidate_ref=f"release-plan:{plan.plan_id}",
                artifact_payload=self._release_payload(champion_package, plan),
                actor_id="release-lifecycle",
            )
        head = self.registry.head(plan.plan_id)
        if head.release_campaign_id is None:
            head = self.registry.bind_release_campaign(
                plan.plan_id,
                campaign.campaign_id,
                expected_revision=head.revision,
                actor_id="release-lifecycle",
            )
        elif head.release_campaign_id != campaign.campaign_id:
            raise ValueError("Release Registry is bound to another Campaign.")
        campaign = self.campaign_governance.repository.get(campaign.campaign_id)
        if campaign.state == CampaignState.CANDIDATE_READY:
            campaign = self.campaign_governance.submit_evaluation(
                campaign.campaign_id,
                passed=True,
                expected_revision=campaign.revision,
                actor_id="release-plan-validator",
                reason="Champion package and immutable release plan binding passed.",
            )
        if campaign.state not in {
            CampaignState.APPROVAL_PENDING,
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            raise ValueError("Champion release Campaign did not reach approval state.")
        return ReleasePlanSubmission(
            plan=plan,
            campaign=campaign,
            head=head,
            reused=plan_reused or reservation.reused,
        )

    def approve_release(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ) -> CampaignRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        plan = self._plan_from_release_campaign(campaign)
        if actor_id == plan.created_by:
            raise ValueError("Release plan creator cannot approve the release Campaign.")
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            expected_revision=expected_revision,
        )

    def synchronize_release_authorization(
        self,
        *,
        plan_id: str,
        campaign_id: str,
        actor_id: str,
    ) -> ReleaseHead:
        campaign = self.campaign_governance.repository.get(campaign_id)
        champion_package = self._champion_package_from_campaign(campaign)
        plan = self.registry.get_plan(plan_id)
        self._validate_release_campaign(
            campaign,
            champion_package=champion_package,
            plan=plan,
            require_authorized=True,
        )
        self._validate_approvals(campaign, forbidden={plan.created_by})
        head = self.registry.head(plan_id)
        before = head
        head = self.registry.mark_authorized(
            plan_id,
            campaign_id,
            expected_revision=head.revision,
            actor_id=actor_id,
        )
        if (
            head.primary_snapshot_id != before.primary_snapshot_id
            or head.candidate_allocation_percent != 0.0
        ):
            raise RuntimeError("Release authorization changed local routing state.")
        return head

    def start_shadow(
        self,
        *,
        plan_id: str,
        campaign_id: str,
        expected_revision: int,
        actor_id: str,
    ) -> ReleaseHead:
        campaign = self.campaign_governance.repository.get(campaign_id)
        plan = self.registry.get_plan(plan_id)
        champion_package = self._champion_package_from_campaign(campaign)
        self._validate_release_campaign(
            campaign,
            champion_package=champion_package,
            plan=plan,
            require_authorized=True,
        )
        self._validate_approvals(campaign, forbidden={plan.created_by})
        head = self.registry.start_shadow(
            plan_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state == CampaignState.AUTHORIZED:
            campaign = self.campaign_governance.repository.transition(
                campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id=actor_id,
                reason=(
                    "Authorized shadow stage recorded in the local control plane; "
                    "no external rollout was executed."
                ),
            )
        if campaign.state != CampaignState.COMPLETED:
            raise RuntimeError("Champion release Campaign did not complete locally.")
        return head

    def evaluate_stage(
        self,
        plan_id: str,
        batch: ReleaseEvidenceBatch,
        *,
        assessment_id: str,
        decision_id: str,
        decision_actor_id: str,
        decided_at: datetime,
    ) -> ReleaseStageEvaluation:
        plan = self.registry.get_plan(plan_id)
        stored_batch, batch_reused = self.registry.store_batch(batch)
        assessment = self.gate.assess(
            plan,
            stored_batch,
            assessment_id=assessment_id,
        )
        stored_assessment, assessment_reused = self.registry.store_assessment(
            assessment
        )
        decision = self.gate.decide(
            plan,
            stored_assessment,
            decision_id=decision_id,
            decision_actor_id=decision_actor_id,
            decided_at=decided_at,
        )
        stored_decision, decision_reused = self.registry.store_decision(decision)
        return ReleaseStageEvaluation(
            batch=stored_batch,
            assessment=stored_assessment,
            decision=stored_decision,
            reused=batch_reused or assessment_reused or decision_reused,
        )

    def advance(
        self,
        decision_id: str,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> ReleaseHead:
        decision = self.registry.get_decision(decision_id)
        return self.registry.advance(
            decision,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )

    def record_hold(
        self,
        decision_id: str,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> ReleaseHead:
        decision = self.registry.get_decision(decision_id)
        return self.registry.record_hold(
            decision,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )

    def mark_ready(
        self,
        decision_id: str,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> ReleaseHead:
        decision = self.registry.get_decision(decision_id)
        head = self.registry.mark_ready(
            decision,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )
        if head.primary_snapshot_id != head.incumbent_snapshot_id:
            raise RuntimeError("Release readiness changed the primary snapshot.")
        return head

    def submit_rollback(
        self,
        champion_package: ChampionDecisionPackageManifest,
        decision_id: str,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> ReleaseRollbackSubmission:
        decision = self.registry.get_decision(decision_id)
        if decision.action != ReleaseDecisionAction.ROLLBACK:
            raise ValueError("Only a rollback decision may create a rollback Campaign.")
        plan = self.registry.get_plan(decision.plan_id)
        self._validate_plan_binding(champion_package, plan)
        assessment = self.registry.get_assessment_by_hash(decision.assessment_hash)
        batch = self.registry.get_batch(assessment.batch_id)
        head = self.registry.recommend_rollback(
            decision,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )
        reservation = self.campaign_governance.reserve(
            campaign_type=CampaignType.CHAMPION_ROLLBACK,
            target_key=self._rollback_target(plan, decision),
            fingerprint_source=self._rollback_fingerprint(
                champion_package,
                plan,
                batch,
                assessment,
                decision,
            ),
            risk=CampaignRisk.HIGH,
            generated_by=decision.decision_actor_id,
            metadata=self._rollback_metadata(plan, decision, head),
        )
        campaign = reservation.campaign
        if reservation.reused:
            self._validate_rollback_campaign(
                campaign,
                champion_package=champion_package,
                plan=plan,
                batch=batch,
                assessment=assessment,
                decision=decision,
                require_authorized=False,
            )
        else:
            campaign = self.campaign_governance.attach_candidate(
                campaign,
                candidate_ref=f"release-rollback:{plan.plan_id}:{decision.stage_id}",
                artifact_payload=self._rollback_payload(
                    champion_package,
                    plan,
                    batch,
                    assessment,
                    decision,
                ),
                actor_id="release-lifecycle",
            )
        if head.rollback_campaign_id is None:
            head = self.registry.bind_rollback_campaign(
                plan.plan_id,
                campaign.campaign_id,
                expected_revision=head.revision,
                actor_id="release-lifecycle",
            )
        elif head.rollback_campaign_id != campaign.campaign_id:
            raise ValueError("Release rollback is bound to another Campaign.")
        campaign = self.campaign_governance.repository.get(campaign.campaign_id)
        if campaign.state == CampaignState.CANDIDATE_READY:
            campaign = self.campaign_governance.submit_evaluation(
                campaign.campaign_id,
                passed=True,
                expected_revision=campaign.revision,
                actor_id="release-rollback-validator",
                reason=(
                    "Hard release gates require rollback of the exact local "
                    "candidate allocation."
                ),
            )
        return ReleaseRollbackSubmission(
            decision=decision,
            campaign=campaign,
            head=head,
            reused=reservation.reused,
        )

    def approve_rollback(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ) -> CampaignRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        decision = self._decision_from_rollback_campaign(campaign)
        forbidden = {decision.decision_actor_id, decision.evidence_producer_id}
        if actor_id in forbidden:
            raise ValueError(
                "Release decision actor or evidence producer cannot approve rollback."
            )
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            expected_revision=expected_revision,
        )

    def execute_rollback(
        self,
        *,
        plan_id: str,
        decision_id: str,
        campaign_id: str,
        expected_revision: int,
        actor_id: str,
    ) -> ReleaseHead:
        campaign = self.campaign_governance.repository.get(campaign_id)
        decision = self.registry.get_decision(decision_id)
        plan = self.registry.get_plan(plan_id)
        assessment = self.registry.get_assessment_by_hash(decision.assessment_hash)
        batch = self.registry.get_batch(assessment.batch_id)
        champion_package = self._champion_package_from_campaign(campaign)
        self._validate_rollback_campaign(
            campaign,
            champion_package=champion_package,
            plan=plan,
            batch=batch,
            assessment=assessment,
            decision=decision,
            require_authorized=True,
        )
        self._validate_approvals(
            campaign,
            forbidden={
                decision.decision_actor_id,
                decision.evidence_producer_id,
            },
        )
        head = self.registry.rollback(
            decision,
            campaign_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state == CampaignState.AUTHORIZED:
            campaign = self.campaign_governance.repository.transition(
                campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id=actor_id,
                reason=(
                    "Authorized local control-plane rollback restored zero "
                    "Challenger allocation; no external rollback was executed."
                ),
            )
        if campaign.state != CampaignState.COMPLETED:
            raise RuntimeError("Champion rollback Campaign did not complete locally.")
        if (
            head.state != ReleaseState.ROLLED_BACK
            or head.primary_snapshot_id != plan.incumbent_snapshot_id
            or head.candidate_allocation_percent != 0.0
        ):
            raise RuntimeError("Explicit release rollback did not restore the incumbent.")
        return head

    def _validate_approvals(
        self,
        campaign: CampaignRecord,
        *,
        forbidden: set[str],
    ) -> tuple[CampaignApproval, ...]:
        approvals = tuple(
            self.campaign_governance.repository.approvals(campaign.campaign_id)
        )
        approving = tuple(
            item for item in approvals if item.decision == ApprovalDecision.APPROVE
        )
        actors = tuple(item.actor_id for item in approving)
        if (
            campaign.required_approvals != 2
            or len(approvals) != 2
            or len(approving) != 2
            or len(set(actors)) != 2
            or set(actors) & forbidden
        ):
            raise ValueError(
                "Release operation requires exactly two independent approving actors."
            )
        return approvals

    @staticmethod
    def _validate_plan_binding(
        champion_package: ChampionDecisionPackageManifest,
        plan: ReleasePlan,
    ) -> None:
        ChampionDecisionPackageManager().verify(champion_package)
        decision = champion_package.decision
        if (
            plan.champion_package_hash != champion_package.package_hash
            or plan.family_id != champion_package.active_family_id
            or plan.incumbent_snapshot_id != decision.baseline_snapshot_id
            or plan.challenger_snapshot_id != champion_package.active_snapshot_id
            or plan.challenger_snapshot_id != decision.selected_snapshot_id
            or plan.champion_decision_hash != decision.decision_hash
            or plan.source_commit != champion_package.source_commit
        ):
            raise ValueError("Release plan differs from the exact Champion package.")

    def _validate_release_campaign(
        self,
        campaign: CampaignRecord,
        *,
        champion_package: ChampionDecisionPackageManifest,
        plan: ReleasePlan,
        require_authorized: bool,
    ) -> None:
        if (
            campaign.campaign_type != CampaignType.CHAMPION_RELEASE
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != plan.created_by
            or campaign.target_key != self._release_target(plan)
            or campaign.fingerprint
            != fingerprint_payload(self._release_fingerprint(champion_package, plan))
            or campaign.candidate_ref != f"release-plan:{plan.plan_id}"
            or campaign.metadata != self._release_metadata(champion_package, plan)
            or campaign.artifact_payload
            != self._release_payload(champion_package, plan)
        ):
            raise ValueError("Champion release Campaign differs from exact plan evidence.")
        if require_authorized and campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Champion release Campaign is not AUTHORIZED.")

    def _validate_rollback_campaign(
        self,
        campaign: CampaignRecord,
        *,
        champion_package: ChampionDecisionPackageManifest,
        plan: ReleasePlan,
        batch: ReleaseEvidenceBatch,
        assessment: ReleaseStageAssessment,
        decision: ReleaseStageDecision,
        require_authorized: bool,
    ) -> None:
        if (
            campaign.campaign_type != CampaignType.CHAMPION_ROLLBACK
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != decision.decision_actor_id
            or campaign.target_key != self._rollback_target(plan, decision)
            or campaign.fingerprint
            != fingerprint_payload(
                self._rollback_fingerprint(
                    champion_package,
                    plan,
                    batch,
                    assessment,
                    decision,
                )
            )
            or campaign.candidate_ref
            != f"release-rollback:{plan.plan_id}:{decision.stage_id}"
            or campaign.metadata
            != self._rollback_metadata(plan, decision, self.registry.head(plan.plan_id))
            or campaign.artifact_payload
            != self._rollback_payload(
                champion_package,
                plan,
                batch,
                assessment,
                decision,
            )
        ):
            raise ValueError("Champion rollback Campaign differs from exact drift evidence.")
        if require_authorized and campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Champion rollback Campaign is not AUTHORIZED.")

    @staticmethod
    def _release_target(plan: ReleasePlan) -> str:
        return (
            f"champion-release:{plan.family_id}:"
            f"{plan.incumbent_snapshot_id}->{plan.challenger_snapshot_id}"
        )

    @staticmethod
    def _release_fingerprint(champion_package, plan) -> dict:
        return {
            "champion_package_hash": champion_package.package_hash,
            "plan_hash": plan.plan_hash,
            "incumbent_snapshot_id": plan.incumbent_snapshot_id,
            "challenger_snapshot_id": plan.challenger_snapshot_id,
            "runtime_config_sha256": plan.runtime_config_sha256,
            "tool_contract_sha256": plan.tool_contract_sha256,
        }

    @staticmethod
    def _release_metadata(champion_package, plan) -> dict:
        return {
            "plan_id": plan.plan_id,
            "family_id": plan.family_id,
            "champion_package_hash": champion_package.package_hash,
            "production_deployment_performed": False,
        }

    @staticmethod
    def _release_payload(champion_package, plan) -> dict:
        return {
            "kind": "champion_release_plan",
            "champion_package": champion_package.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "external_rollout_performed": False,
            "production_deployment_performed": False,
        }

    @staticmethod
    def _rollback_target(plan: ReleasePlan, decision: ReleaseStageDecision) -> str:
        return f"champion-rollback:{plan.family_id}:{plan.plan_id}:{decision.stage_id}"

    @staticmethod
    def _rollback_fingerprint(
        champion_package,
        plan,
        batch,
        assessment,
        decision,
    ) -> dict:
        return {
            "champion_package_hash": champion_package.package_hash,
            "plan_hash": plan.plan_hash,
            "batch_hash": batch.evidence_hash,
            "assessment_hash": assessment.assessment_hash,
            "decision_hash": decision.decision_hash,
            "stage_id": decision.stage_id,
            "candidate_traffic_percent": assessment.candidate_traffic_percent,
        }

    @staticmethod
    def _rollback_metadata(plan, decision, head) -> dict:
        return {
            "plan_id": plan.plan_id,
            "family_id": plan.family_id,
            "stage_id": decision.stage_id,
            "decision_hash": decision.decision_hash,
            "candidate_allocation_percent": head.candidate_allocation_percent,
            "production_deployment_performed": False,
        }

    @staticmethod
    def _rollback_payload(
        champion_package,
        plan,
        batch,
        assessment,
        decision,
    ) -> dict:
        return {
            "kind": "champion_release_rollback",
            "champion_package": champion_package.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json"),
            "assessment": assessment.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "external_rollback_performed": False,
            "production_deployment_performed": False,
        }

    @staticmethod
    def _plan_from_release_campaign(campaign: CampaignRecord) -> ReleasePlan:
        payload = campaign.artifact_payload or {}
        return ReleasePlan.model_validate(payload.get("plan"))

    @staticmethod
    def _champion_package_from_campaign(
        campaign: CampaignRecord,
    ) -> ChampionDecisionPackageManifest:
        payload = campaign.artifact_payload or {}
        return ChampionDecisionPackageManifest.model_validate(
            payload.get("champion_package")
        )

    @staticmethod
    def _decision_from_rollback_campaign(
        campaign: CampaignRecord,
    ) -> ReleaseStageDecision:
        payload = campaign.artifact_payload or {}
        return ReleaseStageDecision.model_validate(payload.get("decision"))


__all__ = [
    "ReleaseLifecycleService",
    "ReleasePlanSubmission",
    "ReleaseRollbackSubmission",
    "ReleaseStageEvaluation",
]