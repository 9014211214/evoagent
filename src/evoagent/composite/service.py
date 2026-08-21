from __future__ import annotations

from datetime import datetime

from .builders import (
    build_composite_snapshot_manifest,
    build_local_policy_component_binding,
    build_skill_component_binding,
    changed_component,
)
from .models import CompositeSnapshotManifest, CompositeSnapshotRecord
from .repository import SQLiteCompositeSnapshotRegistry


class CompositeComponentDriftError(RuntimeError):
    pass


class CompositeSnapshotService:
    """Bind explicit composite commits to the two live component Registries."""

    def __init__(
        self,
        registry: SQLiteCompositeSnapshotRegistry,
        *,
        skill_registry,
        local_policy_registry,
        skill_id: str,
        local_policy_family_id: str,
    ):
        self.registry = registry
        self.skill_registry = skill_registry
        self.local_policy_registry = local_policy_registry
        self.skill_id = skill_id
        self.local_policy_family_id = local_policy_family_id

    def register_initial_from_components(
        self,
        *,
        lineage_id: str,
        snapshot_id: str,
        runtime_hash: str,
        tool_contract_hash: str,
        verifier_hash: str,
        task_manifest_hash: str,
        budget_hash: str,
        actor_id: str,
        created_at: datetime,
    ) -> CompositeSnapshotRecord:
        manifest = build_composite_snapshot_manifest(
            lineage_id=lineage_id,
            snapshot_id=snapshot_id,
            skill=self._live_skill_binding(),
            local_policy=self._live_local_policy_binding(),
            runtime_hash=runtime_hash,
            tool_contract_hash=tool_contract_hash,
            verifier_hash=verifier_hash,
            task_manifest_hash=task_manifest_hash,
            budget_hash=budget_hash,
            created_by=actor_id,
            created_at=created_at,
        )
        return self.registry.register_initial(
            manifest,
            actor_id=actor_id,
            now=created_at,
        )

    def build_child_from_components(
        self,
        *,
        lineage_id: str,
        snapshot_id: str,
        expected_component: str,
        source_case_ids: tuple[str, ...],
        source_decision_hashes: tuple[str, ...],
        source_package_hashes: tuple[str, ...],
        created_by: str,
        created_at: datetime,
    ) -> CompositeSnapshotManifest:
        parent = self.registry.active(lineage_id).manifest
        manifest = build_composite_snapshot_manifest(
            lineage_id=lineage_id,
            snapshot_id=snapshot_id,
            parent=parent,
            skill=self._live_skill_binding(),
            local_policy=self._live_local_policy_binding(),
            runtime_hash=parent.runtime_hash,
            tool_contract_hash=parent.tool_contract_hash,
            verifier_hash=parent.verifier_hash,
            task_manifest_hash=parent.task_manifest_hash,
            budget_hash=parent.budget_hash,
            source_case_ids=source_case_ids,
            source_decision_hashes=source_decision_hashes,
            source_package_hashes=source_package_hashes,
            created_by=created_by,
            created_at=created_at,
        )
        actual = changed_component(parent, manifest)
        if actual != expected_component:
            raise CompositeComponentDriftError(
                "Live component change differs from the attributed intervention."
            )
        return manifest

    def commit(
        self,
        manifest: CompositeSnapshotManifest,
        *,
        expected_active_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> CompositeSnapshotRecord:
        live_skill = self._live_skill_binding()
        live_policy = self._live_local_policy_binding()
        if manifest.skill != live_skill or manifest.local_policy != live_policy:
            raise CompositeComponentDriftError(
                "Composite manifest differs from the live component pointers."
            )
        return self.registry.commit(
            manifest,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            now=now,
        )

    def _live_skill_binding(self):
        record = self.skill_registry.active(self.skill_id)
        revision = self.skill_registry.active_revision(self.skill_id)
        return build_skill_component_binding(
            record,
            active_revision=revision,
        )

    def _live_local_policy_binding(self):
        record = self.local_policy_registry.active(
            self.local_policy_family_id
        )
        revision = self.local_policy_registry.head(
            self.local_policy_family_id
        ).revision
        return build_local_policy_component_binding(
            record,
            active_revision=revision,
        )


__all__ = [
    "CompositeComponentDriftError",
    "CompositeSnapshotService",
]
