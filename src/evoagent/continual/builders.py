from __future__ import annotations

import hashlib

from evoagent.domain.models import AgentSnapshot, ExecutionTrace, Skill, Task
from evoagent.model_registry.models import canonical_sha256
from evoagent.skills import SkillSpec

from .models import (
    ActionPolicy,
    ContinualComponent,
    MemoryRecord,
    MemorySnapshot,
    RouterPolicy,
    RouterRule,
    UnifiedAgentSnapshot,
)


def build_router_rule(
    rule_id: str,
    *,
    task_type: str,
    skill_ids: tuple[str, ...],
    required_tags: tuple[str, ...] = (),
    priority: int = 0,
) -> RouterRule:
    payload = {
        "rule_id": rule_id,
        "task_type": task_type,
        "required_tags": required_tags,
        "skill_ids": skill_ids,
        "priority": priority,
    }
    return RouterRule(**payload, rule_hash=canonical_sha256(payload))


def build_router_policy(
    policy_id: str,
    *,
    version: int,
    rules: tuple[RouterRule, ...],
    default_skill_ids: tuple[str, ...],
    parent: RouterPolicy | None = None,
) -> RouterPolicy:
    if parent is None and version != 0:
        raise ValueError("Initial Router policy version must be zero.")
    if parent is not None and (
        policy_id != parent.policy_id or version != parent.version + 1
    ):
        raise ValueError("Router policy lineage must be identity-stable and contiguous.")
    payload = {
        "policy_id": policy_id,
        "version": version,
        "rules": rules,
        "default_skill_ids": default_skill_ids,
        "parent_policy_hash": parent.policy_hash if parent else None,
    }
    return RouterPolicy(**payload, policy_hash=canonical_sha256(payload))


def build_memory_record(
    record_id: str,
    *,
    capability_key: str,
    source_task: Task,
    source_trace: ExecutionTrace,
) -> MemoryRecord:
    if source_trace.task != source_task:
        raise ValueError("Verified Memory Trace belongs to another Task.")
    if not source_trace.verifier_passed:
        raise ValueError("Only a verifier-passed Trace can create Memory.")
    verification = tuple(
        item
        for item in source_trace.observable_events
        if item.get("event") == "verification"
    )
    if len(verification) != 1 or verification[0].get("safety_violations"):
        raise ValueError("Verified Memory requires one safety-clean verification event.")
    observations = tuple(
        item.get("metadata", {})
        for item in source_trace.observable_events
        if item.get("event") == "policy_observation" and item.get("step_index") == 0
    )
    if len(observations) != 1:
        raise ValueError("Verified Memory requires one initial policy observation.")
    selected_skill_ids = tuple(observations[0].get("selected_skill_ids", ()))
    capability_tags = tuple(
        item for item in source_task.tags if item.startswith("capability:")
    )
    if capability_tags != (f"capability:{capability_key}",):
        raise ValueError("Memory capability does not match the source Task.")
    stable_trace = source_trace.model_dump(mode="json")
    stable_trace["cost"] = {
        key: value
        for key, value in stable_trace.get("cost", {}).items()
        if key != "wall_seconds"
    }
    payload = {
        "record_id": record_id,
        "capability_key": capability_key,
        "task_type": source_task.task_type,
        "task_tags": tuple(
            item
            for item in source_task.tags
            if item.startswith(("capability:", "policy:"))
        ),
        "selected_skill_ids": selected_skill_ids,
        "source_task_id_hash": hashlib.sha256(source_task.task_id.encode("utf-8")).hexdigest(),
        "source_trace_hash": canonical_sha256(stable_trace),
        "verifier_passed": True,
    }
    return MemoryRecord(**payload, record_hash=canonical_sha256(payload))


def build_memory_snapshot(
    memory_id: str,
    *,
    version: int,
    records: tuple[MemoryRecord, ...] = (),
    max_records: int = 128,
    parent: MemorySnapshot | None = None,
) -> MemorySnapshot:
    if parent is None and version != 0:
        raise ValueError("Initial Memory version must be zero.")
    if parent is not None and (
        memory_id != parent.memory_id or version != parent.version + 1
    ):
        raise ValueError("Memory lineage must be identity-stable and contiguous.")
    payload = {
        "memory_id": memory_id,
        "version": version,
        "max_records": max_records,
        "records": records,
        "parent_memory_hash": parent.memory_hash if parent else None,
    }
    return MemorySnapshot(**payload, memory_hash=canonical_sha256(payload))


def append_verified_memory(
    memory: MemorySnapshot,
    record: MemoryRecord,
) -> MemorySnapshot:
    existing = {item.record_id: item for item in memory.records}
    if record.record_id in existing:
        if existing[record.record_id] == record:
            return memory
        raise ValueError("Conflicting Memory record ID.")
    records = (*memory.records, record)
    if len(records) > memory.max_records:
        records = records[-memory.max_records :]
    return build_memory_snapshot(
        memory.memory_id,
        version=memory.version + 1,
        records=records,
        max_records=memory.max_records,
        parent=memory,
    )


def build_action_policy(
    policy_id: str,
    *,
    version: int,
    iteration: int,
    state_keys: tuple[str, ...],
    logits: tuple[tuple[float, ...], ...],
    parent: ActionPolicy | None = None,
) -> ActionPolicy:
    if parent is None and (version != 0 or iteration != 0):
        raise ValueError("Initial Action policy version and iteration must be zero.")
    if parent is not None and (
        policy_id != parent.policy_id
        or version != parent.version + 1
        or iteration <= parent.iteration
    ):
        raise ValueError("Action-policy lineage must be identity-stable and contiguous.")
    payload = {
        "policy_id": policy_id,
        "version": version,
        "iteration": iteration,
        "state_keys": state_keys,
        "actions": ("inspect", "write"),
        "logits": logits,
        "parent_policy_hash": parent.policy_hash if parent else None,
        "artifact_kind": "bounded_observable_agent_policy",
        "foundation_model_weights_changed": False,
    }
    return ActionPolicy(**payload, policy_hash=canonical_sha256(payload))


def build_unified_snapshot(
    *,
    lineage_id: str,
    snapshot_id: str,
    round_index: int,
    model_id: str,
    skills: tuple[SkillSpec, ...],
    router: RouterPolicy,
    memory: MemorySnapshot,
    action_policy: ActionPolicy,
    runtime_hash: str,
    tool_contract_hash: str,
    verifier_hash: str,
    creator_id: str,
    parent: UnifiedAgentSnapshot | None = None,
    changed_component: ContinualComponent | None = None,
    evidence_hashes: tuple[str, ...] = (),
) -> UnifiedAgentSnapshot:
    payload = {
        "lineage_id": lineage_id,
        "snapshot_id": snapshot_id,
        "round_index": round_index,
        "parent_snapshot_id": parent.snapshot_id if parent else None,
        "parent_snapshot_hash": parent.snapshot_hash if parent else None,
        "model_id": model_id,
        "skills": skills,
        "router": router,
        "memory": memory,
        "action_policy": action_policy,
        "runtime_hash": runtime_hash,
        "tool_contract_hash": tool_contract_hash,
        "verifier_hash": verifier_hash,
        "creator_id": creator_id,
        "changed_component": changed_component,
        "evidence_hashes": evidence_hashes,
        "foundation_model_weights_changed": False,
        "production_activation_performed": False,
        "external_execution_performed": False,
    }
    snapshot = UnifiedAgentSnapshot(
        **payload,
        snapshot_hash=canonical_sha256(payload),
    )
    if parent is not None:
        validate_one_component_transition(parent, snapshot)
    return snapshot


def validate_one_component_transition(
    parent: UnifiedAgentSnapshot,
    candidate: UnifiedAgentSnapshot,
) -> ContinualComponent:
    if candidate.lineage_id != parent.lineage_id:
        raise ValueError("Candidate belongs to another Agent lineage.")
    if candidate.parent_snapshot_id != parent.snapshot_id:
        raise ValueError("Candidate does not name the exact active parent.")
    if candidate.parent_snapshot_hash != parent.snapshot_hash:
        raise ValueError("Candidate does not bind the exact active parent hash.")
    if candidate.round_index != parent.round_index + 1:
        raise ValueError("Candidate round must be contiguous.")
    if candidate.model_id != parent.model_id:
        raise ValueError("Component evolution cannot change the frozen foundation model.")
    frozen = ("runtime_hash", "tool_contract_hash", "verifier_hash")
    if any(getattr(candidate, key) != getattr(parent, key) for key in frozen):
        raise ValueError("Component evolution changed a frozen runtime contract.")
    changed = tuple(
        component
        for component in ContinualComponent
        if parent.component_hashes[component] != candidate.component_hashes[component]
    )
    if len(changed) != 1:
        raise ValueError("A candidate must change exactly one Agent component.")
    if candidate.changed_component != changed[0]:
        raise ValueError("Declared changed component differs from the candidate payload.")
    component = changed[0]
    if component == ContinualComponent.ROUTER and (
        candidate.router.policy_id != parent.router.policy_id
        or candidate.router.version != parent.router.version + 1
        or candidate.router.parent_policy_hash != parent.router.policy_hash
    ):
        raise ValueError("Router candidate lineage is not contiguous.")
    if component == ContinualComponent.MEMORY and (
        candidate.memory.memory_id != parent.memory.memory_id
        or candidate.memory.version != parent.memory.version + 1
        or candidate.memory.parent_memory_hash != parent.memory.memory_hash
    ):
        raise ValueError("Memory candidate lineage is not contiguous.")
    if component == ContinualComponent.POLICY and (
        candidate.action_policy.policy_id != parent.action_policy.policy_id
        or candidate.action_policy.version != parent.action_policy.version + 1
        or candidate.action_policy.iteration <= parent.action_policy.iteration
        or candidate.action_policy.parent_policy_hash != parent.action_policy.policy_hash
    ):
        raise ValueError("Action-policy candidate lineage is not contiguous.")
    return changed[0]


def to_runtime_snapshot(snapshot: UnifiedAgentSnapshot) -> AgentSnapshot:
    skills = {
        spec.skill_id: Skill(
            skill_id=spec.skill_id,
            name=spec.name,
            version=spec.version,
            description=spec.description,
            rules=list(spec.rules),
            provenance=spec.provenance,
            status="stable",
        )
        for spec in snapshot.skills
    }
    return AgentSnapshot(
        snapshot_id=snapshot.snapshot_id,
        round_index=snapshot.round_index,
        model_id=snapshot.model_id,
        skills=skills,
        harness_version="unified-continual-v1",
        parent_snapshot_id=snapshot.parent_snapshot_id,
        metadata={
            "unified_snapshot_hash": snapshot.snapshot_hash,
            "router_policy_hash": snapshot.router.policy_hash,
            "memory_hash": snapshot.memory.memory_hash,
            "action_policy_hash": snapshot.action_policy.policy_hash,
        },
    )


__all__ = [
    "append_verified_memory",
    "build_action_policy",
    "build_memory_record",
    "build_memory_snapshot",
    "build_router_policy",
    "build_router_rule",
    "build_unified_snapshot",
    "to_runtime_snapshot",
    "validate_one_component_transition",
]
