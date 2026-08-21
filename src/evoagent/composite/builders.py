from __future__ import annotations

from datetime import datetime

from evoagent.local_policy.models import (
    InitialLocalPolicyManifest,
    LocalPolicyCandidateManifest,
    LocalPolicyVersionRecord,
    LocalPolicyVersionStatus,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.skills import (
    SkillVersionRecord,
    SkillVersionStatus,
    skill_content_hash,
)

from .models import (
    CompositeSnapshotManifest,
    LocalPolicyComponentBinding,
    SkillComponentBinding,
)


_FROZEN_CONTRACT_FIELDS = (
    "runtime_hash",
    "tool_contract_hash",
    "verifier_hash",
    "task_manifest_hash",
    "budget_hash",
)


def build_skill_component_binding(
    record: SkillVersionRecord,
    *,
    active_revision: int,
) -> SkillComponentBinding:
    if record.status != SkillVersionStatus.ACTIVE:
        raise ValueError(
            "Composite snapshot requires an ACTIVE Skill version."
        )
    expected_hash = skill_content_hash(record.spec)
    if record.content_hash != expected_hash:
        raise ValueError(
            "Skill component content hash differs from its immutable specification."
        )
    return SkillComponentBinding(
        skill_id=record.spec.skill_id,
        version=record.spec.version,
        content_hash=record.content_hash,
        active_revision=active_revision,
    )


def build_local_policy_component_binding(
    record: LocalPolicyVersionRecord,
    *,
    active_revision: int,
) -> LocalPolicyComponentBinding:
    if record.status != LocalPolicyVersionStatus.ACTIVE:
        raise ValueError(
            "Composite snapshot requires an ACTIVE local-policy version."
        )
    manifest = record.manifest
    if isinstance(manifest, InitialLocalPolicyManifest):
        checkpoint_hash = manifest.checkpoint_hash
    elif isinstance(manifest, LocalPolicyCandidateManifest):
        checkpoint_hash = manifest.selected_checkpoint_hash
    else:
        raise TypeError(
            "Composite snapshot received another local-policy manifest kind."
        )
    return LocalPolicyComponentBinding(
        family_id=record.family_id,
        policy_id=record.policy_id,
        checkpoint_hash=checkpoint_hash,
        active_revision=active_revision,
    )


def changed_component(
    parent: CompositeSnapshotManifest,
    child: CompositeSnapshotManifest,
) -> str:
    if parent.lineage_id != child.lineage_id:
        raise ValueError(
            "Composite child belongs to another lineage."
        )
    if child.parent_snapshot_id != parent.snapshot_id:
        raise ValueError(
            "Composite child does not reference the active direct parent."
        )
    if child.round_index != parent.round_index + 1:
        raise ValueError(
            "Composite snapshot round is not contiguous with its parent."
        )
    for field in _FROZEN_CONTRACT_FIELDS:
        if getattr(child, field) != getattr(parent, field):
            raise ValueError(
                "Composite snapshot changed a frozen runtime or evaluation contract."
            )

    skill_changed = child.skill != parent.skill
    policy_changed = child.local_policy != parent.local_policy
    if skill_changed == policy_changed:
        raise ValueError(
            "Composite child must change exactly one governed component."
        )

    if skill_changed:
        if (
            child.skill.skill_id != parent.skill.skill_id
            or child.skill.active_revision
            != parent.skill.active_revision + 1
            or child.skill.version == parent.skill.version
            or child.skill.content_hash == parent.skill.content_hash
            or child.local_policy != parent.local_policy
        ):
            raise ValueError(
                "Composite Skill transition differs from one exact active revision."
            )
        return "skill"

    if (
        child.local_policy.family_id
        != parent.local_policy.family_id
        or child.local_policy.active_revision
        != parent.local_policy.active_revision + 1
        or child.local_policy.policy_id
        == parent.local_policy.policy_id
        or child.local_policy.checkpoint_hash
        == parent.local_policy.checkpoint_hash
        or child.skill != parent.skill
    ):
        raise ValueError(
            "Composite local-policy transition differs from one exact active revision."
        )
    return "local_policy"


def build_composite_snapshot_manifest(
    *,
    lineage_id: str,
    snapshot_id: str,
    skill: SkillComponentBinding,
    local_policy: LocalPolicyComponentBinding,
    runtime_hash: str,
    tool_contract_hash: str,
    verifier_hash: str,
    task_manifest_hash: str,
    budget_hash: str,
    created_by: str,
    created_at: datetime,
    parent: CompositeSnapshotManifest | None = None,
    source_case_ids: tuple[str, ...] = (),
    source_decision_hashes: tuple[str, ...] = (),
    source_package_hashes: tuple[str, ...] = (),
) -> CompositeSnapshotManifest:
    if parent is None:
        parent_snapshot_id = None
        round_index = 0
    else:
        if lineage_id != parent.lineage_id:
            raise ValueError(
                "Composite snapshot lineage differs from its parent."
            )
        if created_at < parent.created_at:
            raise ValueError(
                "Composite snapshot creation predates its parent."
            )
        parent_snapshot_id = parent.snapshot_id
        round_index = parent.round_index + 1

    payload = {
        "format_version": "evoagent-composite-snapshot-v1",
        "lineage_id": lineage_id,
        "snapshot_id": snapshot_id,
        "parent_snapshot_id": parent_snapshot_id,
        "round_index": round_index,
        "skill": skill,
        "local_policy": local_policy,
        "source_case_ids": tuple(sorted(source_case_ids)),
        "source_decision_hashes": tuple(sorted(source_decision_hashes)),
        "source_package_hashes": tuple(sorted(source_package_hashes)),
        "runtime_hash": runtime_hash,
        "tool_contract_hash": tool_contract_hash,
        "verifier_hash": verifier_hash,
        "task_manifest_hash": task_manifest_hash,
        "budget_hash": budget_hash,
        "created_by": created_by,
        "created_at": created_at,
        "foundation_model_weights_updated": False,
        "production_activation_authorized": False,
        "production_deployment_authorized": False,
        "external_rollout_performed": False,
    }
    manifest = CompositeSnapshotManifest(
        **payload,
        manifest_hash=canonical_sha256(payload),
    )
    if parent is not None:
        changed_component(parent, manifest)
    return manifest


__all__ = [
    "build_composite_snapshot_manifest",
    "build_local_policy_component_binding",
    "build_skill_component_binding",
    "changed_component",
]
