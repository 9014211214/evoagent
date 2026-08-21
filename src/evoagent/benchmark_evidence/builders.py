from __future__ import annotations

from evoagent.benchmark_evidence.models import (
    BenchmarkAgentIdentity,
    BenchmarkEvidenceSource,
    BenchmarkExecutionBudget,
    BenchmarkModelIdentity,
    BenchmarkRunContract,
    BenchmarkRunRole,
    BenchmarkSuiteIdentity,
    BenchmarkTaskIdentity,
    HARBOR_REVIEWED_COMMIT,
    TERMINAL_BENCH_2_1,
    TERMINAL_BENCH_2_1_REVIEWED_COMMIT,
)
from evoagent.model_registry.models import canonical_sha256


def build_benchmark_suite(
    *,
    suite_id: str,
    tasks: tuple[BenchmarkTaskIdentity, ...],
    primary_reward_key: str = "reward",
    canonical_task_manifest_attested: bool = False,
) -> BenchmarkSuiteIdentity:
    payload = {
        "suite_id": suite_id,
        "dataset_ref": TERMINAL_BENCH_2_1,
        "harbor_reviewed_commit": HARBOR_REVIEWED_COMMIT,
        "benchmark_reviewed_commit": TERMINAL_BENCH_2_1_REVIEWED_COMMIT,
        "primary_reward_key": primary_reward_key,
        "tasks": tasks,
        "canonical_task_manifest_attested": canonical_task_manifest_attested,
    }
    return BenchmarkSuiteIdentity(
        **payload,
        suite_hash=canonical_sha256(payload),
    )


def build_agent_identity(
    *,
    family_id: str,
    name: str,
    version: str,
    source_commit: str,
    config_sha256: str,
    snapshot_id: str,
    evolution_round: int,
    parent_snapshot_id: str | None,
) -> BenchmarkAgentIdentity:
    payload = {
        "family_id": family_id,
        "name": name,
        "version": version,
        "source_commit": source_commit,
        "config_sha256": config_sha256,
        "snapshot_id": snapshot_id,
        "evolution_round": evolution_round,
        "parent_snapshot_id": parent_snapshot_id,
    }
    return BenchmarkAgentIdentity(
        **payload,
        identity_hash=canonical_sha256(payload),
    )


def build_model_identity(
    *,
    provider: str,
    name: str,
    revision: str,
    config_sha256: str,
    inference_settings_sha256: str,
) -> BenchmarkModelIdentity:
    payload = {
        "provider": provider,
        "name": name,
        "revision": revision,
        "config_sha256": config_sha256,
        "inference_settings_sha256": inference_settings_sha256,
        "external_bytes_verified_by_evoagent": False,
    }
    return BenchmarkModelIdentity(
        **payload,
        identity_hash=canonical_sha256(payload),
    )


def build_run_contract(
    *,
    contract_id: str,
    role: BenchmarkRunRole,
    suite: BenchmarkSuiteIdentity,
    agent: BenchmarkAgentIdentity,
    model: BenchmarkModelIdentity,
    reasoning_effort: str,
    trials_per_task: int,
    max_wall_seconds: int,
    max_cost_usd: float,
    source: BenchmarkEvidenceSource,
    timeout_multiplier: float = 1.0,
    agent_timeout_override: bool = False,
    verifier_timeout_override: bool = False,
    resource_overrides: bool = False,
    upload: bool = False,
    public: bool = False,
    harbor_hub_job_uri: str | None = None,
    trajectories_available: bool = False,
    default_execution_settings_attested: bool = False,
) -> BenchmarkRunContract:
    budget = BenchmarkExecutionBudget(
        max_trials=len(suite.tasks) * trials_per_task,
        max_wall_seconds=max_wall_seconds,
        max_cost_usd=max_cost_usd,
    )
    payload = {
        "contract_id": contract_id,
        "role": role,
        "suite": suite,
        "agent": agent,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "trials_per_task": trials_per_task,
        "timeout_multiplier": timeout_multiplier,
        "agent_timeout_override": agent_timeout_override,
        "verifier_timeout_override": verifier_timeout_override,
        "resource_overrides": resource_overrides,
        "upload": upload,
        "public": public,
        "harbor_hub_job_uri": harbor_hub_job_uri,
        "trajectories_available": trajectories_available,
        "default_execution_settings_attested": (
            default_execution_settings_attested
        ),
        "source": source,
        "execution_budget": budget,
    }
    return BenchmarkRunContract(
        **payload,
        contract_hash=canonical_sha256(payload),
    )


def frozen_benchmark_contract_payload(
    contract: BenchmarkRunContract,
) -> dict:
    """Return every field that must remain fixed for fair comparison.

    Agent identity, run role, upload location, and contract ID are intentionally
    excluded. The suite, exact Model, reasoning/inference settings, trial count,
    execution budget, timeouts, resource settings, and evidence source remain
    frozen.
    """

    return {
        "suite": contract.suite,
        "model": contract.model,
        "reasoning_effort": contract.reasoning_effort,
        "trials_per_task": contract.trials_per_task,
        "timeout_multiplier": contract.timeout_multiplier,
        "agent_timeout_override": contract.agent_timeout_override,
        "verifier_timeout_override": contract.verifier_timeout_override,
        "resource_overrides": contract.resource_overrides,
        "default_execution_settings_attested": (
            contract.default_execution_settings_attested
        ),
        "source": contract.source,
        "execution_budget": contract.execution_budget,
    }


def frozen_benchmark_contract_hash(
    contract: BenchmarkRunContract,
) -> str:
    return canonical_sha256(frozen_benchmark_contract_payload(contract))


__all__ = [
    "build_agent_identity",
    "build_benchmark_suite",
    "build_model_identity",
    "build_run_contract",
    "frozen_benchmark_contract_hash",
    "frozen_benchmark_contract_payload",
]
