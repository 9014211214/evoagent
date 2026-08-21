from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.local_policy import (
    SQLiteLocalPolicyRegistry,
    build_candidate_from_accepted_evidence,
    build_initial_local_policy_manifest,
    build_local_policy_promotion_decision,
    build_local_policy_promotion_report,
)
from tests.test_program_local_rl_full_lineage import _full_lineage


FAMILY = "local-policy-family:chronology"
P0 = "local-policy:chronology:p0"
P1 = "local-policy:chronology:p1"


def _base(package):
    return (
        package.runtime_attested_package
        .schema_attested_package
        .attested_package
        .base_package
    )


def _accepted(tmp_path, monkeypatch):
    _, _, package, anchors, receipt = _full_lineage(
        tmp_path / "accepted-evidence",
        monkeypatch,
    )
    return package, anchors, receipt, _base(package)


def test_future_initial_manifest_fails_before_registry_write(
    tmp_path,
    monkeypatch,
):
    _, _, _, base = _accepted(tmp_path, monkeypatch)
    registry = SQLiteLocalPolicyRegistry(tmp_path / "future-initial.db")
    now = datetime.now(timezone.utc)
    manifest = build_initial_local_policy_manifest(
        family_id=FAMILY,
        policy_id=P0,
        checkpoint_hash=base.result.initial_checkpoint_hash,
        optimizer_config_hash=base.intent.optimizer_config_hash,
        source_commit=base.source_commit,
        created_by="chronology-bootstrap-owner",
        created_at=now + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="postdates its Registry write"):
        registry.register_initial(
            manifest,
            actor_id=manifest.created_by,
            now=now,
        )

    assert registry.family_exists(FAMILY) is False
    assert registry.events() == ()


def test_candidate_earlier_than_parent_fails_without_new_event(
    tmp_path,
    monkeypatch,
):
    package, anchors, receipt, base = _accepted(tmp_path, monkeypatch)
    registry = SQLiteLocalPolicyRegistry(tmp_path / "early-candidate.db")
    parent_at = max(
        receipt.accepted_at + timedelta(seconds=3),
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    initial = build_initial_local_policy_manifest(
        family_id=FAMILY,
        policy_id=P0,
        checkpoint_hash=base.result.initial_checkpoint_hash,
        optimizer_config_hash=base.intent.optimizer_config_hash,
        source_commit=base.source_commit,
        created_by="chronology-bootstrap-owner",
        created_at=parent_at,
    )
    registry.register_initial(
        initial,
        actor_id=initial.created_by,
        now=parent_at,
    )
    candidate = build_candidate_from_accepted_evidence(
        package,
        anchors,
        receipt,
        family_id=FAMILY,
        candidate_id=P1,
        base_policy_id=P0,
        created_by="chronology-candidate-controller",
        created_at=parent_at - timedelta(seconds=1),
    )
    before = registry.events()

    with pytest.raises(ValueError, match="parent/write chronology"):
        registry.admit_candidate(
            candidate,
            actor_id=candidate.created_by,
            now=parent_at + timedelta(seconds=1),
        )

    assert registry.list_versions(FAMILY)[0].policy_id == P0
    assert registry.events() == before


def test_future_promotion_decision_fails_without_promotion_event(
    tmp_path,
    monkeypatch,
):
    package, anchors, receipt, base = _accepted(tmp_path, monkeypatch)
    registry = SQLiteLocalPolicyRegistry(tmp_path / "future-decision.db")
    initial_at = max(
        receipt.accepted_at + timedelta(seconds=1),
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    initial = build_initial_local_policy_manifest(
        family_id=FAMILY,
        policy_id=P0,
        checkpoint_hash=base.result.initial_checkpoint_hash,
        optimizer_config_hash=base.intent.optimizer_config_hash,
        source_commit=base.source_commit,
        created_by="chronology-bootstrap-owner",
        created_at=initial_at,
    )
    registry.register_initial(
        initial,
        actor_id=initial.created_by,
        now=initial_at,
    )
    candidate = build_candidate_from_accepted_evidence(
        package,
        anchors,
        receipt,
        family_id=FAMILY,
        candidate_id=P1,
        base_policy_id=P0,
        created_by="chronology-candidate-controller",
        created_at=initial_at + timedelta(seconds=1),
    )
    registry.admit_candidate(
        candidate,
        actor_id=candidate.created_by,
        now=initial_at + timedelta(seconds=1),
    )
    evaluated_at = max(
        datetime.now(timezone.utc),
        candidate.created_at,
    )
    report = build_local_policy_promotion_report(
        candidate,
        package,
        anchors,
        receipt,
        evaluator_id="chronology-promotion-evaluator",
        evaluated_at=evaluated_at,
    )
    decision = build_local_policy_promotion_decision(
        candidate,
        report,
        decided_by="chronology-promotion-decider",
        decided_at=evaluated_at + timedelta(minutes=1),
    )
    before = registry.events()

    with pytest.raises(ValueError, match="Registry write chronology differs"):
        registry.record_promotion(
            FAMILY,
            P1,
            report,
            decision,
            campaign_id="local-policy-promotion-campaign:future-decision",
            actor_id=decision.decided_by,
            now=evaluated_at,
        )

    assert registry.get(FAMILY, P1).promotion_report is None
    assert registry.events() == before
