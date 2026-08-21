from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.campaigns import CampaignCheckpoint, SQLiteCampaignRepository
from evoagent.local_policy import (
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManager,
)
from tests.test_local_policy_promotion_tamper import (
    _completed_package,
    _rehash_package,
)


def _rewrite_campaign_events(package, mutate):
    previous = "0" * 64
    events = []
    for event in package.campaign_events:
        candidate = event.model_copy(update=mutate(event))
        event_hash = SQLiteCampaignRepository._event_hash(
            sequence=candidate.sequence,
            event_id=candidate.event_id,
            campaign_id=candidate.campaign_id,
            event_type=candidate.event_type,
            actor_id=candidate.actor_id,
            payload=candidate.payload,
            created_at=candidate.created_at,
            previous_hash=previous,
        )
        candidate = candidate.model_copy(
            update={
                "previous_hash": previous,
                "event_hash": event_hash,
            }
        )
        events.append(candidate)
        previous = event_hash
    forged = package.model_copy(
        update={
            "campaign_events": tuple(events),
            "campaign_checkpoint": CampaignCheckpoint(
                event_count=len(events),
                head_hash=previous,
            ),
        }
    )
    return _rehash_package(forged)


def test_rehashed_promotion_completion_actor_is_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    promotion_id = package.promotion_campaign.campaign_id
    evaluator = package.candidate_record.promotion_report.evaluator_id

    forged = _rewrite_campaign_events(
        package,
        lambda event: (
            {"actor_id": evaluator}
            if event.campaign_id == promotion_id
            and event.event_type == "campaign_transitioned"
            and event.payload.get("to_state") == "completed"
            else {}
        ),
    )

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="completion audit semantics",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_rehashed_campaign_evaluation_reason_is_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    promotion_id = package.promotion_campaign.campaign_id

    forged = _rewrite_campaign_events(
        package,
        lambda event: (
            {
                "payload": {
                    **event.payload,
                    "reason": "forged local-policy evaluation start",
                }
            }
            if event.campaign_id == promotion_id
            and event.event_type == "campaign_transitioned"
            and event.payload.get("to_state") == "evaluation_pending"
            else {}
        ),
    )

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="evaluation-start audit semantics",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_rehashed_future_package_time_is_rejected(tmp_path, monkeypatch):
    _, package = _completed_package(tmp_path, monkeypatch)
    forged = package.model_copy(
        update={
            "created_at": datetime.now(timezone.utc) + timedelta(minutes=5)
        }
    )
    forged = _rehash_package(forged)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="must not be in the future",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_rehashed_package_time_before_complete_evidence_is_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    forged = package.model_copy(
        update={"created_at": package.promotion_campaign.created_at}
    )
    forged = _rehash_package(forged)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="predates",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)
