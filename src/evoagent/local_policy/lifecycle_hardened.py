from __future__ import annotations

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
)

from .lifecycle import (
    LocalPolicyPromotionLifecycleService as _BaseLifecycleService,
    LocalPolicyPromotionSubmission,
    LocalPolicyRollbackSubmission,
)
from .models import LocalPolicyCandidateManifest
from .repository import LocalPolicyRegistryConflictError


class LocalPolicyPromotionLifecycleService(_BaseLifecycleService):
    """Apply complete role separation to Promotion and Rollback stages."""

    def approve_promotion(
        self,
        campaign_id,
        *,
        actor_id,
        reason,
        expected_revision,
    ):
        campaign = self.campaigns.get(campaign_id)
        record = self._promotion_record(campaign)
        self._validate_approval_actor(
            campaign,
            record,
            actor_id,
            rollback=False,
        )
        return super().approve_promotion(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
        )

    def approve_rollback(
        self,
        campaign_id,
        *,
        actor_id,
        reason,
        expected_revision,
    ):
        campaign = self.campaigns.get(campaign_id)
        record = self._rollback_record(campaign)
        self._validate_approval_actor(
            campaign,
            record,
            actor_id,
            rollback=True,
        )
        return super().approve_rollback(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
        )

    def synchronize_promotion_authorization(
        self,
        *,
        family_id,
        candidate_id,
        campaign_id,
        actor_id,
        now=None,
    ):
        campaign = self.campaigns.get(campaign_id)
        record = self._promotion_record(campaign)
        self._require_record_identity(record, family_id, candidate_id)
        self._validate_approvals(
            campaign,
            record,
            rollback=False,
        )
        if record.promotion_authorized_by is not None:
            if record.promotion_authorized_by != actor_id:
                raise LocalPolicyRegistryConflictError(
                    "Promotion authorization retry used another actor."
                )
            if campaign.state not in {
                CampaignState.AUTHORIZED,
                CampaignState.COMPLETED,
            }:
                raise ValueError(
                    "Authorized candidate is bound to an invalid Promotion Campaign state."
                )
            self._validate_approvals(
                campaign,
                record,
                rollback=False,
            )
            self._verify_record_promotion_campaign(record, campaign)
            return record
        self._validate_operation_actor(
            campaign,
            record,
            actor_id,
            rollback=False,
        )
        return super().synchronize_promotion_authorization(
            family_id=family_id,
            candidate_id=candidate_id,
            campaign_id=campaign_id,
            actor_id=actor_id,
            now=now,
        )

    def synchronize_rollback_authorization(
        self,
        *,
        family_id,
        candidate_id,
        campaign_id,
        actor_id,
        now=None,
    ):
        campaign = self.campaigns.get(campaign_id)
        record = self._rollback_record(campaign)
        self._require_record_identity(record, family_id, candidate_id)
        self._validate_approvals(
            campaign,
            record,
            rollback=True,
        )
        if record.rollback_authorized_by is not None:
            if record.rollback_authorized_by != actor_id:
                raise LocalPolicyRegistryConflictError(
                    "Rollback authorization retry used another actor."
                )
            if campaign.state not in {
                CampaignState.AUTHORIZED,
                CampaignState.COMPLETED,
            }:
                raise ValueError(
                    "Authorized rollback is bound to an invalid Campaign state."
                )
            self._validate_approvals(
                campaign,
                record,
                rollback=True,
            )
            self._verify_record_rollback_campaign(record, campaign)
            return record
        self._validate_operation_actor(
            campaign,
            record,
            actor_id,
            rollback=True,
        )
        return super().synchronize_rollback_authorization(
            family_id=family_id,
            candidate_id=candidate_id,
            campaign_id=campaign_id,
            actor_id=actor_id,
            now=now,
        )

    def activate(self, **kwargs):
        campaign = self.campaigns.get(kwargs["campaign_id"])
        record = self._promotion_record(campaign)
        self._require_record_identity(
            record,
            kwargs["family_id"],
            kwargs["candidate_id"],
        )
        self._validate_operation_actor(
            campaign,
            record,
            kwargs["actor_id"],
            rollback=False,
        )
        return super().activate(**kwargs)

    def rollback(self, **kwargs):
        campaign = self.campaigns.get(kwargs["campaign_id"])
        record = self._rollback_record(campaign)
        self._require_record_identity(
            record,
            kwargs["family_id"],
            kwargs["from_policy_id"],
        )
        if record.parent_policy_id != kwargs["to_policy_id"]:
            raise ValueError(
                "Rollback target differs from the Campaign candidate parent."
            )
        self._validate_operation_actor(
            campaign,
            record,
            kwargs["actor_id"],
            rollback=True,
        )
        return super().rollback(**kwargs)

    def submit_rollback(self, *, family_id, candidate_id, **kwargs):
        record = self.registry.get(family_id, candidate_id)
        promotion_controls = {
            actor
            for actor in (
                record.promotion_authorized_by,
                record.activated_by,
            )
            if actor is not None
        }
        if (
            kwargs["requested_by"] in promotion_controls
            or kwargs["evaluator_id"] in promotion_controls
        ):
            raise ValueError(
                "Rollback requester or evaluator overlaps promotion control roles."
            )
        return super().submit_rollback(
            family_id=family_id,
            candidate_id=candidate_id,
            **kwargs,
        )

    @staticmethod
    def _campaign_prohibited(campaign: CampaignRecord) -> set[str]:
        return set(campaign.metadata.get("prohibited_actor_ids", ()))

    def _validate_approvals(
        self,
        campaign: CampaignRecord,
        record,
        *,
        rollback: bool,
    ):
        expected_type = (
            CampaignType.LOCAL_POLICY_ROLLBACK
            if rollback
            else CampaignType.LOCAL_POLICY_PROMOTION
        )
        if campaign.campaign_type != expected_type:
            raise ValueError(
                "Local-policy approval validation received another Campaign type."
            )
        if (
            campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
        ):
            raise ValueError(
                "Local-policy Campaign must remain HIGH risk with exactly two approvals."
            )
        approvals = tuple(self.campaigns.approvals(campaign.campaign_id))
        if (
            len(approvals) != 2
            or any(
                item.decision != ApprovalDecision.APPROVE
                for item in approvals
            )
            or len({item.actor_id for item in approvals}) != 2
        ):
            raise ValueError(
                "Local-policy Campaign requires exactly two distinct approvals."
            )
        forbidden = {
            *self._forbidden(record, rollback=rollback),
            *self._campaign_prohibited(campaign),
        }
        if {item.actor_id for item in approvals} & forbidden:
            raise ValueError(
                "Persisted local-policy approvals violate role separation."
            )
        return approvals

    def _validate_approval_actor(
        self,
        campaign,
        record,
        actor_id,
        *,
        rollback,
    ):
        forbidden = {
            *self._forbidden(record, rollback=rollback),
            *self._campaign_prohibited(campaign),
        }
        if actor_id in forbidden:
            raise ValueError(
                "Local-policy approval actor overlaps a governed evidence role."
            )
        if actor_id == campaign.generated_by:
            raise ValueError(
                "Local-policy Campaign generator cannot approve its own Campaign."
            )

    def _validate_operation_actor(
        self,
        campaign,
        record,
        actor_id,
        *,
        rollback,
    ):
        approvals = tuple(self.campaigns.approvals(campaign.campaign_id))
        forbidden = {
            *self._forbidden(record, rollback=rollback),
            *self._campaign_prohibited(campaign),
            *(item.actor_id for item in approvals),
        }
        if actor_id in forbidden:
            raise ValueError(
                "Local-policy authorization or pointer executor overlaps a governed role."
            )

    def _forbidden(self, record, *, rollback: bool) -> set[str]:
        candidate = record.manifest
        if not isinstance(candidate, LocalPolicyCandidateManifest):
            raise ValueError(
                "Local-policy governance requires a candidate manifest."
            )
        forbidden = {
            *candidate.governed_actor_ids,
            candidate.created_by,
        }
        if record.promotion_report is not None:
            forbidden.add(record.promotion_report.evaluator_id)
        if record.promotion_decision is not None:
            forbidden.add(record.promotion_decision.decided_by)
        if record.promotion_authorized_by is not None:
            forbidden.add(record.promotion_authorized_by)
        if rollback:
            if record.activated_by is not None:
                forbidden.add(record.activated_by)
            if record.rollback_request is not None:
                forbidden.add(record.rollback_request.requested_by)
            if record.rollback_report is not None:
                forbidden.add(record.rollback_report.evaluator_id)
            if record.rollback_authorized_by is not None:
                forbidden.add(record.rollback_authorized_by)
        return forbidden

    @staticmethod
    def _require_record_identity(record, family_id, policy_id) -> None:
        if record.family_id != family_id or record.policy_id != policy_id:
            raise ValueError(
                "Local-policy operation arguments differ from the Campaign-bound record."
            )

    def _promotion_record(self, campaign):
        family_id = str(campaign.metadata.get("family_id", ""))
        candidate_id = str(campaign.metadata.get("candidate_id", ""))
        record = self.registry.get(family_id, candidate_id)
        if record.promotion_campaign_id != campaign.campaign_id:
            raise ValueError(
                "Promotion Campaign is not bound to the Registry candidate."
            )
        return record

    def _rollback_record(self, campaign):
        family_id = str(campaign.metadata.get("family_id", ""))
        candidate_id = str(campaign.metadata.get("from_policy_id", ""))
        record = self.registry.get(family_id, candidate_id)
        if record.rollback_campaign_id != campaign.campaign_id:
            raise ValueError(
                "Rollback Campaign is not bound to the Registry candidate."
            )
        return record


__all__ = [
    "LocalPolicyPromotionLifecycleService",
    "LocalPolicyPromotionSubmission",
    "LocalPolicyRollbackSubmission",
]
