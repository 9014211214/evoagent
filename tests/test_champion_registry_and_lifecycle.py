from datetime import datetime, timezone
import sqlite3

import pytest

from evoagent.benchmark_evidence import BenchmarkComparisonPackageManager
from evoagent.campaigns import CampaignGovernanceService, CampaignState, SQLiteCampaignRepository
from evoagent.champion import (
    ChampionAuditIntegrityError,
    ChampionLifecycleService,
    ChampionVersionStatus,
    SQLiteChampionRegistry,
    StaleChampionRevision,
    build_champion_policy,
)
from evoagent.lab import AuthoritativeBenchmarkEvidenceLab, BenchmarkGatedChampionLab


def prepare_submission(tmp_path):
    benchmark_lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark",
        source_commit="8" * 40,
    )
    benchmark_lab.run()
    package = BenchmarkComparisonPackageManager().load_file(
        benchmark_lab.package_path
    )
    by_id = {item.evidence_id: item for item in package.runs}
    baseline = by_id[package.longitudinal.run_ids[0]]
    registry = SQLiteChampionRegistry(tmp_path / "champions.db")
    registry.register_initial(
        baseline,
        benchmark_package_hash=package.package_hash,
    )
    campaign_repository = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    service = ChampionLifecycleService(
        registry=registry,
        campaign_governance=CampaignGovernanceService(campaign_repository),
    )
    submission = service.evaluate_and_submit(
        package,
        policy=build_champion_policy(),
        decision_id="champion-decision:lifecycle-test",
        decision_actor_id="policy-evaluator",
        decided_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
    )
    return package, baseline, registry, campaign_repository, service, submission


def authorize(service, submission):
    campaign = submission.campaign
    campaign = service.approve(
        campaign.campaign_id,
        actor_id="reviewer-a",
        reason="benchmark review passed",
        expected_revision=campaign.revision,
    )
    campaign = service.approve(
        campaign.campaign_id,
        actor_id="reviewer-b",
        reason="governance review passed",
        expected_revision=campaign.revision,
    )
    return campaign


def test_authorization_does_not_activate_and_explicit_activation_is_optimistic(tmp_path):
    _, baseline, registry, _, service, submission = prepare_submission(tmp_path)
    family_id = baseline.contract.agent.family_id
    campaign = authorize(service, submission)

    assert campaign.state == CampaignState.AUTHORIZED
    assert registry.active(family_id).snapshot_id == baseline.contract.agent.snapshot_id
    assert registry.active_revision(family_id) == 0

    authorized = service.synchronize_authorization(
        family_id=family_id,
        snapshot_id=submission.decision.selected_snapshot_id,
        campaign_id=campaign.campaign_id,
        actor_id="authorization-sync",
    )
    assert authorized.status == ChampionVersionStatus.AUTHORIZED
    assert registry.active(family_id).snapshot_id == baseline.contract.agent.snapshot_id
    assert registry.active_revision(family_id) == 0

    active = service.activate(
        family_id=family_id,
        snapshot_id=submission.decision.selected_snapshot_id,
        campaign_id=campaign.campaign_id,
        expected_active_revision=0,
        actor_id="operator",
    )
    assert active.status == ChampionVersionStatus.CHAMPION
    assert registry.active(family_id).snapshot_id == "evoagent-a1"
    assert registry.active_revision(family_id) == 1

    with pytest.raises(StaleChampionRevision):
        registry.activate(
            family_id,
            "evoagent-a1",
            campaign_id=campaign.campaign_id,
            expected_active_revision=0,
            actor_id="stale-operator",
            reason="stale activation",
        )


def test_explicit_rollback_restores_parent_and_preserves_history(tmp_path):
    _, baseline, registry, _, service, submission = prepare_submission(tmp_path)
    family_id = baseline.contract.agent.family_id
    campaign = authorize(service, submission)
    service.synchronize_authorization(
        family_id=family_id,
        snapshot_id="evoagent-a1",
        campaign_id=campaign.campaign_id,
        actor_id="authorization-sync",
    )
    service.activate(
        family_id=family_id,
        snapshot_id="evoagent-a1",
        campaign_id=campaign.campaign_id,
        expected_active_revision=0,
        actor_id="operator",
    )

    restored = service.rollback(
        family_id=family_id,
        to_snapshot_id=baseline.contract.agent.snapshot_id,
        expected_active_revision=1,
        actor_id="rollback-operator",
        reason="controlled rollback verification",
    )

    assert restored.status == ChampionVersionStatus.CHAMPION
    assert restored.snapshot_id == "evoagent-a0"
    assert registry.active_revision(family_id) == 2
    assert registry.get(family_id, "evoagent-a1").status == (
        ChampionVersionStatus.ROLLED_BACK
    )
    assert len(registry.list_snapshots(family_id)) == 2
    assert registry.events()[-1].event_type.value == "rolled_back"
    assert registry.verify_state() is True


def test_decision_actor_cannot_approve_own_champion_campaign(tmp_path):
    _, _, _, _, service, submission = prepare_submission(tmp_path)
    with pytest.raises(ValueError, match="decision actor"):
        service.approve(
            submission.campaign.campaign_id,
            actor_id=submission.decision.decision_actor_id,
            reason="self approval",
            expected_revision=submission.campaign.revision,
        )


def test_unapproved_or_wrong_campaign_cannot_authorize_or_activate(tmp_path):
    _, baseline, registry, _, service, submission = prepare_submission(tmp_path)
    family_id = baseline.contract.agent.family_id

    with pytest.raises(ValueError, match="not AUTHORIZED"):
        service.synchronize_authorization(
            family_id=family_id,
            snapshot_id="evoagent-a1",
            campaign_id=submission.campaign.campaign_id,
            actor_id="operator",
        )
    with pytest.raises(ValueError):
        registry.activate(
            family_id,
            "evoagent-a1",
            campaign_id="campaign:wrong",
            expected_active_revision=0,
            actor_id="operator",
            reason="unauthorized activation",
        )
    assert registry.active(family_id).snapshot_id == "evoagent-a0"


def test_champion_audit_modification_and_tail_truncation_are_detected(tmp_path):
    lab = BenchmarkGatedChampionLab(
        tmp_path / "tamper-lab",
        source_commit="9" * 40,
    )
    lab.run()
    registry = SQLiteChampionRegistry(lab.champion_database)
    checkpoint = registry.checkpoint()

    with sqlite3.connect(lab.champion_database) as connection:
        connection.execute(
            "UPDATE champion_audit_events SET payload_json = ? WHERE sequence = 1",
            ('{"tampered":true}',),
        )
        connection.commit()
    with pytest.raises(ChampionAuditIntegrityError):
        registry.verify_audit()

    second_lab = BenchmarkGatedChampionLab(
        tmp_path / "tail-lab",
        source_commit="a" * 40,
    )
    second_lab.run()
    second = SQLiteChampionRegistry(second_lab.champion_database)
    tail_checkpoint = second.checkpoint()
    with sqlite3.connect(second_lab.champion_database) as connection:
        connection.execute(
            "DELETE FROM champion_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM champion_audit_events)"
        )
        connection.commit()
    assert second.verify_audit() is True
    with pytest.raises(ChampionAuditIntegrityError):
        second.verify_audit(tail_checkpoint)
    assert checkpoint.event_count == 6
