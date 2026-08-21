from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from evoagent.program_rl import (
    FullyAttestedProgramLocalRLBindingPackage,
    ProgramLocalRLAcceptanceReceipt,
    ProgramLocalRLTrustedAnchors,
)

from .builders import (
    build_candidate_from_accepted_evidence,
    build_local_policy_promotion_decision,
    build_local_policy_promotion_report,
    build_local_policy_rollback_report,
    build_local_policy_rollback_request,
)
from .models import (
    LocalPolicyCandidateManifest,
    LocalPolicyPromotionDecision,
    LocalPolicyPromotionReport,
    LocalPolicyRollbackReport,
    LocalPolicyRollbackRequest,
    LocalPolicyVersionRecord,
    LocalPolicyVersionStatus,
)
from .repository import SQLiteLocalPolicyRegistry


class LocalPolicyPromotionSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: LocalPolicyCandidateManifest
    report: LocalPolicyPromotionReport
    decision: LocalPolicyPromotionDecision
    campaign: CampaignRecord
    reused: bool


class LocalPolicyRollbackSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: LocalPolicyRollbackRequest
    report: LocalPolicyRollbackReport
    campaign: CampaignRecord
    reused: bool


def promotion_target_key(candidate: LocalPolicyCandidateManifest) -> str:
    return (
        f"local-policy-promotion:{candidate.family_id}:"
        f"{candidate.base_policy_id}->{candidate.candidate_id}"
    )


def promotion_candidate_ref(candidate: LocalPolicyCandidateManifest) -> str:
    return f"local-policy:{candidate.family_id}:{candidate.candidate_id}"


def promotion_artifact(
    candidate: LocalPolicyCandidateManifest,
    report: LocalPolicyPromotionReport,
    decision: LocalPolicyPromotionDecision,
) -> dict:
    return {
        "kind": "local_policy_promotion_candidate",
        "candidate_manifest": candidate.model_dump(mode="json"),
        "promotion_report": report.model_dump(mode="json"),
        "promotion_decision": decision.model_dump(mode="json"),
        "checkpoint_promotion_performed": False,
        "production_activation_performed": False,
        "production_deployment_performed": False,
    }


def promotion_fingerprint_source(
    candidate: LocalPolicyCandidateManifest,
    report: LocalPolicyPromotionReport,
    decision: LocalPolicyPromotionDecision,
) -> dict:
    return {
        "candidate_manifest_hash": candidate.manifest_hash,
        "fully_attested_package_hash": candidate.fully_attested_package_hash,
        "acceptance_receipt_hash": candidate.acceptance_receipt_hash,
        "promotion_report_hash": report.report_hash,
        "promotion_decision_hash": decision.decision_hash,
        "selected_checkpoint_hash": candidate.selected_checkpoint_hash,
    }


def promotion_prohibited_actor_ids(
    candidate: LocalPolicyCandidateManifest,
    report: LocalPolicyPromotionReport,
    decision: LocalPolicyPromotionDecision,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *candidate.governed_actor_ids,
                candidate.created_by,
                report.evaluator_id,
                decision.decided_by,
            }
        )
    )


def promotion_metadata(
    candidate: LocalPolicyCandidateManifest,
    report: LocalPolicyPromotionReport,
    decision: LocalPolicyPromotionDecision,
) -> dict:
    return {
        "kind": "local_policy_promotion",
        "family_id": candidate.family_id,
        "candidate_id": candidate.candidate_id,
        "base_policy_id": candidate.base_policy_id,
        "evaluator_id": report.evaluator_id,
        "decision_actor_id": decision.decided_by,
        "prohibited_actor_ids": list(
            promotion_prohibited_actor_ids(candidate, report, decision)
        ),
        "production_activation_performed": False,
        "production_deployment_performed": False,
    }


def rollback_target_key(request: LocalPolicyRollbackRequest) -> str:
    return (
        f"local-policy-rollback:{request.family_id}:"
        f"{request.from_policy_id}->{request.to_policy_id}"
    )


def rollback_candidate_ref(request: LocalPolicyRollbackRequest) -> str:
    return f"local-policy-rollback:{request.family_id}:{request.from_policy_id}"


def rollback_artifact(
    request: LocalPolicyRollbackRequest,
    report: LocalPolicyRollbackReport,
) -> dict:
    return {
        "kind": "local_policy_rollback_candidate",
        "rollback_request": request.model_dump(mode="json"),
        "rollback_report": report.model_dump(mode="json"),
        "production_activation_performed": False,
        "production_deployment_performed": False,
    }


def rollback_fingerprint_source(
    request: LocalPolicyRollbackRequest,
    report: LocalPolicyRollbackReport,
) -> dict:
    return {
        "request_hash": request.request_hash,
        "report_hash": report.report_hash,
        "promotion_campaign_id": request.promotion_campaign_id,
        "promotion_decision_hash": request.promotion_decision_hash,
        "evidence_hash": request.evidence_hash,
    }


def rollback_metadata(
    request: LocalPolicyRollbackRequest,
    report: LocalPolicyRollbackReport,
    prohibited_actor_ids: tuple[str, ...],
) -> dict:
    return {
        "kind": "local_policy_rollback",
        "family_id": request.family_id,
        "from_policy_id": request.from_policy_id,
        "to_policy_id": request.to_policy_id,
        "requester_id": request.requested_by,
        "evaluator_id": report.evaluator_id,
        "prohibited_actor_ids": list(sorted(prohibited_actor_ids)),
        "production_activation_performed": False,
        "production_deployment_performed": False,
    }


class LocalPolicyPromotionLifecycleService:
    """Govern promotion and rollback without granting production deployment rights."""

    def __init__(
        self,
        registry: SQLiteLocalPolicyRegistry,
        campaign_governance: CampaignGovernanceService,
    ):
        self.registry = registry
        self.campaign_governance = campaign_governance
        self.campaigns = campaign_governance.repository

    def admit_candidate(
        self,
        package: FullyAttestedProgramLocalRLBindingPackage,
        anchors: ProgramLocalRLTrustedAnchors,
        receipt: ProgramLocalRLAcceptanceReceipt,
        *,
        family_id: str,
        candidate_id: str,
        base_policy_id: str,
        created_by: str,
        created_at: datetime,
    ) -> LocalPolicyVersionRecord:
        candidate = build_candidate_from_accepted_evidence(
            package,
            anchors,
            receipt,
            family_id=family_id,
            candidate_id=candidate_id,
            base_policy_id=base_policy_id,
            created_by=created_by,
            created_at=created_at,
        )
        return self.registry.admit_candidate(
            candidate,
            actor_id=created_by,
            now=created_at,
        )

    def submit_promotion(
        self,
        package: FullyAttestedProgramLocalRLBindingPackage,
        anchors: ProgramLocalRLTrustedAnchors,
        receipt: ProgramLocalRLAcceptanceReceipt,
        *,
        family_id: str,
        candidate_id: str,
        evaluator_id: str,
        evaluated_at: datetime,
        decision_actor_id: str,
        decided_at: datetime,
    ) -> LocalPolicyPromotionSubmission:
        record = self.registry.get(family_id, candidate_id)
        if not isinstance(record.manifest, LocalPolicyCandidateManifest):
            raise ValueError("Initial policy cannot enter promotion review.")
        candidate = record.manifest
        report = build_local_policy_promotion_report(
            candidate,
            package,
            anchors,
            receipt,
            evaluator_id=evaluator_id,
            evaluated_at=evaluated_at,
        )
        decision = build_local_policy_promotion_decision(
            candidate,
            report,
            decided_by=decision_actor_id,
            decided_at=decided_at,
        )

        if record.promotion_campaign_id is not None:
            if (
                record.promotion_report != report
                or record.promotion_decision != decision
            ):
                raise ValueError(
                    "Promotion retry differs from persisted candidate evidence."
                )
            campaign = self.campaigns.get(record.promotion_campaign_id)
            self._verify_promotion_campaign_evidence(
                campaign,
                candidate,
                report,
                decision,
            )
            campaign = self._finish_evaluation(
                campaign,
                passed=decision.promote,
                actor_id=evaluator_id,
                now=decided_at,
            )
            return LocalPolicyPromotionSubmission(
                candidate=candidate,
                report=report,
                decision=decision,
                campaign=campaign,
                reused=True,
            )

        reservation = self.campaign_governance.reserve(
            campaign_type=CampaignType.LOCAL_POLICY_PROMOTION,
            target_key=promotion_target_key(candidate),
            fingerprint=fingerprint_payload(
                promotion_fingerprint_source(candidate, report, decision)
            ),
            generated_by=candidate.created_by,
            risk=CampaignRisk.HIGH,
            cooldown=timedelta(0),
            now=evaluated_at,
            metadata=promotion_metadata(candidate, report, decision),
        )
        campaign = reservation.campaign
        self.registry.record_promotion(
            family_id,
            candidate_id,
            report,
            decision,
            campaign_id=campaign.campaign_id,
            actor_id=decision_actor_id,
            now=decided_at,
        )
        if campaign.state == CampaignState.OPEN:
            campaign = self.campaign_governance.attach_candidate(
                campaign.campaign_id,
                candidate_ref=promotion_candidate_ref(candidate),
                artifact_payload=promotion_artifact(candidate, report, decision),
                actor_id=decision_actor_id,
                expected_revision=campaign.revision,
            )
        self._verify_promotion_campaign_evidence(
            campaign,
            candidate,
            report,
            decision,
        )
        campaign = self._finish_evaluation(
            campaign,
            passed=decision.promote,
            actor_id=evaluator_id,
            now=decided_at,
        )
        return LocalPolicyPromotionSubmission(
            candidate=candidate,
            report=report,
            decision=decision,
            campaign=campaign,
            reused=reservation.reused,
        )

    def approve_promotion(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ) -> CampaignRecord:
        campaign = self.campaigns.get(campaign_id)
        self._require_campaign_type(
            campaign,
            CampaignType.LOCAL_POLICY_PROMOTION,
        )
        prohibited = set(campaign.metadata.get("prohibited_actor_ids", []))
        if actor_id in prohibited:
            raise ValueError(
                "Promotion approver overlaps accepted evidence or review roles."
            )
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
        )

    def synchronize_promotion_authorization(
        self,
        *,
        family_id: str,
        candidate_id: str,
        campaign_id: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        campaign = self.campaigns.get(campaign_id)
        now = max(
            now or datetime.now(timezone.utc),
            campaign.updated_at,
        )
        record = self.registry.get(family_id, candidate_id)
        self._verify_record_promotion_campaign(record, campaign)
        approvals = self._validated_approvals(campaign)
        prohibited = self._promotion_role_set(record, approvals)
        if actor_id in prohibited:
            raise ValueError(
                "Promotion authorization actor overlaps governed evidence roles."
            )
        return self.registry.mark_promotion_authorized(
            family_id,
            candidate_id,
            campaign,
            actor_id=actor_id,
            now=now,
        )

    def activate(
        self,
        *,
        family_id: str,
        candidate_id: str,
        campaign_id: str,
        expected_active_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        campaign = self.campaigns.get(campaign_id)
        now = max(
            now or datetime.now(timezone.utc),
            campaign.updated_at,
        )
        record = self.registry.get(family_id, candidate_id)
        self._verify_record_promotion_campaign(record, campaign)
        approvals = self._validated_approvals(campaign)
        prohibited = self._promotion_role_set(record, approvals)
        if record.promotion_authorized_by is not None:
            prohibited.add(record.promotion_authorized_by)
        if actor_id in prohibited:
            raise ValueError(
                "Local-policy activation actor overlaps promotion governance roles."
            )
        if (
            campaign.state == CampaignState.COMPLETED
            and self.registry.active(family_id).policy_id != candidate_id
        ):
            raise ValueError(
                "Completed promotion Campaign has no matching active pointer."
            )
        activated = self.registry.activate(
            family_id,
            candidate_id,
            campaign,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            now=now,
        )
        latest = self.campaigns.get(campaign_id)
        if latest.state == CampaignState.AUTHORIZED:
            latest = self.campaigns.transition(
                campaign_id,
                CampaignState.COMPLETED,
                actor_id=actor_id,
                expected_revision=latest.revision,
                now=now,
            )
        elif latest.state != CampaignState.COMPLETED:
            raise ValueError(
                "Promotion Campaign left the authorized/completed lifecycle."
            )
        return activated

    def submit_rollback(
        self,
        *,
        family_id: str,
        candidate_id: str,
        evidence_hash: str,
        reason: str,
        requested_by: str,
        requested_at: datetime,
        evaluator_id: str,
        evaluated_at: datetime,
    ) -> LocalPolicyRollbackSubmission:
        record = self.registry.get(family_id, candidate_id)
        if record.status != LocalPolicyVersionStatus.ACTIVE:
            raise ValueError("Rollback requires an ACTIVE local policy.")
        promotion_campaign = self.campaigns.get(record.promotion_campaign_id)
        if promotion_campaign.state != CampaignState.COMPLETED:
            raise ValueError(
                "Rollback requires a completed promotion Campaign."
            )
        promotion_approvals = self._validated_approvals(
            promotion_campaign,
            allow_completed=True,
        )
        forbidden = tuple(sorted(self._promotion_role_set(record, promotion_approvals)))
        request = build_local_policy_rollback_request(
            record,
            evidence_hash=evidence_hash,
            reason=reason,
            requested_by=requested_by,
            requested_at=requested_at,
            forbidden_actor_ids=forbidden,
        )
        report = build_local_policy_rollback_report(
            request,
            evaluator_id=evaluator_id,
            evaluated_at=evaluated_at,
            forbidden_actor_ids=forbidden,
        )
        rollback_prohibited = tuple(
            sorted({*forbidden, request.requested_by, report.evaluator_id})
        )

        if record.rollback_campaign_id is not None:
            if (
                record.rollback_request != request
                or record.rollback_report != report
            ):
                raise ValueError(
                    "Rollback retry differs from persisted evidence."
                )
            campaign = self.campaigns.get(record.rollback_campaign_id)
            self._verify_rollback_campaign_evidence(
                campaign,
                request,
                report,
                rollback_prohibited,
            )
            campaign = self._finish_evaluation(
                campaign,
                passed=True,
                actor_id=evaluator_id,
                now=evaluated_at,
            )
            return LocalPolicyRollbackSubmission(
                request=request,
                report=report,
                campaign=campaign,
                reused=True,
            )

        reservation = self.campaign_governance.reserve(
            campaign_type=CampaignType.LOCAL_POLICY_ROLLBACK,
            target_key=rollback_target_key(request),
            fingerprint=fingerprint_payload(
                rollback_fingerprint_source(request, report)
            ),
            generated_by=requested_by,
            risk=CampaignRisk.HIGH,
            cooldown=timedelta(0),
            now=requested_at,
            metadata=rollback_metadata(
                request,
                report,
                rollback_prohibited,
            ),
        )
        campaign = reservation.campaign
        self.registry.record_rollback_submission(
            family_id,
            candidate_id,
            request,
            report,
            campaign_id=campaign.campaign_id,
            actor_id=evaluator_id,
            now=evaluated_at,
        )
        if campaign.state == CampaignState.OPEN:
            campaign = self.campaign_governance.attach_candidate(
                campaign.campaign_id,
                candidate_ref=rollback_candidate_ref(request),
                artifact_payload=rollback_artifact(request, report),
                actor_id=evaluator_id,
                expected_revision=campaign.revision,
            )
        self._verify_rollback_campaign_evidence(
            campaign,
            request,
            report,
            rollback_prohibited,
        )
        campaign = self._finish_evaluation(
            campaign,
            passed=True,
            actor_id=evaluator_id,
            now=evaluated_at,
        )
        return LocalPolicyRollbackSubmission(
            request=request,
            report=report,
            campaign=campaign,
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
        campaign = self.campaigns.get(campaign_id)
        self._require_campaign_type(
            campaign,
            CampaignType.LOCAL_POLICY_ROLLBACK,
        )
        prohibited = set(campaign.metadata.get("prohibited_actor_ids", []))
        if actor_id in prohibited:
            raise ValueError(
                "Rollback approver overlaps promotion, request, or review roles."
            )
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
        )

    def synchronize_rollback_authorization(
        self,
        *,
        family_id: str,
        candidate_id: str,
        campaign_id: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        campaign = self.campaigns.get(campaign_id)
        now = max(
            now or datetime.now(timezone.utc),
            campaign.updated_at,
        )
        record = self.registry.get(family_id, candidate_id)
        self._verify_record_rollback_campaign(record, campaign)
        approvals = self._validated_approvals(campaign)
        prohibited = set(campaign.metadata.get("prohibited_actor_ids", []))
        prohibited.update(item.actor_id for item in approvals)
        if actor_id in prohibited:
            raise ValueError(
                "Rollback authorization actor overlaps governed roles."
            )
        return self.registry.mark_rollback_authorized(
            family_id,
            candidate_id,
            campaign,
            actor_id=actor_id,
            now=now,
        )

    def rollback(
        self,
        *,
        family_id: str,
        from_policy_id: str,
        to_policy_id: str,
        campaign_id: str,
        expected_active_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        campaign = self.campaigns.get(campaign_id)
        now = max(
            now or datetime.now(timezone.utc),
            campaign.updated_at,
        )
        record = self.registry.get(family_id, from_policy_id)
        self._verify_record_rollback_campaign(record, campaign)
        approvals = self._validated_approvals(campaign)
        prohibited = set(campaign.metadata.get("prohibited_actor_ids", []))
        prohibited.update(item.actor_id for item in approvals)
        if record.rollback_authorized_by is not None:
            prohibited.add(record.rollback_authorized_by)
        if actor_id in prohibited:
            raise ValueError(
                "Local-policy rollback executor overlaps governed roles."
            )
        if (
            campaign.state == CampaignState.COMPLETED
            and self.registry.active(family_id).policy_id != to_policy_id
        ):
            raise ValueError(
                "Completed rollback Campaign has no matching active pointer."
            )
        target = self.registry.rollback(
            family_id,
            from_policy_id=from_policy_id,
            to_policy_id=to_policy_id,
            campaign=campaign,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            now=now,
        )
        latest = self.campaigns.get(campaign_id)
        if latest.state == CampaignState.AUTHORIZED:
            latest = self.campaigns.transition(
                campaign_id,
                CampaignState.COMPLETED,
                actor_id=actor_id,
                expected_revision=latest.revision,
                now=now,
            )
        elif latest.state != CampaignState.COMPLETED:
            raise ValueError(
                "Rollback Campaign left the authorized/completed lifecycle."
            )
        return target

    def _finish_evaluation(
        self,
        campaign: CampaignRecord,
        *,
        passed: bool,
        actor_id: str,
        now: datetime,
    ) -> CampaignRecord:
        if campaign.state == CampaignState.CANDIDATE_READY:
            campaign = self.campaigns.transition(
                campaign.campaign_id,
                CampaignState.EVALUATION_PENDING,
                actor_id=actor_id,
                expected_revision=campaign.revision,
                now=now,
            )
        if campaign.state == CampaignState.EVALUATION_PENDING:
            campaign = self.campaigns.transition(
                campaign.campaign_id,
                (
                    CampaignState.APPROVAL_PENDING
                    if passed
                    else CampaignState.REJECTED
                ),
                actor_id=actor_id,
                expected_revision=campaign.revision,
                now=now,
            )
        valid = {
            CampaignState.APPROVAL_PENDING,
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }
        if passed and campaign.state not in valid:
            raise ValueError(
                "Passing Campaign did not reach approval/authorization state."
            )
        if not passed and campaign.state != CampaignState.REJECTED:
            raise ValueError("Failing Campaign did not reach REJECTED state.")
        return campaign

    @staticmethod
    def _require_campaign_type(
        campaign: CampaignRecord,
        expected: CampaignType,
    ) -> None:
        if campaign.campaign_type != expected:
            raise ValueError(f"Campaign must be {expected.value}.")

    def _validated_approvals(
        self,
        campaign: CampaignRecord,
        *,
        allow_completed: bool = True,
    ) -> tuple[CampaignApproval, ...]:
        permitted = {CampaignState.AUTHORIZED}
        if allow_completed:
            permitted.add(CampaignState.COMPLETED)
        if campaign.state not in permitted:
            raise ValueError("Campaign is not domain-authorized.")
        approvals = tuple(self.campaigns.approvals(campaign.campaign_id))
        actor_ids = tuple(item.actor_id for item in approvals)
        prohibited = set(campaign.metadata.get("prohibited_actor_ids", []))
        if (
            len(approvals) != 2
            or len(set(actor_ids)) != 2
            or any(item.decision != ApprovalDecision.APPROVE for item in approvals)
            or set(actor_ids) & prohibited
            or campaign.generated_by in set(actor_ids)
        ):
            raise ValueError(
                "Campaign approvals do not satisfy local-policy role separation."
            )
        return approvals

    @staticmethod
    def _promotion_role_set(
        record: LocalPolicyVersionRecord,
        approvals: tuple[CampaignApproval, ...],
    ) -> set[str]:
        if (
            not isinstance(record.manifest, LocalPolicyCandidateManifest)
            or record.promotion_report is None
            or record.promotion_decision is None
        ):
            raise ValueError("Candidate lacks complete promotion evidence.")
        return {
            *record.manifest.governed_actor_ids,
            record.manifest.created_by,
            record.promotion_report.evaluator_id,
            record.promotion_decision.decided_by,
            *(item.actor_id for item in approvals),
        }

    def _verify_record_promotion_campaign(
        self,
        record: LocalPolicyVersionRecord,
        campaign: CampaignRecord,
    ) -> None:
        if (
            not isinstance(record.manifest, LocalPolicyCandidateManifest)
            or record.promotion_report is None
            or record.promotion_decision is None
            or record.promotion_campaign_id != campaign.campaign_id
        ):
            raise ValueError(
                "Registry candidate is not bound to this promotion Campaign."
            )
        self._verify_promotion_campaign_evidence(
            campaign,
            record.manifest,
            record.promotion_report,
            record.promotion_decision,
        )

    @staticmethod
    def _verify_promotion_campaign_evidence(
        campaign: CampaignRecord,
        candidate: LocalPolicyCandidateManifest,
        report: LocalPolicyPromotionReport,
        decision: LocalPolicyPromotionDecision,
    ) -> None:
        expected_fingerprint = fingerprint_payload(
            promotion_fingerprint_source(candidate, report, decision)
        )
        if (
            campaign.campaign_type != CampaignType.LOCAL_POLICY_PROMOTION
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != candidate.created_by
            or campaign.target_key != promotion_target_key(candidate)
            or campaign.fingerprint != expected_fingerprint
            or campaign.candidate_ref != promotion_candidate_ref(candidate)
            or campaign.artifact_payload
            != promotion_artifact(candidate, report, decision)
            or campaign.metadata != promotion_metadata(candidate, report, decision)
        ):
            raise ValueError(
                "Promotion Campaign differs from exact candidate evidence."
            )

    def _verify_record_rollback_campaign(
        self,
        record: LocalPolicyVersionRecord,
        campaign: CampaignRecord,
    ) -> None:
        if (
            record.rollback_request is None
            or record.rollback_report is None
            or record.rollback_campaign_id != campaign.campaign_id
        ):
            raise ValueError(
                "Registry candidate is not bound to this rollback Campaign."
            )
        prohibited = tuple(
            sorted(campaign.metadata.get("prohibited_actor_ids", []))
        )
        self._verify_rollback_campaign_evidence(
            campaign,
            record.rollback_request,
            record.rollback_report,
            prohibited,
        )

    @staticmethod
    def _verify_rollback_campaign_evidence(
        campaign: CampaignRecord,
        request: LocalPolicyRollbackRequest,
        report: LocalPolicyRollbackReport,
        prohibited_actor_ids: tuple[str, ...],
    ) -> None:
        expected_fingerprint = fingerprint_payload(
            rollback_fingerprint_source(request, report)
        )
        if (
            campaign.campaign_type != CampaignType.LOCAL_POLICY_ROLLBACK
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != request.requested_by
            or campaign.target_key != rollback_target_key(request)
            or campaign.fingerprint != expected_fingerprint
            or campaign.candidate_ref != rollback_candidate_ref(request)
            or campaign.artifact_payload != rollback_artifact(request, report)
            or campaign.metadata
            != rollback_metadata(
                request,
                report,
                prohibited_actor_ids,
            )
        ):
            raise ValueError(
                "Rollback Campaign differs from exact rollback evidence."
            )


__all__ = [
    "LocalPolicyPromotionLifecycleService",
    "LocalPolicyPromotionSubmission",
    "LocalPolicyRollbackSubmission",
    "promotion_artifact",
    "promotion_candidate_ref",
    "promotion_fingerprint_source",
    "promotion_metadata",
    "promotion_prohibited_actor_ids",
    "promotion_target_key",
    "rollback_artifact",
    "rollback_candidate_ref",
    "rollback_fingerprint_source",
    "rollback_metadata",
    "rollback_target_key",
]
