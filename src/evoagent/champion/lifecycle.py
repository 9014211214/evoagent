from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from evoagent.benchmark_evidence.models import BenchmarkRunEvidence
from evoagent.benchmark_evidence.package import (
    BenchmarkComparisonPackageManifest,
)
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
from evoagent.champion.models import (
    ChampionDecisionAction,
    ChampionPromotionPolicy,
    ChampionRoundAssessment,
    ChampionSelectionDecision,
    ChampionSnapshotRecord,
    ChampionVersionStatus,
)
from evoagent.champion.policy import ChampionPromotionGate
from evoagent.champion.repository import SQLiteChampionRegistry


class ChampionLifecycleSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: ChampionSelectionDecision
    selected_run: BenchmarkRunEvidence | None = None
    selected_assessment: ChampionRoundAssessment | None = None
    campaign: CampaignRecord | None = None
    record: ChampionSnapshotRecord | None = None
    reused: bool = False


class ChampionLifecycleService:
    """Govern benchmark selection, approval, activation, and rollback separately."""

    def __init__(
        self,
        *,
        registry: SQLiteChampionRegistry,
        campaign_governance: CampaignGovernanceService,
        gate: ChampionPromotionGate | None = None,
    ):
        self.registry = registry
        self.campaign_governance = campaign_governance
        self.gate = gate or ChampionPromotionGate()

    def evaluate_and_submit(
        self,
        package: BenchmarkComparisonPackageManifest,
        *,
        policy: ChampionPromotionPolicy,
        decision_id: str,
        decision_actor_id: str,
        decided_at: datetime,
    ) -> ChampionLifecycleSubmission:
        decision = self.gate.evaluate(
            package,
            policy=policy,
            decision_id=decision_id,
            decision_actor_id=decision_actor_id,
            decided_at=decided_at,
        )
        by_id = {item.evidence_id: item for item in package.runs}
        baseline = by_id[decision.baseline_run_id]
        family_id = baseline.contract.agent.family_id
        active = self.registry.active(family_id)
        if (
            active.snapshot_id != decision.baseline_snapshot_id
            or active.run_id != decision.baseline_run_id
            or active.benchmark_package_hash != package.package_hash
        ):
            raise ValueError(
                "Champion decision baseline differs from the active Registry pointer."
            )
        _, decision_reused = self.registry.store_decision(
            decision,
            family_id=family_id,
            actor_id=decision_actor_id,
        )
        if decision.action != ChampionDecisionAction.PROMOTE:
            return ChampionLifecycleSubmission(
                decision=decision,
                reused=decision_reused,
            )

        selected_run = by_id[decision.selected_run_id]
        selected_assessment = next(
            item
            for item in decision.assessments
            if item.run_id == decision.selected_run_id
        )
        target_key = self._target_key(
            family_id,
            decision.baseline_snapshot_id,
            decision.selected_snapshot_id,
        )
        fingerprint_source = self._fingerprint_source(
            package,
            decision,
            selected_run,
        )
        metadata = self._metadata(
            package,
            decision,
            family_id=family_id,
        )
        reservation = self.campaign_governance.reserve(
            campaign_type=CampaignType.CHAMPION_PROMOTION,
            target_key=target_key,
            fingerprint_source=fingerprint_source,
            risk=CampaignRisk.HIGH,
            generated_by=decision_actor_id,
            metadata=metadata,
        )
        campaign = reservation.campaign
        if reservation.reused:
            self._validate_campaign(
                campaign,
                package=package,
                decision=decision,
                selected_run=selected_run,
                selected_assessment=selected_assessment,
                require_authorized=False,
            )
        else:
            campaign = self.campaign_governance.attach_candidate(
                campaign,
                candidate_ref=self._candidate_ref(
                    family_id,
                    decision.selected_snapshot_id,
                ),
                artifact_payload=self._artifact_payload(
                    package,
                    decision,
                    selected_run,
                    selected_assessment,
                ),
                actor_id="champion-lifecycle",
            )

        record, record_reused = self.registry.admit_challenger(
            selected_run,
            decision,
            campaign_id=campaign.campaign_id,
            actor_id="champion-lifecycle",
            reason=(
                "Best eligible benchmarked Challenger admitted without changing "
                "the active Champion pointer."
            ),
        )
        record = self.registry.record_evaluation(
            family_id,
            record.snapshot_id,
            decision,
            actor_id=decision_actor_id,
            reason=(
                "Deterministic paired-Task bootstrap and hard promotion gates passed."
            ),
        )
        campaign = self.campaign_governance.repository.get(campaign.campaign_id)
        if campaign.state == CampaignState.CANDIDATE_READY:
            campaign = self.campaign_governance.submit_evaluation(
                campaign.campaign_id,
                passed=True,
                expected_revision=campaign.revision,
                actor_id=decision_actor_id,
                reason=(
                    f"Champion decision {decision.decision_hash} selected the exact "
                    f"Challenger {record.snapshot_id}."
                ),
            )
        if campaign.state not in {
            CampaignState.APPROVAL_PENDING,
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            raise ValueError(
                "Champion promotion Campaign did not reach an approval-capable state."
            )
        if self.registry.active(family_id).snapshot_id != decision.baseline_snapshot_id:
            raise RuntimeError(
                "Champion evaluation silently changed the active pointer."
            )
        return ChampionLifecycleSubmission(
            decision=decision,
            selected_run=selected_run,
            selected_assessment=selected_assessment,
            campaign=campaign,
            record=record,
            reused=decision_reused or reservation.reused or record_reused,
        )

    def approve(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ) -> CampaignRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        decision = self._decision_from_campaign(campaign)
        if actor_id == decision.decision_actor_id:
            raise ValueError(
                "Champion decision actor cannot approve its own promotion Campaign."
            )
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            expected_revision=expected_revision,
        )

    def synchronize_authorization(
        self,
        *,
        family_id: str,
        snapshot_id: str,
        campaign_id: str,
        actor_id: str,
    ) -> ChampionSnapshotRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Champion promotion Campaign is not AUTHORIZED.")
        record = self.registry.get(family_id, snapshot_id)
        decision = self.registry.get_decision(record.decision_id)
        selected_run = self._selected_run_from_campaign(campaign)
        selected_assessment = self._selected_assessment_from_campaign(campaign)
        package = self._package_from_campaign(campaign)
        self._validate_campaign(
            campaign,
            package=package,
            decision=decision,
            selected_run=selected_run,
            selected_assessment=selected_assessment,
            require_authorized=True,
        )
        self._validate_approvals(campaign, decision)
        before = self.registry.active(family_id)
        authorized = self.registry.mark_authorized(
            family_id,
            snapshot_id,
            campaign_id=campaign_id,
            actor_id=actor_id,
            reason="Exact benchmark-bound Champion Campaign was authorized.",
        )
        after = self.registry.active(family_id)
        if before != after:
            raise RuntimeError(
                "Registry authorization silently changed the active Champion pointer."
            )
        return authorized

    def activate(
        self,
        *,
        family_id: str,
        snapshot_id: str,
        campaign_id: str,
        expected_active_revision: int,
        actor_id: str,
    ) -> ChampionSnapshotRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Champion promotion Campaign is not AUTHORIZED.")
        record = self.registry.get(family_id, snapshot_id)
        if record.status != ChampionVersionStatus.AUTHORIZED:
            raise ValueError("Challenger Registry state is not AUTHORIZED.")
        decision = self.registry.get_decision(record.decision_id)
        package = self._package_from_campaign(campaign)
        selected_run = self._selected_run_from_campaign(campaign)
        selected_assessment = self._selected_assessment_from_campaign(campaign)
        self._validate_campaign(
            campaign,
            package=package,
            decision=decision,
            selected_run=selected_run,
            selected_assessment=selected_assessment,
            require_authorized=True,
        )
        self._validate_approvals(campaign, decision)
        active = self.registry.activate(
            family_id,
            snapshot_id,
            campaign_id=campaign_id,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            reason=(
                "Explicit Champion activation after benchmark evaluation and "
                "independent authorization."
            ),
        )
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state == CampaignState.AUTHORIZED:
            campaign = self.campaign_governance.repository.transition(
                campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id=actor_id,
                reason="Authorized Challenger became the active Champion pointer.",
            )
        if campaign.state != CampaignState.COMPLETED:
            raise RuntimeError(
                "Champion promotion Campaign did not reach COMPLETED."
            )
        return active

    def rollback(
        self,
        *,
        family_id: str,
        to_snapshot_id: str,
        expected_active_revision: int,
        actor_id: str,
        reason: str,
    ) -> ChampionSnapshotRecord:
        return self.registry.rollback(
            family_id,
            to_snapshot_id,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            reason=reason,
        )

    def _validate_approvals(
        self,
        campaign: CampaignRecord,
        decision: ChampionSelectionDecision,
    ) -> tuple[CampaignApproval, ...]:
        approvals = tuple(
            self.campaign_governance.repository.approvals(
                campaign.campaign_id
            )
        )
        approving = tuple(
            item
            for item in approvals
            if item.decision == ApprovalDecision.APPROVE
        )
        actors = tuple(item.actor_id for item in approving)
        if (
            campaign.required_approvals != 2
            or len(approvals) != 2
            or len(approving) != 2
            or len(set(actors)) != 2
            or decision.decision_actor_id in actors
        ):
            raise ValueError(
                "Champion promotion requires exactly two independent approving actors."
            )
        return approvals

    def _validate_campaign(
        self,
        campaign: CampaignRecord,
        *,
        package: BenchmarkComparisonPackageManifest,
        decision: ChampionSelectionDecision,
        selected_run: BenchmarkRunEvidence,
        selected_assessment: ChampionRoundAssessment,
        require_authorized: bool,
    ) -> None:
        family_id = selected_run.contract.agent.family_id
        expected_target = self._target_key(
            family_id,
            decision.baseline_snapshot_id,
            decision.selected_snapshot_id,
        )
        expected_fingerprint = fingerprint_payload(
            self._fingerprint_source(package, decision, selected_run)
        )
        if (
            campaign.campaign_type != CampaignType.CHAMPION_PROMOTION
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != decision.decision_actor_id
            or campaign.target_key != expected_target
            or campaign.fingerprint != expected_fingerprint
            or campaign.candidate_ref
            != self._candidate_ref(
                family_id,
                decision.selected_snapshot_id,
            )
            or campaign.metadata
            != self._metadata(package, decision, family_id=family_id)
            or campaign.artifact_payload
            != self._artifact_payload(
                package,
                decision,
                selected_run,
                selected_assessment,
            )
        ):
            raise ValueError(
                "Champion promotion Campaign differs from exact benchmark evidence."
            )
        if require_authorized and campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Champion promotion Campaign is not AUTHORIZED.")

    @staticmethod
    def _target_key(
        family_id: str,
        baseline_snapshot_id: str,
        selected_snapshot_id: str,
    ) -> str:
        return (
            f"champion-promotion:{family_id}:"
            f"{baseline_snapshot_id}->{selected_snapshot_id}"
        )

    @staticmethod
    def _candidate_ref(family_id: str, snapshot_id: str) -> str:
        return f"champion:{family_id}:{snapshot_id}"

    @staticmethod
    def _fingerprint_source(
        package: BenchmarkComparisonPackageManifest,
        decision: ChampionSelectionDecision,
        selected_run: BenchmarkRunEvidence,
    ) -> dict:
        return {
            "benchmark_package_hash": package.package_hash,
            "policy_hash": decision.policy.policy_hash,
            "decision_hash": decision.decision_hash,
            "selected_run_id": selected_run.evidence_id,
            "selected_evidence_hash": selected_run.evidence_hash,
            "selected_snapshot_id": selected_run.contract.agent.snapshot_id,
        }

    @staticmethod
    def _metadata(
        package: BenchmarkComparisonPackageManifest,
        decision: ChampionSelectionDecision,
        *,
        family_id: str,
    ) -> dict:
        return {
            "family_id": family_id,
            "baseline_snapshot_id": decision.baseline_snapshot_id,
            "selected_snapshot_id": decision.selected_snapshot_id,
            "selected_run_id": decision.selected_run_id,
            "benchmark_package_hash": package.package_hash,
            "decision_hash": decision.decision_hash,
            "production_deployment_performed": False,
        }

    @staticmethod
    def _artifact_payload(
        package: BenchmarkComparisonPackageManifest,
        decision: ChampionSelectionDecision,
        selected_run: BenchmarkRunEvidence,
        selected_assessment: ChampionRoundAssessment,
    ) -> dict:
        return {
            "kind": "champion_promotion_candidate",
            "benchmark_package": package.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "selected_run": selected_run.model_dump(mode="json"),
            "selected_assessment": selected_assessment.model_dump(mode="json"),
            "production_deployment_performed": False,
        }

    @staticmethod
    def _decision_from_campaign(
        campaign: CampaignRecord,
    ) -> ChampionSelectionDecision:
        payload = campaign.artifact_payload or {}
        return ChampionSelectionDecision.model_validate(payload.get("decision"))

    @staticmethod
    def _package_from_campaign(
        campaign: CampaignRecord,
    ) -> BenchmarkComparisonPackageManifest:
        payload = campaign.artifact_payload or {}
        return BenchmarkComparisonPackageManifest.model_validate(
            payload.get("benchmark_package")
        )

    @staticmethod
    def _selected_run_from_campaign(
        campaign: CampaignRecord,
    ) -> BenchmarkRunEvidence:
        payload = campaign.artifact_payload or {}
        return BenchmarkRunEvidence.model_validate(payload.get("selected_run"))

    @staticmethod
    def _selected_assessment_from_campaign(
        campaign: CampaignRecord,
    ) -> ChampionRoundAssessment:
        payload = campaign.artifact_payload or {}
        return ChampionRoundAssessment.model_validate(
            payload.get("selected_assessment")
        )


__all__ = [
    "ChampionLifecycleService",
    "ChampionLifecycleSubmission",
]
