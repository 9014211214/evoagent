from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.campaigns import CampaignState
from tests.test_local_policy_promotion_lifecycle import (
    FAMILY,
    P1,
    _activate,
    _authorize_promotion,
    _promotion_context,
)


PROMOTION_REVIEWER = "independent-local-policy-promotion-reviewer-a"


def _rollback_submission(context, *, suffix):
    record = context["registry"].get(FAMILY, P1)
    return context["service"].submit_rollback(
        family_id=FAMILY,
        candidate_id=P1,
        evidence_hash="9" * 64,
        reason=f"Rollback bypass regression {suffix}.",
        requested_by=f"independent-bypass-rollback-requester-{suffix}",
        requested_at=record.activated_at,
        evaluator_id=f"independent-bypass-rollback-evaluator-{suffix}",
        evaluated_at=max(
            datetime.now(timezone.utc),
            record.activated_at + timedelta(milliseconds=1),
        ),
    )


def test_generic_campaign_api_cannot_admit_activation_actor_as_rollback_approver(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    record = context["registry"].get(FAMILY, P1)
    submission = _rollback_submission(context, suffix="activation-actor")
    campaign = submission.campaign

    campaign = context["governance"].approve(
        campaign.campaign_id,
        actor_id=record.activated_by,
        reason="Generic API intentionally admits an invalid overlapping actor.",
        expected_revision=campaign.revision,
    )
    campaign = context["governance"].approve(
        campaign.campaign_id,
        actor_id="otherwise-independent-bypass-reviewer",
        reason="Second generic rollback approval.",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    with pytest.raises(
        ValueError,
        match="role separation|governed role",
    ):
        context["service"].synchronize_rollback_authorization(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            actor_id="independent-bypass-rollback-authorizer",
        )

    assert context["registry"].active(FAMILY).policy_id == P1


def test_direct_registry_authorization_cannot_normalize_promotion_reviewer_overlap(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    submission = _rollback_submission(context, suffix="promotion-reviewer")
    campaign = submission.campaign

    campaign = context["governance"].approve(
        campaign.campaign_id,
        actor_id=PROMOTION_REVIEWER,
        reason="Generic API intentionally reuses a Promotion reviewer.",
        expected_revision=campaign.revision,
    )
    campaign = context["governance"].approve(
        campaign.campaign_id,
        actor_id="independent-direct-bypass-reviewer",
        reason="Second generic approval for the direct Registry bypass.",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    authorizer = "independent-direct-registry-bypass-authorizer"
    context["registry"].mark_rollback_authorized(
        FAMILY,
        P1,
        campaign,
        actor_id=authorizer,
    )
    before = tuple(context["registry"].events())

    with pytest.raises(ValueError, match="role separation"):
        context["service"].synchronize_rollback_authorization(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            actor_id=authorizer,
        )

    assert tuple(context["registry"].events()) == before
    assert context["registry"].active(FAMILY).policy_id == P1
