from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.local_policy import (
    LocalPolicyPromotionLifecycleService,
    SQLiteLocalPolicyRegistry,
    build_initial_local_policy_manifest,
)
from tests.test_program_local_rl_full_lineage import _full_lineage


FAMILY = "local-policy-family:v2.2-time"
P0 = "local-policy:time:p0"
P1 = "local-policy:time:p1"


def _base(package):
    return (
        package.runtime_attested_package
        .schema_attested_package
        .attested_package
        .base_package
    )


def _candidate_context(tmp_path, monkeypatch):
    _, _, accepted, anchors, receipt = _full_lineage(
        tmp_path / "accepted-evidence",
        monkeypatch,
    )
    base = _base(accepted)
    registry = SQLiteLocalPolicyRegistry(tmp_path / "local-policy.db")
    campaigns = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    service = LocalPolicyPromotionLifecycleService(
        registry,
        CampaignGovernanceService(campaigns),
    )
    start = max(
        receipt.accepted_at + timedelta(seconds=1),
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    initial = build_initial_local_policy_manifest(
        family_id=FAMILY,
        policy_id=P0,
        checkpoint_hash=base.result.initial_checkpoint_hash,
        optimizer_config_hash=base.intent.optimizer_config_hash,
        source_commit=base.source_commit,
        created_by="time-validation-bootstrap-owner",
        created_at=start,
    )
    registry.register_initial(
        initial,
        actor_id=initial.created_by,
        now=start,
    )
    service.admit_candidate(
        accepted,
        anchors,
        receipt,
        family_id=FAMILY,
        candidate_id=P1,
        base_policy_id=P0,
        created_by="time-validation-candidate-controller",
        created_at=start + timedelta(seconds=1),
    )
    return {
        "accepted": accepted,
        "anchors": anchors,
        "receipt": receipt,
        "service": service,
    }


def test_promotion_rejects_naive_evidence_time(tmp_path, monkeypatch):
    context = _candidate_context(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="include a timezone"):
        context["service"].submit_promotion(
            context["accepted"],
            context["anchors"],
            context["receipt"],
            family_id=FAMILY,
            candidate_id=P1,
            evaluator_id="independent-naive-time-evaluator",
            evaluated_at=datetime.now(),
            decision_actor_id="independent-naive-time-decider",
            decided_at=datetime.now(),
        )


def test_promotion_rejects_future_evidence_time(tmp_path, monkeypatch):
    context = _candidate_context(tmp_path, monkeypatch)
    future = datetime.now(timezone.utc) + timedelta(minutes=5)

    with pytest.raises(ValueError, match="must not be in the future"):
        context["service"].submit_promotion(
            context["accepted"],
            context["anchors"],
            context["receipt"],
            family_id=FAMILY,
            candidate_id=P1,
            evaluator_id="independent-future-time-evaluator",
            evaluated_at=future,
            decision_actor_id="independent-future-time-decider",
            decided_at=future,
        )
