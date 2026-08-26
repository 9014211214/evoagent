from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.benchmarks.models import ResourceBudget, ResourceUsage
from evoagent.continual.models import (
    ContinualComponent,
    ContinualTaskRole,
    UnifiedAgentSnapshot,
)
from evoagent.model_registry.models import canonical_sha256


_HASH = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class BenchmarkAgentScope(str, Enum):
    SKILL_COMPONENT = "skill_component"
    FULL_AGENT = "full_agent"


class FullAgentBenchmarkManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: str = Field(pattern=_SAFE_ID)
    benchmark_id: str = Field(pattern=_SAFE_ID)
    benchmark_revision: str = Field(pattern=_SAFE_ID)
    task_roles: dict[str, ContinualTaskRole]
    task_hashes: dict[str, str]
    model_id: str = Field(pattern=_SAFE_ID)
    seed: str = Field(pattern=_SAFE_ID)
    inference_config_hash: str = Field(pattern=_HASH)
    runtime_hash: str = Field(pattern=_HASH)
    tool_contract_hash: str = Field(pattern=_HASH)
    verifier_hash: str = Field(pattern=_HASH)
    trials_per_task: int = Field(default=1, gt=0)
    updates_allowed_during_evaluation: bool = False
    manifest_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_manifest(self):
        if not self.task_roles:
            raise ValueError("Full-Agent benchmark manifest requires Tasks.")
        if len(self.task_roles) > 100_000 or any(
            re.fullmatch(_SAFE_ID, task_id) is None for task_id in self.task_roles
        ):
            raise ValueError("Full-Agent benchmark Task IDs are not bounded safe IDs.")
        if set(self.task_roles.values()) != set(ContinualTaskRole):
            raise ValueError("Full-Agent benchmark must cover every continual role.")
        if set(self.task_hashes) != set(self.task_roles) or any(
            re.fullmatch(_HASH, value) is None for value in self.task_hashes.values()
        ):
            raise ValueError("Full-Agent benchmark requires one exact hash per Task.")
        if self.updates_allowed_during_evaluation:
            raise ValueError("Full-Agent benchmark evaluation must be frozen.")
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("Full-Agent benchmark manifest hash mismatch.")
        return self


class FullAgentBenchmarkTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(pattern=_SAFE_ID)
    task_hash: str = Field(pattern=_HASH)
    role: ContinualTaskRole
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    safety_violation_count: int = Field(ge=0)
    snapshot_hash: str = Field(pattern=_HASH)
    skill_hash: str = Field(pattern=_HASH)
    router_hash: str = Field(pattern=_HASH)
    memory_hash: str = Field(pattern=_HASH)
    policy_hash: str = Field(pattern=_HASH)
    observable_trace_hash: str = Field(pattern=_HASH)
    result_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_result(self):
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Full-Agent benchmark Task result hash mismatch.")
        return self


class FullAgentBenchmarkBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter_id: str = Field(pattern=_SAFE_ID)
    adapter_scope: BenchmarkAgentScope
    manifest_hash: str = Field(pattern=_HASH)
    snapshot_hash: str = Field(pattern=_HASH)
    source_result_sha256: str = Field(pattern=_HASH)
    task_results: tuple[FullAgentBenchmarkTaskResult, ...]
    usage: ResourceUsage
    batch_hash: str = Field(pattern=_HASH)
    external_execution_performed: Literal[True] = True
    synthetic_fixture: Literal[False] = False
    official_submission_performed: Literal[False] = False
    official_leaderboard_claimed: Literal[False] = False

    @model_validator(mode="after")
    def validate_batch(self):
        if self.adapter_scope != BenchmarkAgentScope.FULL_AGENT:
            raise ValueError("A Full-Agent batch cannot claim a component-only scope.")
        task_ids = [item.task_id for item in self.task_results]
        if not task_ids or len(set(task_ids)) != len(task_ids):
            raise ValueError("Full-Agent batch Task IDs must be non-empty and unique.")
        payload = self.model_dump(mode="json", exclude={"batch_hash"})
        if self.batch_hash != canonical_sha256(payload):
            raise ValueError("Full-Agent benchmark batch hash mismatch.")
        return self


class FullAgentBenchmarkAdapter(ABC):
    """Third-party benchmarks implement this outside the EvoAgent core."""

    scope: BenchmarkAgentScope = BenchmarkAgentScope.FULL_AGENT

    @abstractmethod
    def evaluate(
        self,
        snapshot: UnifiedAgentSnapshot,
        manifest: FullAgentBenchmarkManifest,
        budget: ResourceBudget,
    ) -> FullAgentBenchmarkBatch:
        raise NotImplementedError


class FullAgentBenchmarkProtocol:
    """Fail closed unless external evidence binds every Agent component."""

    def evaluate(
        self,
        snapshot: UnifiedAgentSnapshot,
        manifest: FullAgentBenchmarkManifest,
        budget: ResourceBudget,
        adapter: FullAgentBenchmarkAdapter,
    ) -> FullAgentBenchmarkBatch:
        if adapter.scope != BenchmarkAgentScope.FULL_AGENT:
            raise ValueError("Component-only adapter cannot evaluate the complete Agent.")
        if snapshot.model_id != manifest.model_id:
            raise ValueError("Full-Agent benchmark changed the frozen model.")
        for key in ("runtime_hash", "tool_contract_hash", "verifier_hash"):
            if getattr(snapshot, key) != getattr(manifest, key):
                raise ValueError("Full-Agent benchmark runtime contract drifted.")
        before = snapshot.model_dump_json()
        batch = adapter.evaluate(snapshot, manifest, budget)
        if before != snapshot.model_dump_json():
            raise RuntimeError("Benchmark adapter mutated a frozen Agent snapshot.")
        if batch.adapter_scope != BenchmarkAgentScope.FULL_AGENT:
            raise ValueError("Benchmark batch is not Full-Agent evidence.")
        if batch.manifest_hash != manifest.manifest_hash:
            raise ValueError("Benchmark batch used another manifest.")
        if batch.snapshot_hash != snapshot.snapshot_hash:
            raise ValueError("Benchmark batch used another Agent snapshot.")
        if set(item.task_id for item in batch.task_results) != set(manifest.task_roles):
            raise ValueError("Benchmark batch Task set differs from the frozen manifest.")
        component_hashes = snapshot.component_hashes
        expected = {
            "skill_hash": component_hashes[ContinualComponent.SKILL],
            "router_hash": component_hashes[ContinualComponent.ROUTER],
            "memory_hash": component_hashes[ContinualComponent.MEMORY],
            "policy_hash": component_hashes[ContinualComponent.POLICY],
        }
        for item in batch.task_results:
            if item.role != manifest.task_roles[item.task_id]:
                raise ValueError("Benchmark Task role differs from the frozen manifest.")
            if item.task_hash != manifest.task_hashes[item.task_id]:
                raise ValueError("Benchmark Task result used another frozen Task payload.")
            if item.snapshot_hash != snapshot.snapshot_hash:
                raise ValueError("Benchmark Task result used another snapshot.")
            if any(getattr(item, key) != value for key, value in expected.items()):
                raise ValueError("Benchmark Task result does not bind every Agent component.")
        expected_trials = len(manifest.task_roles) * manifest.trials_per_task
        if batch.usage.task_trials != expected_trials or not batch.usage.fits(budget):
            raise ValueError("Benchmark batch usage differs from the frozen budget.")
        return batch


class SkillEvolBenchBridgeDescriptor(BaseModel):
    """Truthful scope declaration for the existing pinned strategy bridge."""

    model_config = ConfigDict(frozen=True)

    bridge_id: str = "evoagent-skillevolbench-strategy-v1"
    scope: BenchmarkAgentScope = BenchmarkAgentScope.SKILL_COMPONENT
    evaluated_components: tuple[ContinualComponent, ...] = (ContinualComponent.SKILL,)
    full_agent_claim_permitted: bool = False

    def require_full_agent(self) -> None:
        raise ValueError(
            "The pinned SkillEvolBench bridge replaces only Skill evolution; "
            "it cannot validate Router, Memory, Agent Policy, or the full EvoAgent loop."
        )


def build_full_agent_benchmark_manifest(**values) -> FullAgentBenchmarkManifest:
    payload = dict(values)
    return FullAgentBenchmarkManifest(
        **payload,
        manifest_hash=canonical_sha256(payload),
    )


__all__ = [
    "BenchmarkAgentScope",
    "FullAgentBenchmarkAdapter",
    "FullAgentBenchmarkBatch",
    "FullAgentBenchmarkManifest",
    "FullAgentBenchmarkProtocol",
    "FullAgentBenchmarkTaskResult",
    "SkillEvolBenchBridgeDescriptor",
    "build_full_agent_benchmark_manifest",
]
