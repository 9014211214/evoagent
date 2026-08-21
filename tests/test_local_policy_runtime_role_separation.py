from __future__ import annotations

import pytest

from evoagent.local_policy import (
    LocalPolicyRegistryConflictError,
    LocalPolicyVersionStatus,
)
from tests.test_local_policy_promotion_lifecycle import (
    FAMILY,
    P0,
    P1,
    _activate,
    _authorize_promotion,
    _authorize_rollback,
    _promotion_context,
)


PROMOTION_AUTHORIZER = "independent-local-policy-promotion-authorizer"
ACTIVATION_EXECUTOR = "independent-local-policy-activation-executor"
ROLLBACK_AUTHORIZER = "independent-local-policy-rollback-authorizer"
ROLLBACK_EXECUTOR = "independent-local-policy-rollback-executor"


def test_promotion_approver_cannot_authorize_and_authorizer_cannot_activate(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    service = context["service"]
    campaign = context["submission"].campaign
    reviewer_a = "independent-local-policy-promotion-reviewer-a"
    reviewer_b = "independent-local-policy-promotion-reviewer-b"
    campaign = service.approve_promotion(
        campaign.campaign_id,
        actor_id=reviewer_a,
        reason="Candidate evidence and authority boundary passed review.",
        expected_revision=campaign.revision,
    )
    campaign = service.approve_promotion(
        campaign.campaign_id,
        actor_id=reviewer_b,
        reason="Candidate safety and rollback readiness passed review.",
        expected_revision=campaign.revision,
    )
    before = tuple(context["registry"].events())

    with pytest.raises(ValueError, match="governed role"):
        service.synchronize_promotion_authorization(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            actor_id=reviewer_a,
        )
    assert tuple(context["registry"].events()) == before
    assert context["registry"].active(FAMILY).policy_id == P0

    service.synchronize_promotion_authorization(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        actor_id=PROMOTION_AUTHORIZER,
    )
    authorized_events = tuple(context["registry"].events())

    with pytest.raises(ValueError, match="governed role"):
        service.activate(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            expected_active_revision=0,
            actor_id=PROMOTION_AUTHORIZER,
        )
    assert tuple(context["registry"].events()) == authorized_events
    assert context["registry"].active(FAMILY).policy_id == P0

    service.activate(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        expected_active_revision=0,
        actor_id=ACTIVATION_EXECUTOR,
    )
    assert context["registry"].active(FAMILY).policy_id == P1


def test_completed_promotion_authorization_retry_is_read_only(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    campaign = _authorize_promotion(context)
    _activate(context)
    before_registry = tuple(context["registry"].events())
    before_campaign = tuple(
        context["campaign_repository"].audit_events()
    )

    record = context["service"].synchronize_promotion_authorization(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        actor_id=PROMOTION_AUTHORIZER,
    )

    assert record.status == LocalPolicyVersionStatus.ACTIVE
    assert tuple(context["registry"].events()) == before_registry
    assert tuple(context["campaign_repository"].audit_events()) == (
        before_campaign
    )

    with pytest.raises(
        LocalPolicyRegistryConflictError,
        match="another actor",
    ):
        context["service"].synchronize_promotion_authorization(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            actor_id="different-promotion-authorizer",
        )


def test_campaign_bound_promotion_arguments_cannot_be_substituted(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    campaign = _authorize_promotion(context)
    before = tuple(context["registry"].events())

    for family_id, candidate_id in (
        ("another-local-policy-family", P1),
        (FAMILY, "another-local-policy-candidate"),
    ):
        with pytest.raises(ValueError, match="arguments differ"):
            context["service"].synchronize_promotion_authorization(
                family_id=family_id,
                candidate_id=candidate_id,
                campaign_id=campaign.campaign_id,
                actor_id=PROMOTION_AUTHORIZER,
            )
        assert tuple(context["registry"].events()) == before
        assert context["registry"].active(FAMILY).policy_id == P0


def test_rollback_authorizer_cannot_execute_and_completed_retry_is_read_only(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    campaign = _authorize_rollback(context)
    before = tuple(context["registry"].events())

    with pytest.raises(ValueError, match="governed role"):
        context["service"].rollback(
            family_id=FAMILY,
            from_policy_id=P1,
            to_policy_id=P0,
            campaign_id=campaign.campaign_id,
            expected_active_revision=1,
            actor_id=ROLLBACK_AUTHORIZER,
        )
    assert tuple(context["registry"].events()) == before
    assert context["registry"].active(FAMILY).policy_id == P1

    context["service"].rollback(
        family_id=FAMILY,
        from_policy_id=P1,
        to_policy_id=P0,
        campaign_id=campaign.campaign_id,
        expected_active_revision=1,
        actor_id=ROLLBACK_EXECUTOR,
    )
    completed_registry = tuple(context["registry"].events())
    completed_campaign = tuple(
        context["campaign_repository"].audit_events()
    )

    record = context["service"].synchronize_rollback_authorization(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        actor_id=ROLLBACK_AUTHORIZER,
    )
    assert record.status == LocalPolicyVersionStatus.ROLLED_BACK
    assert tuple(context["registry"].events()) == completed_registry
    assert tuple(context["campaign_repository"].audit_events()) == (
        completed_campaign
    )

    with pytest.raises(
        LocalPolicyRegistryConflictError,
        match="another actor",
    ):
        context["service"].synchronize_rollback_authorization(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            actor_id="different-rollback-authorizer",
        )


def test_campaign_bound_rollback_arguments_cannot_be_substituted(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    campaign = _authorize_rollback(context)
    before = tuple(context["registry"].events())

    with pytest.raises(ValueError, match="arguments differ"):
        context["service"].synchronize_rollback_authorization(
            family_id="another-local-policy-family",
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            actor_id=ROLLBACK_AUTHORIZER,
        )
    assert tuple(context["registry"].events()) == before

    with pytest.raises(ValueError, match="arguments differ"):
        context["service"].rollback(
            family_id=FAMILY,
            from_policy_id="another-local-policy-candidate",
            to_policy_id=P0,
            campaign_id=campaign.campaign_id,
            expected_active_revision=1,
            actor_id=ROLLBACK_EXECUTOR,
        )
    assert tuple(context["registry"].events()) == before

    with pytest.raises(ValueError, match="target differs"):
        context["service"].rollback(
            family_id=FAMILY,
            from_policy_id=P1,
            to_policy_id="another-local-policy-parent",
            campaign_id=campaign.campaign_id,
            expected_active_revision=1,
            actor_id=ROLLBACK_EXECUTOR,
        )
    assert tuple(context["registry"].events()) == before
    assert context["registry"].active(FAMILY).policy_id == P1
