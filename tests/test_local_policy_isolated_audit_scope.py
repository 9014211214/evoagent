from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evoagent.campaigns import CampaignRisk, CampaignType
from evoagent.local_policy import LocalPolicyPromotionPackageError
from tests.test_local_policy_promotion_lifecycle import (
    FAMILY,
    P0,
    P1,
    _activate,
    _authorize_promotion,
    _authorize_rollback,
    _build_full_package,
    _promotion_context,
)


def test_final_package_rejects_unrelated_campaign_audit_events(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    rollback_campaign = _authorize_rollback(context)
    context["service"].rollback(
        family_id=FAMILY,
        from_policy_id=P1,
        to_policy_id=P0,
        campaign_id=rollback_campaign.campaign_id,
        expected_active_revision=1,
        actor_id="independent-local-policy-rollback-executor",
    )
    context["governance"].reserve(
        campaign_type=CampaignType.SKILL,
        target_key="unrelated-skill:foreign-audit-scope",
        fingerprint_source={"fingerprint": "f" * 64},
        generated_by="unrelated-campaign-generator",
        risk=CampaignRisk.LOW,
        now=datetime.now(timezone.utc),
        metadata={"kind": "unrelated_audit_scope_regression"},
    )

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="another Campaign audit event",
    ):
        _build_full_package(context)
