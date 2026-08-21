from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignConflictError,
    CampaignGovernanceService,
    CampaignRecord,
    CampaignReservation,
    CampaignRisk,
    CampaignState,
    CampaignType,
    fingerprint_payload,
)

from .builders import (
    build_local_policy_promotion_decision,
    build_local_policy_promotion_report,
    build_local_policy_rollback_report,
    build_local_policy_rollback_request,
)
from .lifecycle import (
    promotion_artifact,
    promotion_candidate_ref,
    promotion_fingerprint_source,
    promotion_metadata,
    promotion_target_key,
    rollback_artifact,
    rollback_candidate_ref,
    rollback_fingerprint_source,
    rollback_metadata,
    rollback_target_key,
)
from .lifecycle_hardened import (
    LocalPolicyPromotionLifecycleService as _HardenedLifecycleService,
    LocalPolicyPromotionSubmission,
    LocalPolicyRollbackSubmission,
)
from .models import LocalPolicyCandidateManifest
from .repository import LocalPolicyRegistryConflictError


class _LocalPolicyCampaignGovernanceAdapter:
    """Translate the local-policy lifecycle's bounded Campaign calls."""

    def __init__(self, service: CampaignGovernanceService):
        self.service = service
        self.repository = service.repository
        self.approval_policy = service.approval_policy

    def reserve(
        self,
        *,
        campaign_type,
        target_key,
        fingerprint,
        generated_by,
        risk,
        cooldown=None,
        now=None,
        metadata=None,
    ):
        if cooldown not in {None, timedelta(0)}:
            raise ValueError(
                "Local-policy Campaign reservation does not support a cooldown."
            )
        required = self.approval_policy.required_approvals(
            campaign_type,
            risk,
        )
        existing = self.repository.find_open_by_target(target_key)
        if existing is not None:
            if (
                existing.fingerprint != fingerprint
                or existing.campaign_type != campaign_type
                or existing.risk != risk
                or existing.generated_by != generated_by
                or existing.required_approvals != required
                or existing.metadata != (metadata or {})
            ):
                raise CampaignConflictError(
                    "Open local-policy Campaign differs from exact immutable evidence."
                )
            return CampaignReservation(campaign=existing, reused=True)
        return self.repository.reserve_campaign(
            campaign_type=campaign_type,
            target_key=target_key,
            fingerprint=fingerprint,
            risk=risk,
            generated_by=generated_by,
            required_approvals=required,
            metadata=metadata,
            now=now,
        )

    def attach_candidate(
        self,
        campaign_id,
        *,
        candidate_ref,
        artifact_payload,
        actor_id,
        expected_revision,
        now=None,
    ):
        campaign = self.repository.get(campaign_id)
        effective_now = max(
            now or datetime.now(timezone.utc),
            campaign.updated_at,
        )
        return self.repository.attach_candidate(
            campaign_id,
            candidate_ref=candidate_ref,
            artifact_payload=artifact_payload,
            expected_revision=expected_revision,
            actor_id=actor_id,
            now=effective_now,
        )

    def approve(
        self,
        campaign_id,
        *,
        actor_id,
        reason,
        expected_revision,
        now=None,
    ):
        campaign = self.repository.get(campaign_id)
        existing = tuple(
            item
            for item in self.repository.approvals(campaign_id)
            if item.actor_id == actor_id
        )
        if existing:
            if (
                len(existing) != 1
                or existing[0].decision != ApprovalDecision.APPROVE
                or existing[0].reason != reason
            ):
                raise ValueError(
                    "Local-policy approval retry conflicts with immutable evidence."
                )
            if campaign.state not in {
                CampaignState.APPROVAL_PENDING,
                CampaignState.AUTHORIZED,
                CampaignState.COMPLETED,
            }:
                raise ValueError(
                    "Local-policy approval retry targets an invalid Campaign state."
                )
            return campaign
        effective_now = max(
            now or datetime.now(timezone.utc),
            campaign.updated_at,
        )
        return self.service.approve(
            campaign_id,
            actor_id=actor_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            expected_revision=expected_revision,
            now=effective_now,
        )

    def __getattr__(self, name):
        return getattr(self.service, name)


class _LocalPolicyCampaignRepositoryAdapter:
    """Supply explicit semantic reasons to the current Campaign repository."""

    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _reason(record: CampaignRecord, to_state: CampaignState) -> str:
        if to_state == CampaignState.EVALUATION_PENDING:
            return "Independent local-policy evaluation started."
        if to_state == CampaignState.APPROVAL_PENDING:
            return "Independent local-policy evaluation passed."
        if to_state == CampaignState.REJECTED:
            return "Independent local-policy evaluation rejected."
        if to_state == CampaignState.COMPLETED:
            return (
                "Authorized local-policy promotion completed."
                if record.campaign_type
                == CampaignType.LOCAL_POLICY_PROMOTION
                else "Authorized local-policy rollback completed."
            )
        raise ValueError(
            "Local-policy lifecycle requested an unsupported Campaign transition."
        )

    def transition(
        self,
        campaign_id,
        to_state,
        *,
        actor_id,
        expected_revision,
        now=None,
        reason=None,
        cooldown_seconds=0,
    ):
        record = self.repository.get(campaign_id)
        effective_now = max(
            now or datetime.now(timezone.utc),
            record.updated_at,
        )
        return self.repository.transition(
            campaign_id,
            to_state=to_state,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=reason or self._reason(record, to_state),
            cooldown_seconds=cooldown_seconds,
            now=effective_now,
        )

    def __getattr__(self, name):
        return getattr(self.repository, name)


class LocalPolicyPromotionLifecycleService(_HardenedLifecycleService):
    """Final lifecycle with stage-aware recovery and monotonic evidence."""

    def __init__(
        self,
        registry,
        campaign_governance: CampaignGovernanceService,
    ):
        self.registry = registry
        self._campaign_governance_service = campaign_governance
        self.campaign_governance = _LocalPolicyCampaignGovernanceAdapter(
            campaign_governance
        )
        self.campaigns = _LocalPolicyCampaignRepositoryAdapter(
            campaign_governance.repository
        )

    @staticmethod
    def _require_not_future(*values: datetime) -> None:
        now = datetime.now(timezone.utc)
        for value in values:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(
                    "Local-policy lifecycle evidence time must include a timezone."
                )
            if value > now + timedelta(minutes=2):
                raise ValueError(
                    "Local-policy lifecycle evidence time must not be in the future beyond the bounded clock-skew window."
                )

    def submit_promotion(
        self,
        package,
        anchors,
        receipt,
        *,
        family_id,
        candidate_id,
        evaluator_id,
        evaluated_at,
        decision_actor_id,
        decided_at,
    ) -> LocalPolicyPromotionSubmission:
        self._require_not_future(evaluated_at, decided_at)
        record = self.registry.get(family_id, candidate_id)
        has_any = any(
            value is not None
            for value in (
                record.promotion_report,
                record.promotion_decision,
                record.promotion_campaign_id,
            )
        )
        has_all = all(
            value is not None
            for value in (
                record.promotion_report,
                record.promotion_decision,
                record.promotion_campaign_id,
            )
        )
        if has_any and not has_all:
            raise LocalPolicyRegistryConflictError(
                "Candidate contains incomplete immutable promotion evidence."
            )
        if not has_all:
            return super().submit_promotion(
                package,
                anchors,
                receipt,
                family_id=family_id,
                candidate_id=candidate_id,
                evaluator_id=evaluator_id,
                evaluated_at=evaluated_at,
                decision_actor_id=decision_actor_id,
                decided_at=decided_at,
            )

        candidate = record.manifest
        if not isinstance(candidate, LocalPolicyCandidateManifest):
            raise TypeError(
                "Local-policy promotion requires a candidate manifest."
            )
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
        if (
            report != record.promotion_report
            or decision != record.promotion_decision
        ):
            raise LocalPolicyRegistryConflictError(
                "Promotion retry differs from immutable evaluation evidence."
            )
        campaign = self.campaigns.get(record.promotion_campaign_id)
        self._validate_promotion_campaign_shell(
            campaign,
            candidate,
            report,
            decision,
        )
        if campaign.state == CampaignState.OPEN:
            campaign = self.campaign_governance.attach_candidate(
                campaign.campaign_id,
                candidate_ref=promotion_candidate_ref(candidate),
                artifact_payload=promotion_artifact(
                    candidate,
                    report,
                    decision,
                ),
                actor_id=decision.decided_by,
                expected_revision=campaign.revision,
            )
        if campaign.state != CampaignState.OPEN:
            self._verify_promotion_campaign_evidence(
                campaign,
                candidate,
                report,
                decision,
            )
        if campaign.state in {
            CampaignState.CANDIDATE_READY,
            CampaignState.EVALUATION_PENDING,
        }:
            campaign = self._finish_evaluation(
                campaign,
                passed=decision.promote,
                actor_id=report.evaluator_id,
                now=max(evaluated_at, decided_at),
            )
        self._verify_promotion_campaign_evidence(
            campaign,
            candidate,
            report,
            decision,
        )
        return LocalPolicyPromotionSubmission(
            candidate=candidate,
            report=report,
            decision=decision,
            campaign=campaign,
            reused=True,
        )

    def submit_rollback(
        self,
        *,
        family_id,
        candidate_id,
        evidence_hash,
        reason,
        requested_by,
        requested_at,
        evaluator_id,
        evaluated_at,
    ) -> LocalPolicyRollbackSubmission:
        self._require_not_future(requested_at, evaluated_at)
        record = self.registry.get(family_id, candidate_id)
        has_any = any(
            value is not None
            for value in (
                record.rollback_request,
                record.rollback_report,
                record.rollback_campaign_id,
            )
        )
        has_all = all(
            value is not None
            for value in (
                record.rollback_request,
                record.rollback_report,
                record.rollback_campaign_id,
            )
        )
        if has_any and not has_all:
            raise LocalPolicyRegistryConflictError(
                "Candidate contains incomplete immutable rollback evidence."
            )
        if not has_all:
            return super().submit_rollback(
                family_id=family_id,
                candidate_id=candidate_id,
                evidence_hash=evidence_hash,
                reason=reason,
                requested_by=requested_by,
                requested_at=requested_at,
                evaluator_id=evaluator_id,
                evaluated_at=evaluated_at,
            )

        promotion_campaign = self.campaigns.get(
            record.promotion_campaign_id
        )
        promotion_approvals = self._validated_approvals(
            promotion_campaign,
            allow_completed=True,
        )
        forbidden = tuple(
            sorted(self._promotion_role_set(record, promotion_approvals))
        )
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
        if (
            request != record.rollback_request
            or report != record.rollback_report
        ):
            raise LocalPolicyRegistryConflictError(
                "Rollback retry differs from immutable assessment evidence."
            )
        prohibited = tuple(
            sorted({*forbidden, request.requested_by, report.evaluator_id})
        )
        campaign = self.campaigns.get(record.rollback_campaign_id)
        self._validate_rollback_campaign_shell(
            campaign,
            request,
            report,
            prohibited,
        )
        if campaign.state == CampaignState.OPEN:
            campaign = self.campaign_governance.attach_candidate(
                campaign.campaign_id,
                candidate_ref=rollback_candidate_ref(request),
                artifact_payload=rollback_artifact(request, report),
                actor_id=report.evaluator_id,
                expected_revision=campaign.revision,
            )
        if campaign.state != CampaignState.OPEN:
            self._verify_rollback_campaign_evidence(
                campaign,
                request,
                report,
                prohibited,
            )
        if campaign.state in {
            CampaignState.CANDIDATE_READY,
            CampaignState.EVALUATION_PENDING,
        }:
            campaign = self._finish_evaluation(
                campaign,
                passed=report.safe_to_rollback,
                actor_id=report.evaluator_id,
                now=evaluated_at,
            )
        self._verify_rollback_campaign_evidence(
            campaign,
            request,
            report,
            prohibited,
        )
        return LocalPolicyRollbackSubmission(
            request=request,
            report=report,
            campaign=campaign,
            reused=True,
        )

    @staticmethod
    def _validate_promotion_campaign_shell(
        campaign,
        candidate,
        report,
        decision,
    ) -> None:
        allowed_states = {
            CampaignState.OPEN,
            CampaignState.CANDIDATE_READY,
            CampaignState.EVALUATION_PENDING,
            CampaignState.APPROVAL_PENDING,
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }
        if (
            campaign.campaign_type
            != CampaignType.LOCAL_POLICY_PROMOTION
            or campaign.state not in allowed_states
            or campaign.risk != CampaignRisk.HIGH
            or campaign.target_key != promotion_target_key(candidate)
            or campaign.fingerprint
            != fingerprint_payload(
                promotion_fingerprint_source(candidate, report, decision)
            )
            or campaign.generated_by != candidate.created_by
            or campaign.required_approvals != 2
            or campaign.metadata
            != promotion_metadata(candidate, report, decision)
            or (
                campaign.state == CampaignState.OPEN
                and (
                    campaign.candidate_ref is not None
                    or campaign.artifact_payload is not None
                )
            )
        ):
            raise ValueError(
                "Promotion Campaign shell differs from immutable evidence."
            )

    @staticmethod
    def _validate_rollback_campaign_shell(
        campaign,
        request,
        report,
        prohibited_actor_ids,
    ) -> None:
        allowed_states = {
            CampaignState.OPEN,
            CampaignState.CANDIDATE_READY,
            CampaignState.EVALUATION_PENDING,
            CampaignState.APPROVAL_PENDING,
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }
        if (
            campaign.campaign_type != CampaignType.LOCAL_POLICY_ROLLBACK
            or campaign.state not in allowed_states
            or campaign.risk != CampaignRisk.HIGH
            or campaign.target_key != rollback_target_key(request)
            or campaign.fingerprint
            != fingerprint_payload(
                rollback_fingerprint_source(request, report)
            )
            or campaign.generated_by != request.requested_by
            or campaign.required_approvals != 2
            or campaign.metadata
            != rollback_metadata(
                request,
                report,
                prohibited_actor_ids,
            )
            or (
                campaign.state == CampaignState.OPEN
                and (
                    campaign.candidate_ref is not None
                    or campaign.artifact_payload is not None
                )
            )
        ):
            raise ValueError(
                "Rollback Campaign shell differs from immutable evidence."
            )

    def _finish_evaluation(
        self,
        campaign,
        *,
        passed,
        actor_id,
        now,
    ):
        transition_at = max(
            datetime.now(timezone.utc),
            campaign.updated_at,
            now,
        )
        return super()._finish_evaluation(
            campaign,
            passed=passed,
            actor_id=actor_id,
            now=transition_at,
        )


__all__ = [
    "LocalPolicyPromotionLifecycleService",
    "LocalPolicyPromotionSubmission",
    "LocalPolicyRollbackSubmission",
]
