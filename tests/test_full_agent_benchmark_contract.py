from pathlib import Path

import pytest

from evoagent.benchmarks.full_agent import (
    BenchmarkAgentScope,
    FullAgentBenchmarkAdapter,
    FullAgentBenchmarkBatch,
    FullAgentBenchmarkProtocol,
    FullAgentBenchmarkTaskResult,
    SkillEvolBenchBridgeDescriptor,
    build_full_agent_benchmark_manifest,
)
from evoagent.benchmarks.models import ResourceBudget, ResourceUsage
from evoagent.continual import ContinualComponent, ContinualTaskRole
from evoagent.lab import UnifiedContinualEvolutionLab
from evoagent.model_registry.models import canonical_sha256


class _BoundFullAgentAdapter(FullAgentBenchmarkAdapter):
    def evaluate(self, snapshot, manifest, budget):
        hashes = snapshot.component_hashes
        results = []
        for task_id, role in manifest.task_roles.items():
            payload = {
                "task_id": task_id,
                "role": role,
                "score": 1.0,
                "passed": True,
                "safety_violation_count": 0,
                "snapshot_hash": snapshot.snapshot_hash,
                "skill_hash": hashes[ContinualComponent.SKILL],
                "router_hash": hashes[ContinualComponent.ROUTER],
                "memory_hash": hashes[ContinualComponent.MEMORY],
                "policy_hash": hashes[ContinualComponent.POLICY],
                "observable_trace_hash": canonical_sha256((task_id, snapshot.snapshot_hash)),
            }
            results.append(
                FullAgentBenchmarkTaskResult(
                    **payload,
                    result_hash=canonical_sha256(payload),
                )
            )
        payload = {
            "adapter_id": "test-full-agent-adapter",
            "adapter_scope": BenchmarkAgentScope.FULL_AGENT,
            "manifest_hash": manifest.manifest_hash,
            "snapshot_hash": snapshot.snapshot_hash,
            "task_results": tuple(results),
            "usage": ResourceUsage(task_trials=len(results)),
        }
        return FullAgentBenchmarkBatch(**payload, batch_hash=canonical_sha256(payload))


def test_full_agent_protocol_requires_every_component_binding(tmp_path: Path):
    snapshot = UnifiedContinualEvolutionLab(tmp_path)._initial_snapshot()
    roles = {
        "retention-1": ContinualTaskRole.RETENTION,
        "transfer-1": ContinualTaskRole.TRANSFER,
        "adversarial-1": ContinualTaskRole.ADVERSARIAL,
        "composition-1": ContinualTaskRole.COMPOSITION,
    }
    manifest = build_full_agent_benchmark_manifest(
        manifest_id="full-agent-test-v1",
        benchmark_id="example/external-suite",
        benchmark_revision="pinned-commit",
        task_roles=roles,
        model_id=snapshot.model_id,
        seed="A",
        inference_config_hash="1" * 64,
        runtime_hash=snapshot.runtime_hash,
        tool_contract_hash=snapshot.tool_contract_hash,
        verifier_hash=snapshot.verifier_hash,
        trials_per_task=1,
        updates_allowed_during_evaluation=False,
    )
    budget = ResourceBudget(max_task_trials=4)

    batch = FullAgentBenchmarkProtocol().evaluate(
        snapshot,
        manifest,
        budget,
        _BoundFullAgentAdapter(),
    )

    assert batch.adapter_scope == BenchmarkAgentScope.FULL_AGENT
    assert {item.policy_hash for item in batch.task_results} == {
        snapshot.action_policy.policy_hash
    }


def test_skillevolbench_strategy_bridge_cannot_claim_full_agent_evidence():
    descriptor = SkillEvolBenchBridgeDescriptor()
    assert descriptor.scope == BenchmarkAgentScope.SKILL_COMPONENT
    assert descriptor.evaluated_components == (ContinualComponent.SKILL,)
    assert descriptor.full_agent_claim_permitted is False
    with pytest.raises(ValueError, match="replaces only Skill evolution"):
        descriptor.require_full_agent()
