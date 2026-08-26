from __future__ import annotations

from pathlib import Path

from evoagent.benchmarks.full_agent import build_full_agent_benchmark_manifest
from evoagent.benchmarks.models import ResourceBudget
from evoagent.continual import (
    ContinualTaskRole,
    UnifiedAgentSnapshot,
    build_action_policy,
    build_unified_snapshot,
)
from evoagent.domain.models import Task
from evoagent.lab.unified_continual import UnifiedContinualEvolutionLab
from evoagent.model_registry.models import canonical_sha256

from .full_agent_external import (
    FullAgentExternalRunPlan,
    build_full_agent_external_run_plan,
)


def build_calibration_snapshot(root: str | Path, *, model_id: str) -> UnifiedAgentSnapshot:
    lab_root = Path(root).expanduser().resolve() / "unified-reference"
    lab = UnifiedContinualEvolutionLab(lab_root)
    a0 = lab._initial_snapshot()
    a1 = lab._skill_candidate(a0)
    a2 = lab._memory_candidate(a1)
    reference = lab._router_candidate(a2)
    action_policy = build_action_policy(
        reference.action_policy.policy_id,
        version=reference.action_policy.version + 1,
        iteration=20,
        state_keys=reference.action_policy.state_keys,
        logits=(
            (0.0, 1.0),
            (0.0, 1.0),
            (0.0, 1.0),
            (0.0, 1.0),
            (4.921875, -0.921875),
        ),
        parent=reference.action_policy,
    )
    return build_unified_snapshot(
        lineage_id="external-full-agent-calibration",
        snapshot_id="calibration-A0-complete",
        round_index=0,
        model_id=model_id,
        skills=reference.skills,
        router=reference.router,
        memory=reference.memory,
        action_policy=action_policy,
        runtime_hash=reference.runtime_hash,
        tool_contract_hash=reference.tool_contract_hash,
        verifier_hash=reference.verifier_hash,
        creator_id="calibration-fixture-builder",
        evidence_hashes=(
            canonical_sha256(
                "public deterministic calibration fixture derived from the A4 contract"
            ),
        ),
    )


def build_calibration_task() -> Task:
    return Task(
        task_id="calibration:memory-policy-composition",
        task_type="continual-document",
        input={
            "initial_documents": {},
            "target_path": "calibration/transfer.txt",
            "content": "public synthetic calibration content",
            "expected_status": "completed",
            "require_verification": True,
            "required_observations": ["inspect_before_write", "verify_after_write"],
        },
        expected_outcome={"status": "completed"},
        tags=["policy:adversarial", "capability:write-verify"],
    )


def build_contract_dry_run_plan(
    root: str | Path,
    *,
    model_id: str,
) -> FullAgentExternalRunPlan:
    snapshot = build_calibration_snapshot(root, model_id=model_id)
    tasks = {
        "contract:retention": ContinualTaskRole.RETENTION,
        "contract:transfer": ContinualTaskRole.TRANSFER,
        "contract:adversarial": ContinualTaskRole.ADVERSARIAL,
        "contract:composition": ContinualTaskRole.COMPOSITION,
    }
    task_hashes = {
        task_id: canonical_sha256(
            {
                "fixture": "credential-free-full-agent-adapter-contract-v1",
                "task_id": task_id,
                "role": role,
            }
        )
        for task_id, role in tasks.items()
    }
    manifest = build_full_agent_benchmark_manifest(
        manifest_id="full-agent-adapter-dry-run-v1",
        benchmark_id="evoagent/full-agent-contract-fixture",
        benchmark_revision="v1",
        task_roles=tasks,
        task_hashes=task_hashes,
        model_id=snapshot.model_id,
        seed="A",
        inference_config_hash=canonical_sha256(
            {
                "temperature": 0,
                "provider_fallbacks": False,
                "updates_allowed": False,
            }
        ),
        runtime_hash=snapshot.runtime_hash,
        tool_contract_hash=snapshot.tool_contract_hash,
        verifier_hash=snapshot.verifier_hash,
        trials_per_task=1,
        updates_allowed_during_evaluation=False,
    )
    budget = ResourceBudget(
        max_task_trials=4,
        max_tokens=40_000,
        max_tool_calls=32,
        max_wall_seconds=1_200,
        max_cost_usd=2.0,
    )
    return build_full_agent_external_run_plan(
        plan_id="full-agent-adapter-dry-run-A",
        snapshot=snapshot,
        manifest=manifest,
        budget=budget,
    )


__all__ = [
    "build_calibration_snapshot",
    "build_calibration_task",
    "build_contract_dry_run_plan",
]
