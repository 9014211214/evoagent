from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignRisk,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
)
from evoagent.local_policy import LocalPolicyPromotionLifecycleService


class _UnusedRegistry:
    pass


def test_local_policy_lifecycle_adapts_current_campaign_service_and_repository(
    tmp_path,
):
    repository = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    service = LocalPolicyPromotionLifecycleService(
        _UnusedRegistry(),
        CampaignGovernanceService(repository),
    )
    start = datetime.now(timezone.utc) - timedelta(minutes=1)

    reservation = service.campaign_governance.reserve(
        campaign_type=CampaignType.LOCAL_POLICY_PROMOTION,
        target_key="local-policy-promotion:test:p0->p1",
        fingerprint="a" * 64,
        generated_by="candidate-controller",
        risk=CampaignRisk.HIGH,
        cooldown=timedelta(0),
        now=start,
        metadata={"kind": "local_policy_promotion"},
    )
    assert reservation.reused is False
    campaign = service.campaign_governance.attach_candidate(
        reservation.campaign.campaign_id,
        candidate_ref="local-policy:test:p1",
        artifact_payload={"kind": "local_policy_promotion_candidate"},
        actor_id="promotion-decider",
        expected_revision=reservation.campaign.revision,
        now=start + timedelta(seconds=1),
    )
    campaign = service.campaigns.transition(
        campaign.campaign_id,
        CampaignState.EVALUATION_PENDING,
        actor_id="promotion-evaluator",
        expected_revision=campaign.revision,
        now=start + timedelta(seconds=2),
    )
    campaign = service.campaigns.transition(
        campaign.campaign_id,
        CampaignState.APPROVAL_PENDING,
        actor_id="promotion-evaluator",
        expected_revision=campaign.revision,
        now=start + timedelta(seconds=3),
    )
    campaign = service.campaign_governance.approve(
        campaign.campaign_id,
        actor_id="reviewer-a",
        reason="First independent review passed.",
        expected_revision=campaign.revision,
        now=start + timedelta(seconds=4),
    )
    campaign = service.campaign_governance.approve(
        campaign.campaign_id,
        actor_id="reviewer-b",
        reason="Second independent review passed.",
        expected_revision=campaign.revision,
        now=start + timedelta(seconds=5),
    )
    assert campaign.state == CampaignState.AUTHORIZED

    campaign = service.campaigns.transition(
        campaign.campaign_id,
        CampaignState.COMPLETED,
        actor_id="activation-executor",
        expected_revision=campaign.revision,
        now=start + timedelta(seconds=6),
    )
    assert campaign.state == CampaignState.COMPLETED

    transitions = [
        item.payload["reason"]
        for item in repository.audit_events()
        if item.event_type == "campaign_transitioned"
    ]
    assert transitions == [
        "Independent local-policy evaluation started.",
        "Independent local-policy evaluation passed.",
        "Authorized local-policy promotion completed.",
    ]
