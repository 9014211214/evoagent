from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from evoagent.composite import (
    CompositeAuditIntegrityError,
    CompositeEventType,
    CompositeRegistryConflictError,
    CompositeSnapshotStatus,
    LocalPolicyComponentBinding,
    SQLiteCompositeSnapshotRegistry,
    SkillComponentBinding,
    StaleCompositeRevision,
    build_composite_snapshot_manifest,
    build_local_policy_component_binding,
    build_skill_component_binding,
)
from evoagent.local_policy import build_initial_local_policy_manifest
from evoagent.local_policy.models import (
    LocalPolicyVersionRecord,
    LocalPolicyVersionStatus,
)
from evoagent.skills import (
    SkillSpec,
    SkillVersionRecord,
    SkillVersionStatus,
    skill_content_hash,
)


LINEAGE = "composite-lineage:controlled-v2.3"
A0 = "composite-snapshot:a0"
A1 = "composite-snapshot:a1"
A2 = "composite-snapshot:a2"
FROZEN = {
    "runtime_hash": "1" * 64,
    "tool_contract_hash": "2" * 64,
    "verifier_hash": "3" * 64,
    "task_manifest_hash": "4" * 64,
    "budget_hash": "5" * 64,
}


def _skill(version: str, content_hash: str, revision: int):
    return SkillComponentBinding(
        skill_id="document_guard",
        version=version,
        content_hash=content_hash,
        active_revision=revision,
    )


def _policy(policy_id: str, checkpoint_hash: str, revision: int):
    return LocalPolicyComponentBinding(
        family_id="local-policy-family:document-guard",
        policy_id=policy_id,
        checkpoint_hash=checkpoint_hash,
        active_revision=revision,
    )


def _lineage(start: datetime):
    a0 = build_composite_snapshot_manifest(
        lineage_id=LINEAGE,
        snapshot_id=A0,
        skill=_skill("0.1.0", "a" * 64, 0),
        local_policy=_policy("local-policy:p0", "b" * 64, 0),
        created_by="composite-bootstrap-owner",
        created_at=start,
        **FROZEN,
    )
    a1 = build_composite_snapshot_manifest(
        lineage_id=LINEAGE,
        snapshot_id=A1,
        parent=a0,
        skill=_skill("0.2.0", "c" * 64, 1),
        local_policy=a0.local_policy,
        source_case_ids=("case:protected-document-skill",),
        source_decision_hashes=("d" * 64,),
        source_package_hashes=("e" * 64,),
        created_by="composite-skill-snapshot-builder",
        created_at=start + timedelta(seconds=1),
        **FROZEN,
    )
    a2 = build_composite_snapshot_manifest(
        lineage_id=LINEAGE,
        snapshot_id=A2,
        parent=a1,
        skill=a1.skill,
        local_policy=_policy("local-policy:p1", "f" * 64, 1),
        source_case_ids=(
            "case:normal-document-policy",
            "case:protected-document-policy",
        ),
        source_decision_hashes=("6" * 64,),
        source_package_hashes=("7" * 64, "8" * 64),
        created_by="composite-policy-snapshot-builder",
        created_at=start + timedelta(seconds=2),
        **FROZEN,
    )
    return a0, a1, a2


def test_a0_a1_a2_requires_explicit_composite_pointer_commits(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, a1, a2 = _lineage(start)
    registry = SQLiteCompositeSnapshotRegistry(tmp_path / "composite.db")

    registry.register_initial(
        a0,
        actor_id=a0.created_by,
        now=start,
    )

    # Component evolution evidence alone does not silently change the
    # composite pointer.
    assert registry.active(LINEAGE).snapshot_id == A0
    assert registry.head(LINEAGE).revision == 0

    registry.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=1),
    )
    assert registry.active(LINEAGE).snapshot_id == A1
    assert registry.head(LINEAGE).revision == 1

    registry.commit(
        a2,
        expected_active_revision=1,
        actor_id="independent-composite-policy-committer",
        now=start + timedelta(seconds=2),
    )

    assert registry.active(LINEAGE).snapshot_id == A2
    assert registry.head(LINEAGE).revision == 2
    assert registry.get(LINEAGE, A0).status == (
        CompositeSnapshotStatus.SUPERSEDED
    )
    assert registry.get(LINEAGE, A1).status == (
        CompositeSnapshotStatus.SUPERSEDED
    )
    assert registry.get(LINEAGE, A2).status == CompositeSnapshotStatus.ACTIVE
    assert registry.verify_state(LINEAGE) is True
    assert tuple(item.event_type for item in registry.events(LINEAGE)) == (
        CompositeEventType.REGISTERED,
        CompositeEventType.COMMITTED,
        CompositeEventType.COMMITTED,
    )


def test_exact_commit_retry_is_read_only_and_actor_change_fails(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, a1, _ = _lineage(start)
    registry = SQLiteCompositeSnapshotRegistry(tmp_path / "composite.db")
    registry.register_initial(a0, actor_id=a0.created_by, now=start)
    actor = "independent-composite-skill-committer"
    first = registry.commit(
        a1,
        expected_active_revision=0,
        actor_id=actor,
        now=start + timedelta(seconds=1),
    )
    before = tuple(registry.events(LINEAGE))

    second = registry.commit(
        a1,
        expected_active_revision=0,
        actor_id=actor,
        now=start + timedelta(seconds=3),
    )

    assert first == second
    assert tuple(registry.events(LINEAGE)) == before
    with pytest.raises(CompositeRegistryConflictError):
        registry.commit(
            a1,
            expected_active_revision=0,
            actor_id="different-composite-committer",
            now=start + timedelta(seconds=3),
        )


def test_stale_parent_revision_and_self_commit_fail_closed(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, a1, _ = _lineage(start)
    registry = SQLiteCompositeSnapshotRegistry(tmp_path / "composite.db")
    registry.register_initial(a0, actor_id=a0.created_by, now=start)

    with pytest.raises(StaleCompositeRevision):
        registry.commit(
            a1,
            expected_active_revision=99,
            actor_id="independent-composite-committer",
            now=start + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="creator cannot commit"):
        registry.commit(
            a1,
            expected_active_revision=0,
            actor_id=a1.created_by,
            now=start + timedelta(seconds=1),
        )


def test_wrong_parent_and_two_component_change_are_rejected(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, a1, _ = _lineage(start)
    registry = SQLiteCompositeSnapshotRegistry(tmp_path / "composite.db")
    registry.register_initial(a0, actor_id=a0.created_by, now=start)
    registry.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=1),
    )

    stale_child = build_composite_snapshot_manifest(
        lineage_id=LINEAGE,
        snapshot_id="composite-snapshot:stale-child",
        parent=a0,
        skill=_skill("0.2.1", "9" * 64, 1),
        local_policy=a0.local_policy,
        source_case_ids=("case:stale-parent",),
        source_decision_hashes=("a" * 64,),
        source_package_hashes=("b" * 64,),
        created_by="stale-child-builder",
        created_at=start + timedelta(seconds=2),
        **FROZEN,
    )
    with pytest.raises(StaleCompositeRevision, match="parent"):
        registry.commit(
            stale_child,
            expected_active_revision=1,
            actor_id="stale-child-committer",
            now=start + timedelta(seconds=2),
        )

    with pytest.raises(ValueError, match="exactly one"):
        build_composite_snapshot_manifest(
            lineage_id=LINEAGE,
            snapshot_id="composite-snapshot:invalid-two-component-change",
            parent=a1,
            skill=_skill("0.3.0", "c" * 64, 2),
            local_policy=_policy("local-policy:p1", "d" * 64, 1),
            source_case_ids=("case:invalid-composite",),
            source_decision_hashes=("e" * 64,),
            source_package_hashes=("f" * 64,),
            created_by="invalid-composite-builder",
            created_at=start + timedelta(seconds=2),
            **FROZEN,
        )


def test_component_bindings_recompute_live_registry_hashes():
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    skill_spec = SkillSpec(
        skill_id="document_guard",
        name="Document Guard",
        version="0.1.0",
        description="Inspect before write.",
        rules=("inspect_before_write",),
    )
    skill_record = SkillVersionRecord(
        spec=skill_spec,
        parent_version=None,
        status=SkillVersionStatus.ACTIVE,
        content_hash=skill_content_hash(skill_spec),
    )
    skill_binding = build_skill_component_binding(
        skill_record,
        active_revision=0,
    )
    assert skill_binding.content_hash == skill_content_hash(skill_spec)

    policy_manifest = build_initial_local_policy_manifest(
        family_id="local-policy-family:document-guard",
        policy_id="local-policy:p0",
        checkpoint_hash="1" * 64,
        optimizer_config_hash="2" * 64,
        source_commit="3" * 40,
        created_by="local-policy-bootstrap-owner",
        created_at=now,
    )
    policy_record = LocalPolicyVersionRecord(
        family_id=policy_manifest.family_id,
        policy_id=policy_manifest.policy_id,
        manifest=policy_manifest,
        parent_policy_id=None,
        status=LocalPolicyVersionStatus.ACTIVE,
        created_at=now,
    )
    policy_binding = build_local_policy_component_binding(
        policy_record,
        active_revision=0,
    )
    assert policy_binding.checkpoint_hash == policy_manifest.checkpoint_hash

    forged_skill = skill_record.model_copy(
        update={"content_hash": "f" * 64}
    )
    with pytest.raises(ValueError, match="content hash"):
        build_skill_component_binding(forged_skill, active_revision=0)


def test_audit_modification_and_tail_truncation_are_detected(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    a0, a1, _ = _lineage(start)
    registry = SQLiteCompositeSnapshotRegistry(tmp_path / "composite.db")
    registry.register_initial(a0, actor_id=a0.created_by, now=start)
    registry.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=1),
    )

    checkpoint = registry.checkpoint(LINEAGE)
    with sqlite3.connect(registry.path) as connection:
        connection.execute(
            "UPDATE composite_audit_events SET reason = ? "
            "WHERE lineage_id = ? AND sequence = 2",
            ("forged composite commit semantics", LINEAGE),
        )
        connection.commit()
    with pytest.raises(CompositeAuditIntegrityError, match="modified"):
        registry.verify_audit(LINEAGE, checkpoint)

    # Restore the Registry and then demonstrate that deleting the terminal
    # event cannot make the persisted snapshot lineage valid.
    registry = SQLiteCompositeSnapshotRegistry(tmp_path / "tail.db")
    registry.register_initial(a0, actor_id=a0.created_by, now=start)
    registry.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-composite-skill-committer",
        now=start + timedelta(seconds=1),
    )
    with sqlite3.connect(registry.path) as connection:
        connection.execute(
            "DELETE FROM composite_audit_events "
            "WHERE lineage_id = ? AND sequence = 2",
            (LINEAGE,),
        )
        connection.commit()
    with pytest.raises(CompositeAuditIntegrityError, match="omits"):
        registry.verify_state(LINEAGE)
