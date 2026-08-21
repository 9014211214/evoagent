from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evoagent.composite import (
    CompositeComponentDriftError,
    CompositeSnapshotService,
    SQLiteCompositeSnapshotRegistry,
)
from evoagent.local_policy import build_initial_local_policy_manifest
from evoagent.local_policy.models import (
    LocalPolicyCandidateManifest,
    LocalPolicyVersionRecord,
    LocalPolicyVersionStatus,
)
from evoagent.skills import (
    SkillSpec,
    SkillVersionRecord,
    SkillVersionStatus,
    skill_content_hash,
)


LINEAGE = "composite-lineage:service-v2.3"
SKILL_ID = "document_guard"
POLICY_FAMILY = "local-policy-family:document-guard-service"
FROZEN = {
    "runtime_hash": "1" * 64,
    "tool_contract_hash": "2" * 64,
    "verifier_hash": "3" * 64,
    "task_manifest_hash": "4" * 64,
    "budget_hash": "5" * 64,
}


class MutableSkillRegistry:
    def __init__(self, record):
        self.record = record
        self.revision = 0

    def active(self, skill_id):
        assert skill_id == SKILL_ID
        return self.record

    def active_revision(self, skill_id):
        assert skill_id == SKILL_ID
        return self.revision

    def set_active(self, record):
        self.record = record
        self.revision += 1


class MutablePolicyRegistry:
    def __init__(self, record):
        self.record = record
        self.revision = 0

    def active(self, family_id):
        assert family_id == POLICY_FAMILY
        return self.record

    def head(self, family_id):
        assert family_id == POLICY_FAMILY
        return SimpleNamespace(revision=self.revision)

    def set_active(self, record):
        self.record = record
        self.revision += 1


def _skill(version: str, rules: tuple[str, ...]):
    spec = SkillSpec(
        skill_id=SKILL_ID,
        name="Document Guard",
        version=version,
        description="Govern document writes.",
        rules=rules,
    )
    return SkillVersionRecord(
        spec=spec,
        parent_version=None if version == "0.1.0" else "0.1.0",
        status=SkillVersionStatus.ACTIVE,
        content_hash=skill_content_hash(spec),
    )


def _initial_policy(now):
    manifest = build_initial_local_policy_manifest(
        family_id=POLICY_FAMILY,
        policy_id="local-policy:service:p0",
        checkpoint_hash="a" * 64,
        optimizer_config_hash="b" * 64,
        source_commit="c" * 40,
        created_by="policy-bootstrap-owner",
        created_at=now,
    )
    return LocalPolicyVersionRecord(
        family_id=POLICY_FAMILY,
        policy_id=manifest.policy_id,
        manifest=manifest,
        parent_policy_id=None,
        status=LocalPolicyVersionStatus.ACTIVE,
        created_at=now,
    )


def _evolved_policy(now):
    manifest = LocalPolicyCandidateManifest.model_construct(
        family_id=POLICY_FAMILY,
        candidate_id="local-policy:service:p1",
        base_policy_id="local-policy:service:p0",
        selected_checkpoint_hash="d" * 64,
    )
    return LocalPolicyVersionRecord.model_construct(
        family_id=POLICY_FAMILY,
        policy_id=manifest.candidate_id,
        manifest=manifest,
        parent_policy_id=manifest.base_policy_id,
        status=LocalPolicyVersionStatus.ACTIVE,
        created_at=now,
    )


def _service(tmp_path, start):
    skill_registry = MutableSkillRegistry(
        _skill("0.1.0", ("write_document",))
    )
    policy_registry = MutablePolicyRegistry(_initial_policy(start))
    composite_registry = SQLiteCompositeSnapshotRegistry(
        tmp_path / "composite.db"
    )
    service = CompositeSnapshotService(
        composite_registry,
        skill_registry=skill_registry,
        local_policy_registry=policy_registry,
        skill_id=SKILL_ID,
        local_policy_family_id=POLICY_FAMILY,
    )
    service.register_initial_from_components(
        lineage_id=LINEAGE,
        snapshot_id="composite-snapshot:service:a0",
        actor_id="composite-service-bootstrap-owner",
        created_at=start,
        **FROZEN,
    )
    return service, skill_registry, policy_registry, composite_registry


def test_service_commits_skill_then_policy_from_live_pointers(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    service, skills, policies, registry = _service(tmp_path, start)

    skills.set_active(
        _skill(
            "0.2.0",
            ("inspect_before_write", "write_document"),
        )
    )
    a1 = service.build_child_from_components(
        lineage_id=LINEAGE,
        snapshot_id="composite-snapshot:service:a1",
        expected_component="skill",
        source_case_ids=("case:service-skill",),
        source_decision_hashes=("6" * 64,),
        source_package_hashes=("7" * 64,),
        created_by="service-skill-manifest-builder",
        created_at=start + timedelta(seconds=1),
    )
    assert registry.active(LINEAGE).snapshot_id.endswith(":a0")
    service.commit(
        a1,
        expected_active_revision=0,
        actor_id="independent-service-skill-committer",
        now=start + timedelta(seconds=1),
    )

    policies.set_active(_evolved_policy(start + timedelta(seconds=2)))
    a2 = service.build_child_from_components(
        lineage_id=LINEAGE,
        snapshot_id="composite-snapshot:service:a2",
        expected_component="local_policy",
        source_case_ids=(
            "case:service-policy-normal",
            "case:service-policy-protected",
        ),
        source_decision_hashes=("8" * 64,),
        source_package_hashes=("9" * 64,),
        created_by="service-policy-manifest-builder",
        created_at=start + timedelta(seconds=2),
    )
    service.commit(
        a2,
        expected_active_revision=1,
        actor_id="independent-service-policy-committer",
        now=start + timedelta(seconds=2),
    )

    assert registry.active(LINEAGE).snapshot_id.endswith(":a2")
    assert registry.verify_state(LINEAGE) is True


def test_attribution_track_must_match_the_live_component_change(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    service, skills, _, registry = _service(tmp_path, start)
    skills.set_active(
        _skill(
            "0.2.0",
            ("inspect_before_write", "write_document"),
        )
    )

    with pytest.raises(
        CompositeComponentDriftError,
        match="attributed intervention",
    ):
        service.build_child_from_components(
            lineage_id=LINEAGE,
            snapshot_id="composite-snapshot:service:wrong-track",
            expected_component="local_policy",
            source_case_ids=("case:wrong-track",),
            source_decision_hashes=("a" * 64,),
            source_package_hashes=("b" * 64,),
            created_by="wrong-track-manifest-builder",
            created_at=start + timedelta(seconds=1),
        )

    assert registry.active(LINEAGE).snapshot_id.endswith(":a0")


def test_manifest_becomes_invalid_when_live_component_moves_again(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    service, skills, _, registry = _service(tmp_path, start)
    skills.set_active(
        _skill(
            "0.2.0",
            ("inspect_before_write", "write_document"),
        )
    )
    manifest = service.build_child_from_components(
        lineage_id=LINEAGE,
        snapshot_id="composite-snapshot:service:stale-manifest",
        expected_component="skill",
        source_case_ids=("case:stale-live-skill",),
        source_decision_hashes=("c" * 64,),
        source_package_hashes=("d" * 64,),
        created_by="stale-live-manifest-builder",
        created_at=start + timedelta(seconds=1),
    )

    skills.set_active(
        _skill(
            "0.3.0",
            (
                "inspect_before_write",
                "confirm_sensitive_write",
                "write_document",
            ),
        )
    )
    with pytest.raises(
        CompositeComponentDriftError,
        match="live component pointers",
    ):
        service.commit(
            manifest,
            expected_active_revision=0,
            actor_id="independent-stale-live-committer",
            now=start + timedelta(seconds=2),
        )

    assert registry.active(LINEAGE).snapshot_id.endswith(":a0")
