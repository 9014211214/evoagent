from __future__ import annotations

import sqlite3
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


def _truncate_campaign_to_created(context, campaign_id):
    repository = context["campaign_repository"]
    created = next(
        item
        for item in repository.audit_events()
        if item.campaign_id == campaign_id
        and item.event_type == "campaign_created"
    )
    campaign = repository.get(campaign_id)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "DELETE FROM campaign_approvals WHERE campaign_id = ?",
            (campaign_id,),
        )
        connection.execute(
            "DELETE FROM campaign_audit_events WHERE sequence > ?",
            (created.sequence,),
        )
        connection.execute(
            "UPDATE campaigns SET state = ?, revision = 0, "
            "candidate_ref = NULL, artifact_json = NULL, "
            "cooldown_until = NULL, updated_at = ? WHERE campaign_id = ?",
            (
                CampaignState.OPEN.value,
                campaign.created_at.isoformat(),
                campaign_id,
            ),
        )
        connection.commit()
    assert repository.verify_audit() is True
    restored = repository.get(campaign_id)
    assert restored.state == CampaignState.OPEN
    assert restored.revision == 0
    return restored


def test_exact_promotion_submission_retry_is_read_only(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    first = context["submission"]
    before_registry = tuple(context["registry"].events())
    before_campaign = tuple(
        context["campaign_repository"].audit_events()
    )

    second = context["service"].submit_promotion(
        context["accepted"],
        context["anchors"],
        context["receipt"],
        family_id=FAMILY,
        candidate_id=P1,
        evaluator_id=first.report.evaluator_id,
        evaluated_at=first.report.evaluated_at,
        decision_actor_id=first.decision.decided_by,
        decided_at=first.decision.decided_at,
    )

    assert second.reused is True
    assert second.candidate == first.candidate
    assert second.report == first.report
    assert second.decision == first.decision
    assert second.campaign == first.campaign
    assert tuple(context["registry"].events()) == before_registry
    assert tuple(context["campaign_repository"].audit_events()) == (
        before_campaign
    )


def test_promotion_submission_recovers_open_campaign_without_registry_rewrite(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    first = context["submission"]
    _truncate_campaign_to_created(
        context,
        first.campaign.campaign_id,
    )
    before_registry = tuple(context["registry"].events())

    recovered = context["service"].submit_promotion(
        context["accepted"],
        context["anchors"],
        context["receipt"],
        family_id=FAMILY,
        candidate_id=P1,
        evaluator_id=first.report.evaluator_id,
        evaluated_at=first.report.evaluated_at,
        decision_actor_id=first.decision.decided_by,
        decided_at=first.decision.decided_at,
    )

    assert recovered.reused is True
    assert recovered.campaign.state == CampaignState.APPROVAL_PENDING
    assert tuple(context["registry"].events()) == before_registry
    assert tuple(
        item.event_type
        for item in context["campaign_repository"].audit_events()
    ) == (
        "campaign_created",
        "candidate_attached",
        "campaign_transitioned",
        "campaign_transitioned",
    )

    before_campaign = tuple(
        context["campaign_repository"].audit_events()
    )
    second = context["service"].submit_promotion(
        context["accepted"],
        context["anchors"],
        context["receipt"],
        family_id=FAMILY,
        candidate_id=P1,
        evaluator_id=first.report.evaluator_id,
        evaluated_at=first.report.evaluated_at,
        decision_actor_id=first.decision.decided_by,
        decided_at=first.decision.decided_at,
    )
    assert second.campaign == recovered.campaign
    assert tuple(context["registry"].events()) == before_registry
    assert tuple(context["campaign_repository"].audit_events()) == (
        before_campaign
    )


def test_exact_rollback_submission_retry_is_read_only(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    record = context["registry"].get(FAMILY, P1)
    requested_at = record.activated_at
    evaluated_at = max(
        datetime.now(timezone.utc),
        requested_at + timedelta(milliseconds=1),
    )

    first = context["service"].submit_rollback(
        family_id=FAMILY,
        candidate_id=P1,
        evidence_hash="9" * 64,
        reason="Exact rollback submission retry regression.",
        requested_by="submission-retry-rollback-requester",
        requested_at=requested_at,
        evaluator_id="submission-retry-rollback-evaluator",
        evaluated_at=evaluated_at,
    )
    before_registry = tuple(context["registry"].events())
    before_campaign = tuple(
        context["campaign_repository"].audit_events()
    )

    second = context["service"].submit_rollback(
        family_id=FAMILY,
        candidate_id=P1,
        evidence_hash="9" * 64,
        reason="Exact rollback submission retry regression.",
        requested_by="submission-retry-rollback-requester",
        requested_at=requested_at,
        evaluator_id="submission-retry-rollback-evaluator",
        evaluated_at=evaluated_at,
    )

    assert second.reused is True
    assert second.request == first.request
    assert second.report == first.report
    assert second.campaign == first.campaign
    assert tuple(context["registry"].events()) == before_registry
    assert tuple(context["campaign_repository"].audit_events()) == (
        before_campaign
    )


def test_rollback_submission_recovers_open_campaign_without_registry_rewrite(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    record = context["registry"].get(FAMILY, P1)
    requested_at = record.activated_at
    evaluated_at = max(
        datetime.now(timezone.utc),
        requested_at + timedelta(milliseconds=1),
    )
    first = context["service"].submit_rollback(
        family_id=FAMILY,
        candidate_id=P1,
        evidence_hash="8" * 64,
        reason="Rollback pre-attachment recovery regression.",
        requested_by="recovery-rollback-requester",
        requested_at=requested_at,
        evaluator_id="recovery-rollback-evaluator",
        evaluated_at=evaluated_at,
    )
    _truncate_campaign_to_created(
        context,
        first.campaign.campaign_id,
    )
    before_registry = tuple(context["registry"].events())

    recovered = context["service"].submit_rollback(
        family_id=FAMILY,
        candidate_id=P1,
        evidence_hash="8" * 64,
        reason="Rollback pre-attachment recovery regression.",
        requested_by="recovery-rollback-requester",
        requested_at=requested_at,
        evaluator_id="recovery-rollback-evaluator",
        evaluated_at=evaluated_at,
    )

    assert recovered.reused is True
    assert recovered.campaign.state == CampaignState.APPROVAL_PENDING
    assert tuple(context["registry"].events()) == before_registry
    rollback_events = tuple(
        item.event_type
        for item in context["campaign_repository"].audit_events()
        if item.campaign_id == first.campaign.campaign_id
    )
    assert rollback_events == (
        "campaign_created",
        "candidate_attached",
        "campaign_transitioned",
        "campaign_transitioned",
    )

    before_campaign = tuple(
        context["campaign_repository"].audit_events()
    )
    second = context["service"].submit_rollback(
        family_id=FAMILY,
        candidate_id=P1,
        evidence_hash="8" * 64,
        reason="Rollback pre-attachment recovery regression.",
        requested_by="recovery-rollback-requester",
        requested_at=requested_at,
        evaluator_id="recovery-rollback-evaluator",
        evaluated_at=evaluated_at,
    )
    assert second.campaign == recovered.campaign
    assert tuple(context["registry"].events()) == before_registry
    assert tuple(context["campaign_repository"].audit_events()) == (
        before_campaign
    )


def test_exact_first_approval_retry_is_read_only_and_conflict_fails(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    campaign = context["submission"].campaign
    actor = "submission-retry-promotion-reviewer"
    reason = "Exact first approval retry passed independent review."

    approved = context["service"].approve_promotion(
        campaign.campaign_id,
        actor_id=actor,
        reason=reason,
        expected_revision=campaign.revision,
    )
    before_events = tuple(
        context["campaign_repository"].audit_events()
    )
    before_approvals = tuple(
        context["campaign_repository"].approvals(campaign.campaign_id)
    )

    retried = context["service"].approve_promotion(
        campaign.campaign_id,
        actor_id=actor,
        reason=reason,
        expected_revision=campaign.revision,
    )

    assert retried == approved
    assert tuple(context["campaign_repository"].audit_events()) == (
        before_events
    )
    assert tuple(
        context["campaign_repository"].approvals(campaign.campaign_id)
    ) == before_approvals

    with pytest.raises(ValueError, match="approval retry conflicts"):
        context["service"].approve_promotion(
            campaign.campaign_id,
            actor_id=actor,
            reason="Conflicting retry rationale.",
            expected_revision=campaign.revision,
        )
