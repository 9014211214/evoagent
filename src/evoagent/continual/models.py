from __future__ import annotations

import math
import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.skills import SkillSpec


_HASH = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class ContinualComponent(str, Enum):
    SKILL = "skill"
    ROUTER = "router"
    MEMORY = "memory"
    POLICY = "policy"


class ContinualTaskRole(str, Enum):
    RETENTION = "retention"
    TRANSFER = "transfer"
    ADVERSARIAL = "adversarial"
    COMPOSITION = "composition"


class SnapshotStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class RouterRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(pattern=_SAFE_ID)
    task_type: str = Field(pattern=_SAFE_ID)
    required_tags: tuple[str, ...] = ()
    skill_ids: tuple[str, ...]
    priority: int = Field(default=0, ge=-10_000, le=10_000)
    rule_hash: str = Field(pattern=_HASH)

    @field_validator("required_tags", "skill_ids")
    @classmethod
    def unique_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item or "\x00" in item for item in normalized):
            raise ValueError("Router values must be non-empty and NUL-free.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Router values must be unique.")
        if len(normalized) > 32 or any(
            re.fullmatch(_SAFE_ID, item) is None for item in normalized
        ):
            raise ValueError("Router values must be bounded safe identifiers.")
        return normalized

    @model_validator(mode="after")
    def validate_rule(self):
        if not self.skill_ids:
            raise ValueError("A Router rule must select at least one Skill.")
        payload = self.model_dump(mode="json", exclude={"rule_hash"})
        if self.rule_hash != canonical_sha256(payload):
            raise ValueError("Router rule hash mismatch.")
        return self


class RouterPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID)
    version: int = Field(ge=0)
    rules: tuple[RouterRule, ...]
    default_skill_ids: tuple[str, ...]
    parent_policy_hash: str | None = Field(default=None, pattern=_HASH)
    policy_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_policy(self):
        if len(self.rules) > 256:
            raise ValueError("Router policy exceeds its bounded rule budget.")
        if not self.default_skill_ids:
            raise ValueError("Router policy requires a default Skill route.")
        if len(self.default_skill_ids) > 32 or any(
            re.fullmatch(_SAFE_ID, item) is None for item in self.default_skill_ids
        ):
            raise ValueError("Default Router values must be bounded safe identifiers.")
        if len(set(self.default_skill_ids)) != len(self.default_skill_ids):
            raise ValueError("Default Router Skill IDs must be unique.")
        rule_ids = [item.rule_id for item in self.rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("Router rule IDs must be unique.")
        if self.version == 0 and self.parent_policy_hash is not None:
            raise ValueError("Initial Router policy cannot have a parent.")
        if self.version > 0 and self.parent_policy_hash is None:
            raise ValueError("Evolved Router policy requires a parent hash.")
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Router policy hash mismatch.")
        return self


class MemoryRecord(BaseModel):
    """Bounded observable memory; raw prompts, inputs and trajectories are forbidden."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(pattern=_SAFE_ID)
    capability_key: str = Field(pattern=_SAFE_ID)
    task_type: str = Field(pattern=_SAFE_ID)
    task_tags: tuple[str, ...]
    selected_skill_ids: tuple[str, ...]
    source_task_id_hash: str = Field(pattern=_HASH)
    source_trace_hash: str = Field(pattern=_HASH)
    verifier_passed: Literal[True] = True
    record_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_record(self):
        if not self.selected_skill_ids:
            raise ValueError("Verified memory requires at least one selected Skill.")
        if len(set(self.task_tags)) != len(self.task_tags):
            raise ValueError("Memory tags must be unique.")
        if len(set(self.selected_skill_ids)) != len(self.selected_skill_ids):
            raise ValueError("Memory Skill IDs must be unique.")
        for label, values, limit in (
            ("tags", self.task_tags, 32),
            ("Skill IDs", self.selected_skill_ids, 32),
        ):
            if len(values) > limit or any(
                re.fullmatch(_SAFE_ID, item) is None for item in values
            ):
                raise ValueError(f"Memory {label} must be bounded safe identifiers.")
        payload = self.model_dump(mode="json", exclude={"record_hash"})
        validate_safe_content(payload)
        if self.record_hash != canonical_sha256(payload):
            raise ValueError("Memory record hash mismatch.")
        return self


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    memory_id: str = Field(pattern=_SAFE_ID)
    version: int = Field(ge=0)
    max_records: int = Field(default=128, ge=1, le=10_000)
    records: tuple[MemoryRecord, ...] = ()
    parent_memory_hash: str | None = Field(default=None, pattern=_HASH)
    memory_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_memory(self):
        if len(self.records) > self.max_records:
            raise ValueError("Memory exceeds its frozen record budget.")
        record_ids = [item.record_id for item in self.records]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("Memory record IDs must be unique.")
        if self.version == 0 and self.parent_memory_hash is not None:
            raise ValueError("Initial Memory cannot have a parent.")
        if self.version > 0 and self.parent_memory_hash is None:
            raise ValueError("Evolved Memory requires a parent hash.")
        payload = self.model_dump(mode="json", exclude={"memory_hash"})
        if self.memory_hash != canonical_sha256(payload):
            raise ValueError("Memory snapshot hash mismatch.")
        return self


class ActionPolicy(BaseModel):
    """Small numeric Agent policy used by the reference unified runtime."""

    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID)
    version: int = Field(ge=0)
    iteration: int = Field(ge=0)
    state_keys: tuple[str, ...]
    actions: tuple[Literal["inspect", "write"], ...] = ("inspect", "write")
    logits: tuple[tuple[float, ...], ...]
    parent_policy_hash: str | None = Field(default=None, pattern=_HASH)
    policy_hash: str = Field(pattern=_HASH)
    artifact_kind: Literal["bounded_observable_agent_policy"] = (
        "bounded_observable_agent_policy"
    )
    foundation_model_weights_changed: Literal[False] = False

    @model_validator(mode="after")
    def validate_policy(self):
        if not self.state_keys or len(set(self.state_keys)) != len(self.state_keys):
            raise ValueError("Action-policy state keys must be non-empty and unique.")
        if len(self.state_keys) > 256 or any(
            re.fullmatch(_SAFE_ID, item) is None for item in self.state_keys
        ):
            raise ValueError("Action-policy states must be bounded safe identifiers.")
        if self.actions != ("inspect", "write"):
            raise ValueError("The reference policy action contract changed.")
        if len(self.logits) != len(self.state_keys):
            raise ValueError("Action-policy state/logit dimensions differ.")
        for row in self.logits:
            if len(row) != len(self.actions):
                raise ValueError("Action-policy action/logit dimensions differ.")
            if any(not math.isfinite(value) for value in row):
                raise ValueError("Action-policy logits must be finite.")
            if any(abs(value) > 100.0 for value in row):
                raise ValueError("Action-policy logits exceed the bounded numeric range.")
        if self.version == 0 and self.parent_policy_hash is not None:
            raise ValueError("Initial Action policy cannot have a parent.")
        if self.version > 0 and self.parent_policy_hash is None:
            raise ValueError("Evolved Action policy requires a parent hash.")
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Action-policy hash mismatch.")
        return self


class UnifiedAgentSnapshot(BaseModel):
    """One immutable identity for every component that can affect an Agent run."""

    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID)
    snapshot_id: str = Field(pattern=_SAFE_ID)
    round_index: int = Field(ge=0)
    parent_snapshot_id: str | None = Field(default=None, pattern=_SAFE_ID)
    parent_snapshot_hash: str | None = Field(default=None, pattern=_HASH)
    model_id: str = Field(pattern=_SAFE_ID)
    skills: tuple[SkillSpec, ...]
    router: RouterPolicy
    memory: MemorySnapshot
    action_policy: ActionPolicy
    runtime_hash: str = Field(pattern=_HASH)
    tool_contract_hash: str = Field(pattern=_HASH)
    verifier_hash: str = Field(pattern=_HASH)
    creator_id: str = Field(pattern=_SAFE_ID)
    changed_component: ContinualComponent | None = None
    evidence_hashes: tuple[str, ...] = ()
    snapshot_hash: str = Field(pattern=_HASH)
    foundation_model_weights_changed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    external_execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def validate_snapshot(self):
        if not self.model_id.strip() or "\x00" in self.model_id:
            raise ValueError("Model identity must be explicit and NUL-free.")
        skill_ids = [item.skill_id for item in self.skills]
        if not skill_ids or len(set(skill_ids)) != len(skill_ids):
            raise ValueError("Unified snapshot Skill IDs must be non-empty and unique.")
        known = set(skill_ids)
        routed = set(self.router.default_skill_ids)
        for rule in self.router.rules:
            routed.update(rule.skill_ids)
        if not routed <= known:
            raise ValueError("Router references Skills absent from the snapshot.")
        if any(not set(item.selected_skill_ids) <= known for item in self.memory.records):
            raise ValueError("Memory references Skills absent from the snapshot.")
        if len(set(self.evidence_hashes)) != len(self.evidence_hashes):
            raise ValueError("Snapshot evidence hashes must be unique.")
        if len(self.evidence_hashes) > 128 or any(
            re.fullmatch(_HASH, item) is None for item in self.evidence_hashes
        ):
            raise ValueError("Snapshot evidence must contain bounded SHA-256 values.")
        if self.round_index == 0:
            if (
                self.parent_snapshot_id is not None
                or self.parent_snapshot_hash is not None
                or self.changed_component is not None
            ):
                raise ValueError("Round zero cannot have a parent or changed component.")
        elif (
            self.parent_snapshot_id is None
            or self.parent_snapshot_hash is None
            or self.changed_component is None
        ):
            raise ValueError("Evolved snapshot requires a parent and changed component.")
        payload = self.model_dump(mode="json", exclude={"snapshot_hash"})
        validate_safe_content(payload)
        if self.snapshot_hash != canonical_sha256(payload):
            raise ValueError("Unified Agent snapshot hash mismatch.")
        return self

    @property
    def component_hashes(self) -> dict[ContinualComponent, str]:
        return {
            ContinualComponent.SKILL: canonical_sha256(
                [item.model_dump(mode="json") for item in self.skills]
            ),
            ContinualComponent.ROUTER: self.router.policy_hash,
            ContinualComponent.MEMORY: self.memory.memory_hash,
            ContinualComponent.POLICY: self.action_policy.policy_hash,
        }


__all__ = [
    "ActionPolicy",
    "ContinualComponent",
    "ContinualTaskRole",
    "MemoryRecord",
    "MemorySnapshot",
    "RouterPolicy",
    "RouterRule",
    "SnapshotStatus",
    "UnifiedAgentSnapshot",
]
