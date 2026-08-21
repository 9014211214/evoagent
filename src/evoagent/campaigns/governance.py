from __future__ import annotations

from datetime import datetime

from evoagent.campaigns.models import (
    ApprovalDecision,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
)
from evoagent.campaigns.policy import CampaignApprovalPolicy
from evoagent.campaigns.repository import SQLiteCampaignRepository, fingerprint_payload


class CampaignGovernanceService:
    """Candidate reservation and approval authorization without execution rights."""

    def __init__(
        self,
        repository: SQLiteCampaignRepository,
        approval_policy: CampaignApprovalPolicy | None = None,
    ):
        self.repository = repository
        self.approval_policy = approval_policy or CampaignApprovalPolicy()

    def reserve(
        self,
        *,
        campaign_type: CampaignType,
        target_key: str,
        fingerprint_source,
        risk: CampaignRisk,
        generated_by: str,
        initial_state: CampaignState = CampaignState.OPEN,
        metadata: dict | None = None,
        now: datetime | None = None,
    ):
        return self.repository.reserve_campaign(
            campaign_type=campaign_type,
            target_key=target_key,
            fingerprint=fingerprint_payload(fingerprint_source),
            risk=risk,
            generated_by=generated_by,
            required_approvals=self.approval_policy.required_approvals(campaign_type, risk),
            initial_state=initial_state,
            metadata=metadata,
            now=now,
        )

    def attach_candidate(
        self,
        campaign: CampaignRecord,
        *,
        candidate_ref: str,
        artifact_payload: dict,
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> CampaignRecord:
        return self.repository.attach_candidate(
            campaign.campaign_id,
            candidate_ref=candidate_ref,
            artifact_payload=artifact_payload,
            expected_revision=campaign.revision,
            actor_id=actor_id,
            now=now,
        )

    def submit_evaluation(
        self,
        campaign_id: str,
        *,
        passed: bool,
        expected_revision: int,
        actor_id: str,
        reason: str,
        rejection_cooldown_seconds: int = 0,
        now: datetime | None = None,
    ) -> CampaignRecord:
        campaign = self.repository.get(campaign_id)
        if campaign.state == CampaignState.CANDIDATE_READY:
            campaign = self.repository.transition(
                campaign_id,
                to_state=CampaignState.EVALUATION_PENDING,
                expected_revision=expected_revision,
                actor_id=actor_id,
                reason="Independent evaluation started.",
                now=now,
            )
            expected_revision = campaign.revision
        if campaign.state != CampaignState.EVALUATION_PENDING:
            raise ValueError("Campaign is not awaiting an evaluation result.")
        return self.repository.transition(
            campaign_id,
            to_state=(CampaignState.APPROVAL_PENDING if passed else CampaignState.REJECTED),
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=reason,
            cooldown_seconds=(0 if passed else rejection_cooldown_seconds),
            now=now,
        )

    def approve(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
        decision: ApprovalDecision = ApprovalDecision.APPROVE,
        rejection_cooldown_seconds: int = 0,
        now: datetime | None = None,
    ) -> CampaignRecord:
        return self.repository.record_approval(
            campaign_id,
            actor_id=actor_id,
            decision=decision,
            reason=reason,
            expected_revision=expected_revision,
            rejection_cooldown_seconds=rejection_cooldown_seconds,
            now=now,
        )
