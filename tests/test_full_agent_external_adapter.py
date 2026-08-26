import hashlib
import json
from pathlib import Path

import pytest

from evoagent.benchmarks.full_agent import (
    FullAgentBenchmarkProtocol,
    FullAgentBenchmarkTaskResult,
    build_full_agent_benchmark_manifest,
)
from evoagent.benchmarks.models import ResourceBudget, ResourceUsage
from evoagent.continual import ContinualComponent, ContinualTaskRole
from evoagent.integrations.full_agent_calibration import (
    build_calibration_snapshot,
    build_contract_dry_run_plan,
)
from evoagent.integrations.full_agent_external import (
    FullAgentExternalEvidenceAdapter,
    FullAgentExternalEvidenceError,
    FullAgentExternalResultFile,
)
from evoagent.model_registry.models import canonical_sha256


def _manifest(snapshot):
    roles = {
        "retention-1": ContinualTaskRole.RETENTION,
        "transfer-1": ContinualTaskRole.TRANSFER,
        "adversarial-1": ContinualTaskRole.ADVERSARIAL,
        "composition-1": ContinualTaskRole.COMPOSITION,
    }
    return build_full_agent_benchmark_manifest(
        manifest_id="external-test-v1",
        benchmark_id="example/external-suite",
        benchmark_revision="pinned-commit",
        task_roles=roles,
        task_hashes={task_id: canonical_sha256(task_id) for task_id in roles},
        model_id=snapshot.model_id,
        seed="A",
        inference_config_hash="1" * 64,
        runtime_hash=snapshot.runtime_hash,
        tool_contract_hash=snapshot.tool_contract_hash,
        verifier_hash=snapshot.verifier_hash,
        trials_per_task=1,
        updates_allowed_during_evaluation=False,
    )


def _write_result(root: Path, snapshot, manifest, *, extra=None, usage=None) -> str:
    hashes = snapshot.component_hashes
    results = []
    for task_id, role in manifest.task_roles.items():
        payload = {
            "task_id": task_id,
            "task_hash": manifest.task_hashes[task_id],
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
        "format_version": "evoagent-full-agent-result-v1",
        "execution_id": "external-test-execution",
        "adapter_id": "external-test-adapter",
        "manifest_hash": manifest.manifest_hash,
        "snapshot_hash": snapshot.snapshot_hash,
        "task_results": tuple(results),
        "usage": usage or ResourceUsage(task_trials=4, tool_calls=8),
        "completed": True,
        "external_execution_performed": True,
        "synthetic_fixture": False,
        "official_submission_performed": False,
        "official_leaderboard_claimed": False,
    }
    record = FullAgentExternalResultFile(
        **payload,
        result_hash=canonical_sha256(payload),
    )
    raw_payload = record.model_dump(mode="json")
    if extra:
        raw_payload.update(extra)
    raw = json.dumps(raw_payload, sort_keys=True).encode("utf-8")
    path = root / "full-agent-result.json"
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_external_adapter_imports_caller_hashed_complete_full_agent_result(tmp_path: Path):
    snapshot = build_calibration_snapshot(tmp_path / "snapshot", model_id="model/external")
    manifest = _manifest(snapshot)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source_sha = _write_result(evidence_root, snapshot, manifest)
    adapter = FullAgentExternalEvidenceAdapter(
        evidence_root,
        relative_path="full-agent-result.json",
        expected_sha256=source_sha,
    )

    batch = FullAgentBenchmarkProtocol().evaluate(
        snapshot,
        manifest,
        ResourceBudget(max_task_trials=4, max_tool_calls=8),
        adapter,
    )

    assert batch.source_result_sha256 == source_sha
    assert batch.external_execution_performed is True
    assert batch.synthetic_fixture is False
    assert {item.task_hash for item in batch.task_results} == set(
        manifest.task_hashes.values()
    )


def test_external_adapter_rejects_sha_secret_extra_and_over_budget(tmp_path: Path):
    snapshot = build_calibration_snapshot(tmp_path / "snapshot", model_id="model/external")
    manifest = _manifest(snapshot)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    source_sha = _write_result(evidence_root, snapshot, manifest)

    with pytest.raises(FullAgentExternalEvidenceError, match="SHA-256"):
        FullAgentExternalBenchmark = FullAgentExternalEvidenceAdapter(
            evidence_root,
            relative_path="full-agent-result.json",
            expected_sha256="0" * 64,
        )
        FullAgentExternalBenchmark.evaluate(
            snapshot, manifest, ResourceBudget(max_task_trials=4)
        )

    raw = (evidence_root / "full-agent-result.json").read_text(encoding="utf-8")
    secret_raw = raw[:-1] + ',"api_key":"sk-examplecredential123456"}'
    (evidence_root / "full-agent-result.json").write_text(secret_raw, encoding="utf-8")
    secret_sha = hashlib.sha256(secret_raw.encode()).hexdigest()
    with pytest.raises(FullAgentExternalEvidenceError, match="credential"):
        FullAgentExternalEvidenceAdapter(
            evidence_root,
            relative_path="full-agent-result.json",
            expected_sha256=secret_sha,
        ).evaluate(snapshot, manifest, ResourceBudget(max_task_trials=4))

    extra_sha = _write_result(evidence_root, snapshot, manifest, extra={"logs": []})
    with pytest.raises(FullAgentExternalEvidenceError, match="strict observable schema"):
        FullAgentExternalEvidenceAdapter(
            evidence_root,
            relative_path="full-agent-result.json",
            expected_sha256=extra_sha,
        ).evaluate(snapshot, manifest, ResourceBudget(max_task_trials=4))

    over_sha = _write_result(
        evidence_root,
        snapshot,
        manifest,
        usage=ResourceUsage(task_trials=4, tool_calls=9),
    )
    with pytest.raises(ValueError, match="frozen budget"):
        FullAgentBenchmarkProtocol().evaluate(
            snapshot,
            manifest,
            ResourceBudget(max_task_trials=4, max_tool_calls=8),
            FullAgentExternalEvidenceAdapter(
                evidence_root,
                relative_path="full-agent-result.json",
                expected_sha256=over_sha,
            ),
        )


def test_contract_dry_run_is_non_executing_and_binds_every_component(tmp_path: Path):
    plan = build_contract_dry_run_plan(tmp_path, model_id="xiaomi/mimo-v2.5")
    assert plan.execution_enabled is False
    assert plan.network_access_authorized is False
    assert plan.paid_execution_authorized is False
    assert set(plan.manifest.task_roles.values()) == set(ContinualTaskRole)
    assert plan.snapshot.component_hashes.keys() == set(ContinualComponent)
