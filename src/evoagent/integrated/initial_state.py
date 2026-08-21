from __future__ import annotations

from datetime import datetime
from pathlib import Path

from evoagent.local_policy import (
    LocalPolicyHead,
    LocalPolicyVersionRecord,
    LocalPolicyVersionStatus,
    SQLiteLocalPolicyRegistry,
    build_initial_local_policy_manifest,
)
from evoagent.local_rl import (
    LocalRLCheckpointStatus,
    LocalRLRunManifest,
    LocalPolicyCheckpoint,
    TabularSoftmaxPolicy,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.skills import (
    CONTROLLED_DOCUMENT_SKILL_ID,
    SQLiteSkillRegistry,
    SkillVersionRecord,
    build_controlled_document_skill_v1,
)


CONTROLLED_LOCAL_POLICY_FAMILY_ID = "local-policy-family:accepted-lab"
CONTROLLED_LOCAL_POLICY_INITIAL_ID = "local-policy:accepted:p0"
CONTROLLED_LOCAL_POLICY_CANDIDATE_ID = "local-policy:accepted:p1"
CONTROLLED_LOCAL_POLICY_PREVIEW_ACTOR = "integrated-local-policy-preview-owner"


def controlled_optimizer_config_hash(
    manifest: LocalRLRunManifest,
) -> str:
    """Match the production native projector without executing optimization."""

    return canonical_sha256(
        {
            "environment_contract_hash": manifest.environment.contract_hash,
            "hyperparameter_hash": manifest.hyperparameters.hyperparameter_hash,
            "training_budget_hash": manifest.budget.budget_hash,
        }
    )


def build_controlled_initial_policy_checkpoint(
    manifest: LocalRLRunManifest,
) -> LocalPolicyCheckpoint:
    """Construct P0 only; no rollout, gradient, update, or selection occurs."""

    policy = TabularSoftmaxPolicy.initial(manifest.environment)
    return policy.checkpoint(
        checkpoint_id=f"{manifest.run_id}:checkpoint:0",
        run_id=manifest.run_id,
        iteration=0,
        parent_checkpoint_hash=None,
        status=LocalRLCheckpointStatus.INITIAL,
    )


def build_controlled_initial_policy_record(
    manifest: LocalRLRunManifest,
    *,
    source_commit: str,
    created_at: datetime,
    family_id: str = CONTROLLED_LOCAL_POLICY_FAMILY_ID,
    policy_id: str = CONTROLLED_LOCAL_POLICY_INITIAL_ID,
) -> LocalPolicyVersionRecord:
    checkpoint = build_controlled_initial_policy_checkpoint(manifest)
    initial = build_initial_local_policy_manifest(
        family_id=family_id,
        policy_id=policy_id,
        checkpoint_hash=checkpoint.checkpoint_hash,
        optimizer_config_hash=controlled_optimizer_config_hash(manifest),
        source_commit=source_commit,
        created_by=CONTROLLED_LOCAL_POLICY_PREVIEW_ACTOR,
        created_at=created_at,
    )
    return LocalPolicyVersionRecord(
        family_id=family_id,
        policy_id=policy_id,
        manifest=initial,
        parent_policy_id=None,
        status=LocalPolicyVersionStatus.ACTIVE,
        created_at=created_at,
    )


def prepare_controlled_initial_skill(
    registry: SQLiteSkillRegistry,
    *,
    actor_id: str,
    created_at: datetime,
) -> SkillVersionRecord:
    """Register the exact S0 in the real Skill Registry or verify its retry."""

    spec = build_controlled_document_skill_v1()
    if CONTROLLED_DOCUMENT_SKILL_ID not in registry.list_skill_ids():
        registry.register_initial(
            spec,
            reason="Register canonical S0 before integrated multi-track evolution.",
            actor_id=actor_id,
            now=created_at,
        )
    active = registry.active(CONTROLLED_DOCUMENT_SKILL_ID)
    if active.spec != spec or registry.active_revision(spec.skill_id) != 0:
        raise RuntimeError(
            "Integrated initial Skill Registry differs from canonical S0."
        )
    return active


class PreviewingLocalPolicyRegistry:
    """Read P0 before v2.2 creates its persistent Registry, then delegate."""

    def __init__(
        self,
        actual_registry: SQLiteLocalPolicyRegistry,
        preview_record: LocalPolicyVersionRecord,
    ):
        self.actual_registry = actual_registry
        self.preview_record = preview_record

    def active(self, family_id: str) -> LocalPolicyVersionRecord:
        if family_id != self.preview_record.family_id:
            raise KeyError(f"Unknown preview local-policy family: {family_id}")
        if self.actual_registry.family_exists(family_id):
            return self.actual_registry.active(family_id)
        return self.preview_record.model_copy(deep=True)

    def head(self, family_id: str) -> LocalPolicyHead:
        if family_id != self.preview_record.family_id:
            raise KeyError(f"Unknown preview local-policy family: {family_id}")
        if self.actual_registry.family_exists(family_id):
            return self.actual_registry.head(family_id)
        return LocalPolicyHead(
            family_id=family_id,
            active_policy_id=self.preview_record.policy_id,
            revision=0,
            updated_at=self.preview_record.created_at,
        )

    def verify_actual_parent(self) -> bool:
        """After Promotion, require actual P0 to match the preview binding."""

        family_id = self.preview_record.family_id
        if not self.actual_registry.family_exists(family_id):
            raise RuntimeError(
                "Actual local-policy Registry has not been created by v2.2."
            )
        actual = self.actual_registry.get(
            family_id,
            self.preview_record.policy_id,
        )
        if (
            actual.manifest.checkpoint_hash
            != self.preview_record.manifest.checkpoint_hash
            or actual.manifest.optimizer_config_hash
            != self.preview_record.manifest.optimizer_config_hash
            or actual.manifest.source_commit
            != self.preview_record.manifest.source_commit
            or actual.parent_policy_id is not None
        ):
            raise RuntimeError(
                "Actual v2.2 parent policy differs from the non-training P0 preview."
            )
        return True


__all__ = [
    "CONTROLLED_LOCAL_POLICY_CANDIDATE_ID",
    "CONTROLLED_LOCAL_POLICY_FAMILY_ID",
    "CONTROLLED_LOCAL_POLICY_INITIAL_ID",
    "PreviewingLocalPolicyRegistry",
    "build_controlled_initial_policy_checkpoint",
    "build_controlled_initial_policy_record",
    "controlled_optimizer_config_hash",
    "prepare_controlled_initial_skill",
]
