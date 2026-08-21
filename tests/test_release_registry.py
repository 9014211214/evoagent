import sqlite3

import pytest

from evoagent.lab import ShadowCanaryReleaseLab
from evoagent.release import (
    ReleaseAuditIntegrityError,
    ReleaseEvidencePackageManager,
    ReleaseState,
    SQLiteReleaseRegistry,
    StaleReleaseRevision,
)


def test_release_authorization_and_shadow_start_are_separate(tmp_path):
    result = ShadowCanaryReleaseLab(
        tmp_path / "source", source_commit="c" * 40
    ).run()
    package = ReleaseEvidencePackageManager().load_file(result.passing.package_path)
    registry = SQLiteReleaseRegistry(tmp_path / "fresh.db")
    registry.register_plan(package.plan)
    head = registry.bind_release_campaign(
        package.plan.plan_id,
        "campaign:test-release",
        expected_revision=0,
        actor_id="test",
    )
    assert head.state == ReleaseState.PLANNED
    assert head.candidate_allocation_percent == 0.0
    head = registry.mark_authorized(
        package.plan.plan_id,
        "campaign:test-release",
        expected_revision=head.revision,
        actor_id="test",
    )
    assert head.state == ReleaseState.AUTHORIZED
    assert head.active_stage_id is None
    assert head.primary_snapshot_id == package.plan.incumbent_snapshot_id
    assert head.candidate_allocation_percent == 0.0

    with pytest.raises(StaleReleaseRevision):
        registry.start_shadow(
            package.plan.plan_id,
            expected_revision=head.revision - 1,
            actor_id="test",
        )
    started = registry.start_shadow(
        package.plan.plan_id,
        expected_revision=head.revision,
        actor_id="test",
    )
    assert started.state == ReleaseState.SHADOW
    assert started.active_stage_id == "shadow"
    assert started.candidate_allocation_percent == 0.0
    assert started.primary_snapshot_id == package.plan.incumbent_snapshot_id


def test_release_audit_detects_content_modification(tmp_path):
    root = tmp_path / "release-lab"
    result = ShadowCanaryReleaseLab(root, source_commit="d" * 40).run()
    database = root / "drift" / "release-registry.db"
    registry = SQLiteReleaseRegistry(database)
    assert registry.verify_audit() is True

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE release_audit_events SET reason = ? WHERE sequence = 1",
            ("modified",),
        )
        connection.commit()
    with pytest.raises(ReleaseAuditIntegrityError):
        registry.verify_audit()
    assert result.drift.release_event_count > 0


def test_release_audit_checkpoint_detects_tail_truncation(tmp_path):
    root = tmp_path / "release-lab"
    result = ShadowCanaryReleaseLab(root, source_commit="e" * 40).run()
    package = ReleaseEvidencePackageManager().load_file(result.drift.package_path)
    database = root / "drift" / "release-registry.db"
    registry = SQLiteReleaseRegistry(database)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM release_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM release_audit_events)"
        )
        connection.commit()
    assert registry.verify_audit() is True
    with pytest.raises(ReleaseAuditIntegrityError):
        registry.verify_audit(package.release_checkpoint)