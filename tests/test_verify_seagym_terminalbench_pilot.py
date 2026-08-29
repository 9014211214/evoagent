from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

import scripts.verify_seagym_terminalbench_pilot as pilot_verifier
from scripts.verify_seagym_terminalbench_pilot import (
    VerificationError,
    _canonical_sha,
    verify_pilot,
    write_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = REPO_ROOT / "experiments" / "seagym_terminalbench" / "protocol.json"
CONFIG = REPO_ROOT / "experiments" / "seagym_terminalbench" / "configs" / "evoagent_mimo_v2_5_seed42.json"
SPLIT = REPO_ROOT / "experiments" / "seagym_terminalbench" / "splits" / "seed42.json"
TASK_INDEX = REPO_ROOT / "experiments" / "seagym_terminalbench" / "tasks" / "task_index.json"
ROUTE_CONTRACT = {
    "provider": {
        "only": ["xiaomi/fp8"],
        "allow_fallbacks": False,
        "require_parameters": True,
    },
    "reasoning": {"enabled": False},
    "accepted_response_models": ["xiaomi/mimo-v2.5", "xiaomi/mimo-v2.5-20260422"],
    "response_provider": "Xiaomi",
}
ROUTE_CONTRACT_SHA256 = _canonical_sha(ROUTE_CONTRACT)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def _snapshot(generation: int, parent: str | None, marker: str) -> dict[str, object]:
    components = {
        "skills": [{"name": "inspect", "guidance": f"inspect carefully {marker}"}],
        "memory": [{"topic": "verification", "lesson": f"verify observable state {marker}"}],
        "router": [{"condition": "before completion", "skill": "inspect"}],
        "policy": {
            "planning": f"make a bounded plan {marker}",
            "verification": "run deterministic checks",
            "recovery": "inspect the narrowest failure",
            "max_iterations": 8,
        },
    }
    unsigned = {
        "schema_version": "evoagent-seagym-harness-v1",
        "generation": generation,
        "parent_snapshot_sha256": parent,
        "model_id": "xiaomi/mimo-v2.5",
        "evidence_sha256": hashlib.sha256(f"evidence-{marker}".encode()).hexdigest(),
        "components": components,
        "component_sha256": {name: _canonical_sha(value) for name, value in components.items()},
        "evaluation_only": True,
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
    }
    return {**unsigned, "snapshot_sha256": _canonical_sha(unsigned)}


def _state(a0: dict[str, object], candidate: dict[str, object], update_index: int) -> dict[str, object]:
    return {
        "schema_version": "evoagent-seagym-state-v1",
        "baseline_id": "evoagent",
        "adapter_version": "0.1.0",
        "model_id": "xiaomi/mimo-v2.5",
        "seed": 42,
        "update_index": update_index,
        "a0_sha256": a0["snapshot_sha256"],
        "evaluation_candidate_sha256": candidate["snapshot_sha256"],
        "prompt_template": f"prompts/{candidate['snapshot_sha256']}.md",
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
    }


def _checkpoint(run: Path, checkpoint_id: str, a0: dict[str, object], candidate: dict[str, object], update_index: int) -> None:
    checkpoint = run / "checkpoints" / checkpoint_id
    state_root = checkpoint / "baseline_state"
    _write_json(state_root / "state.json", _state(a0, candidate, update_index))
    for snapshot in {str(a0["snapshot_sha256"]): a0, str(candidate["snapshot_sha256"]): candidate}.values():
        _write_json(state_root / "snapshots" / f"{snapshot['snapshot_sha256']}.json", snapshot)
    prompt = state_root / "prompts" / f"{candidate['snapshot_sha256']}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("{{ instruction }}\n", encoding="utf-8")
    inventory = {
        path.relative_to(state_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(state_root.rglob("*"))
        if path.is_file()
    }
    baseline = {
        "type": "evoagent_seagym_checkpoint",
        "schema_version": "evoagent-seagym-checkpoint-v1",
        "baseline_id": "evoagent",
        "state_ref": "baseline_state",
        "update_index": update_index,
        "state_inventory": inventory,
        "state_inventory_sha256": _canonical_sha(inventory),
        "state_metadata": {},
        "checkpoint_dir": str(checkpoint),
    }
    _write_json(
        checkpoint / "checkpoint.json",
        {
            "checkpoint_id": checkpoint_id,
            "checkpoint_type": "initial" if checkpoint_id == "initial" else "evaluation_point",
            "run_id": "pilot-run-1",
            "experiment_id": "evoagent_seagym_terminalbench2_mimo_v2_5_seed42",
            "trainer_state": {"updates_completed": update_index},
            "metadata": {},
            "refs": {},
            "baseline": baseline,
        },
    )


def _atif(snapshot: dict[str, object]) -> dict[str, object]:
    extra = {
        "api_model_id": "xiaomi/mimo-v2.5",
        "seed": 42,
        "snapshot_hash": snapshot["snapshot_sha256"],
        "component_hashes": snapshot["component_sha256"],
        "runtime_identity": {"name": "mimocode", "version": "0.1.13"},
        "route_contract_sha256": ROUTE_CONTRACT_SHA256,
    }
    return {
        "schema_version": "ATIF-v1.7",
        "agent": {
            "name": "seagym-evoagent-mimocode",
            "version": "0.1.0",
            "model_name": "openrouter/xiaomi/mimo-v2.5",
            "extra": extra,
        },
        "steps": [
            {"step_id": 1, "source": "system", "message": "", "extra": {"status": "sanitized"}},
            {
                "step_id": 2,
                "source": "agent",
                "message": "",
                "model_name": "openrouter/xiaomi/mimo-v2.5",
                "metrics": {"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 1, "cost_usd": 0.001},
                "llm_call_count": 1,
                "tool_calls": [{"tool_call_id": "tool-000001", "function_name": "bash", "arguments": {}}],
                "observation": {"results": [{"source_call_id": "tool-000001", "content": "status:success"}]},
                "extra": {"status": "success"},
            },
        ],
        "final_metrics": {
            "total_steps": 2,
            "total_prompt_tokens": 10,
            "total_completion_tokens": 2,
            "total_cached_tokens": 1,
            "total_cost_usd": 0.001,
        },
        "extra": extra,
    }


def _trial(run: Path, index: int, task_id: str, score: int, snapshot: dict[str, object]) -> tuple[Path, str]:
    trial = run / "harbor" / "jobs" / f"job-{index:02d}" / f"trial-{index:02d}"
    agent = trial / "agent"
    trajectory = _atif(snapshot)
    _write_json(agent / "trajectory.json", trajectory)
    atif_sha = hashlib.sha256((agent / "trajectory.json").read_bytes()).hexdigest()
    unsigned = {
        "schema_version": "evoagent-harbor-attestation-v1",
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "component_sha256": snapshot["component_sha256"],
        "atif_sha256": atif_sha,
        "route_contract_sha256": ROUTE_CONTRACT_SHA256,
        "model": {
            "api_id": "xiaomi/mimo-v2.5",
            "harbor_id": "openrouter/xiaomi/mimo-v2.5",
            "openrouter_provider": "xiaomi/fp8",
            "fallbacks_allowed": False,
            "reasoning_enabled": False,
            "credential_transport": "local_guard_proxy_v1",
        },
        "seed": 42,
        "runtime": {
            "adapter_version": "0.1.0",
            "mimocode_version": "0.1.13",
            "mimocode_archive_sha256": "0997a43647a99969d0194fad71af1fd6112aa8220e24a4562aea63953b1e1ada",
            "seagym_commit": "9e61e14db1f1355de944cd7c5b10c244fc74e82d",
            "harbor_commit": "f7110f1a240c6a50589b90c4d69714763946d088",
        },
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 1, "cost_usd": 0.001},
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "reasoning_persisted": False,
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
        "activation_claimed": False,
    }
    _write_json(agent / "evoagent-attestation.json", {**unsigned, "attestation_sha256": _canonical_sha(unsigned)})
    checksum = hashlib.sha256(task_id.encode()).hexdigest()
    result = {
        "task_name": task_id.rsplit("/", 1)[-1],
        "trial_name": f"trial-{index:02d}",
        "trial_uri": trial.as_uri(),
        "task_checksum": checksum,
        "source": "local",
        "config": {
            "job_id": f"job-{index:02d}",
            "agent": {
                "name": "evoagent-mimo",
                "kwargs": {
                    "route_contract": {
                        "provider": {
                            "only": ["xiaomi/fp8"],
                            "allow_fallbacks": False,
                            "require_parameters": True,
                        },
                        "reasoning": {"enabled": False},
                    }
                },
            },
        },
        "agent_result": {
            "n_input_tokens": 10,
            "n_cache_tokens": 1,
            "n_output_tokens": 2,
            "cost_usd": 0.001,
            "metadata": {"attestation_sha256": _canonical_sha(unsigned)},
        },
        "verifier_result": {"rewards": {"reward": float(score)}},
        "exception_info": None,
        "started_at": "2026-08-29T00:00:00Z",
        "finished_at": "2026-08-29T00:00:01Z",
    }
    _write_json(trial / "result.json", result)
    return trial / "result.json", checksum


def _usage(path: Path, value: float) -> None:
    _write_json(
        path,
        {
            "schema_version": "openrouter-safe-key-usage-v1",
            "authenticated": True,
            "key_url": "https://openrouter.ai/api/v1/key",
            "checked_at": "2026-08-29T00:00:00+00:00",
            "numeric": {
                "usage": value,
                "usage_daily": value,
                "usage_weekly": value,
                "usage_monthly": value,
                "byok_usage": 0,
                "limit": 6,
                "limit_remaining": 6 - value,
            },
        },
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    run = tmp_path / "run"
    shutil.copyfile(CONFIG, run / "inputs" / "experiment_config.json") if (run / "inputs").mkdir(parents=True, exist_ok=True) is None else None
    shutil.copyfile(SPLIT, run / "inputs" / "split_manifest.json")
    split = json.loads(SPLIT.read_text(encoding="utf-8"))["splits"]
    plan = {
        "experiment_id": "evoagent_seagym_terminalbench2_mimo_v2_5_seed42",
        "run_id": "pilot-run-1",
        "seed": 42,
        "split_id": "evoagent_tb2_seed42_6_3_3_v1",
        "train_batches": [split["train"][:3], split["train"][3:]],
        "views": {"final": {"id_test": split["test"]}, "replay": split["train"][:3], "update_validation": split["val"]},
    }
    _write_json(run / "inputs" / "batch_plan.json", plan)
    a0 = _snapshot(0, None, "a0")
    e1 = _snapshot(1, str(a0["snapshot_sha256"]), "e1")
    at = _snapshot(2, str(e1["snapshot_sha256"]), "at")
    _checkpoint(run, "initial", a0, a0, 0)
    _checkpoint(run, "E_1", a0, e1, 1)
    _checkpoint(run, "final", a0, at, 2)
    updates = [
        (1, e1),
        (2, at),
    ]
    for index, snapshot in updates:
        _append_jsonl(
            run / "records" / "agent_updates.jsonl",
            {
                "agent_id": "evoagent",
                "experiment_id": plan["experiment_id"],
                "global_update_index": index,
                "num_train_tasks_seen": index * 3,
                "num_updates_per_batch": 1,
                "run_id": plan["run_id"],
                "summary": {
                    "type": "baseline_update",
                    "update_index": index,
                    "changed": True,
                    "status": "updated",
                    "metrics": {"input_tokens": 5, "output_tokens": 1, "cost_usd": 0.001},
                    "logs": {
                        "candidate_sha256": snapshot["snapshot_sha256"],
                        "evidence_sha256": hashlib.sha256(f"batch-{index}".encode()).hexdigest(),
                        "causal_attribution_claimed": False,
                        "promotion_claimed": False,
                    },
                    "artifacts": {},
                },
                "train_batch_index": index,
                "update_repeat_index": 1,
            },
        )
    phases: list[tuple[str, str, str | None, int, list[str], list[int], dict[str, object]]] = [
        ("validation", "update_validation", None, 0, split["val"], [1, 1, 0], a0),
        ("train", "train", None, 1, split["train"][:3], [1, 0, 1], a0),
        ("replay", "replay", None, 1, plan["views"]["replay"], [1, 1, 1], e1),
        ("train", "train", None, 2, split["train"][3:], [0, 1, 1], e1),
        ("replay", "replay", None, 2, plan["views"]["replay"], [1, 1, 1], at),
        ("validation", "update_validation", None, 2, split["val"], [1, 1, 0], at),
        ("final", "id_test", "A_T", 2, split["test"], [1, 0, 1], at),
        ("final", "id_test", "A_0", 2, split["test"], [0, 0, 1], a0),
    ]
    task_domains = {
        item["task_id"]: item["attributes"]["domain"]
        for item in json.loads(TASK_INDEX.read_text(encoding="utf-8"))["tasks"]
    }
    trial_index = 0
    for mode, view, role, batch, task_ids, scores, snapshot in phases:
        for task_id, score in zip(task_ids, scores, strict=True):
            trial_index += 1
            result_path, checksum = _trial(run, trial_index, task_id, score, snapshot)
            point = "E_T" if role else ("E_0" if mode == "validation" and batch == 0 else f"E_{batch}" if mode in {"validation", "replay"} else None)
            _append_jsonl(
                run / "records" / "task_results.jsonl",
                {
                    "agent_checkpoint_id": role,
                    "agent_id": "evoagent",
                    "attributes": {"domain": task_domains[task_id]},
                    "baseline_role": role,
                    "cost": {"n_input_tokens": 10, "n_cache_tokens": 1, "n_output_tokens": 2, "cost_usd": 0.001},
                    "error": None,
                    "evaluation_point_id": point,
                    "experiment_id": plan["experiment_id"],
                    "global_update_index": batch if mode == "train" else None,
                    "mode": mode,
                    "num_train_tasks_seen": batch * 3,
                    "num_updates_per_batch": 1 if mode == "train" else None,
                    "refs": {"result_path": str(result_path), "task_checksum": checksum},
                    "rewards": {"reward": float(score)},
                    "run_id": plan["run_id"],
                    "runtime_seconds": 1.0,
                    "score": float(score),
                    "split_id": plan["split_id"],
                    "success": bool(score),
                    "task_id": task_id,
                    "train_batch_index": batch,
                    "update_repeat_index": 1 if mode == "train" else None,
                    "view_name": view,
                },
            )
    metrics = {
        "success_rate": {"id_test.A_0": 1 / 3, "id_test.A_T": 2 / 3},
        "mean_score": {"id_test.A_0": 1 / 3, "id_test.A_T": 2 / 3},
        "domain_macro_success_rate": {"id_test.A_0": 0.25, "id_test.A_T": 0.75},
        "final_gain": {"id_test": 1 / 3},
        "tokens": {
            "rollout": {
                "num_records": 24,
                "num_records_with_tokens": 24,
                "input_tokens": 240.0,
                "cache_tokens": 24.0,
                "output_tokens": 48.0,
                "total_tokens": 312.0,
                "cost_usd": 0.024,
            },
            "update": {
                "num_records": 2,
                "num_records_with_tokens": 2,
                "input_tokens": 10.0,
                "cache_tokens": 0.0,
                "output_tokens": 2.0,
                "total_tokens": 12.0,
                "cost_usd": 0.002,
            },
            "overall": {
                "num_records": 26,
                "num_records_with_tokens": 26,
                "input_tokens": 250.0,
                "cache_tokens": 24.0,
                "output_tokens": 50.0,
                "total_tokens": 324.0,
                "cost_usd": 0.026,
            },
        },
    }
    _write_json(run / "metrics.json", metrics)
    for point in ("E_0", "E_1", "E_2"):
        _append_jsonl(run / "records" / "evaluation_points.jsonl", {"evaluation_point_id": point, "evaluations": {}})
    _append_jsonl(
        run / "records" / "evaluation_points.jsonl",
        {
            "evaluation_point_id": "E_T",
            "evaluations": {
                "id_test": {
                    "agent_checkpoint_id": "A_T",
                    "baseline_checkpoint_id": "A_0",
                    "num_tasks": 3,
                    "num_baseline_tasks": 3,
                    "score": 2 / 3,
                    "baseline_score": 1 / 3,
                    "gain_vs_A_0": 1 / 3,
                }
            },
        },
    )
    attempt_error_classes = {name: 0 for name in pilot_verifier.PROXY_ERROR_CLASSES}
    attempt_error_classes["http_4xx"] = 1
    status_buckets = {name: 0 for name in pilot_verifier.PROXY_HTTP_STATUS_BUCKETS}
    status_buckets["429"] = 1
    _write_json(
        run / "evidence" / "guard-proxy-health.json",
        {
            "active_requests": 0,
            "completed_requests": 24,
            "credential_persisted": False,
            "forwarded_requests": 24,
            "guard_proxy_source_sha256": pilot_verifier.EXPECTED_GUARD_PROXY_RUNTIME["source_sha256"],
            "limits": pilot_verifier.EXPECTED_GUARD_PROXY_RUNTIME["limits"],
            "max_upstream_retries": 2,
            "ready": True,
            "rejected_requests": 0,
            "rejection_classes": {"concurrency_limit": 0, "request_limit": 0, "other": 0},
            "remaining_requests": 744,
            "request_limit": 768,
            "retry_policy": pilot_verifier.EXPECTED_RETRY_POLICY,
            "schema_version": "openrouter-guard-proxy-health-v3",
            "upstream_attempt_error_classes": attempt_error_classes,
            "upstream_attempts": 25,
            "upstream_error_classes": {name: 0 for name in pilot_verifier.PROXY_ERROR_CLASSES},
            "upstream_errors": 0,
            "upstream_http_statuses": status_buckets,
            "upstream_retries": 1,
        },
    )
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    _usage(before, 1.0)
    _usage(after, 1.03)
    return run, before, after


def _first_trial_evidence(run: Path) -> tuple[Path, dict[str, object], Path, dict[str, object]]:
    trajectory_path = next((run / "harbor" / "jobs").rglob("trajectory.json"))
    attestation_path = trajectory_path.with_name("evoagent-attestation.json")
    return (
        trajectory_path,
        json.loads(trajectory_path.read_text(encoding="utf-8")),
        attestation_path,
        json.loads(attestation_path.read_text(encoding="utf-8")),
    )


def _rewrite_self_consistent_evidence(
    trajectory_path: Path,
    trajectory: dict[str, object],
    attestation_path: Path,
    attestation: dict[str, object],
) -> None:
    _write_json(trajectory_path, trajectory)
    attestation["atif_sha256"] = hashlib.sha256(trajectory_path.read_bytes()).hexdigest()
    unsigned = dict(attestation)
    unsigned.pop("attestation_sha256", None)
    attestation["attestation_sha256"] = _canonical_sha(unsigned)
    _write_json(attestation_path, attestation)


def test_verifies_real_pilot_and_writes_privacy_bounded_bundle(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    result, rows, updates = verify_pilot(
        run_dir=run,
        protocol_path=PROTOCOL,
        usage_before=before,
        usage_after=after,
    )
    assert result["classification"] == "positive_pilot_signal"
    assert result["comparison"]["held_out"]["gain_vs_A_0"] == pytest.approx(1 / 3)
    assert result["comparison"]["frozen_validation"]["delta"] == 0
    assert result["usage"]["observed_key_usage_delta_usd"] == pytest.approx(0.03)
    assert len(rows) == 24
    assert len(updates) == 2
    assert result["evidence"]["guard_proxy_health"]["upstream_retries"] == 1
    assert result["evidence"]["verified_rollout_model_calls"] == 24
    assert result["evidence"]["verified_update_model_calls"] == 2
    assert result["evidence"]["verified_guard_proxy_logical_requests"] == 24
    assert result["evidence"]["verified_total_logical_model_requests"] == 26
    output = tmp_path / "bundle"
    write_bundle(output, result, rows, updates)
    assert (output / "SHA256SUMS").is_file()
    combined = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    assert "prompt_template_path" not in combined
    assert "result_path" not in combined
    assert "exception_traceback" not in combined


def test_rejects_score_tampering(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    path = run / "records" / "task_results.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[-1]["score"] = 0.0
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(VerificationError):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_route_attestation_drift(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    path = next((run / "harbor" / "jobs").rglob("evoagent-attestation.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["model"]["openrouter_provider"] = "auto"
    unsigned = dict(value)
    unsigned.pop("attestation_sha256")
    value["attestation_sha256"] = _canonical_sha(unsigned)
    _write_json(path, value)
    with pytest.raises(VerificationError):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_attested_seed_drift(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    path = next((run / "harbor" / "jobs").rglob("evoagent-attestation.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["seed"] = 43
    unsigned = dict(value)
    unsigned.pop("attestation_sha256")
    value["attestation_sha256"] = _canonical_sha(unsigned)
    _write_json(path, value)
    with pytest.raises(VerificationError, match="seed drifted"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


@pytest.mark.parametrize(
    "tamper",
    ["component_hashes", "route_contract", "runtime_identity", "agent_identity", "final_metrics"],
)
def test_rejects_self_consistent_atif_attestation_tampering(tmp_path: Path, tamper: str) -> None:
    run, before, after = _fixture(tmp_path)
    trajectory_path, trajectory, attestation_path, attestation = _first_trial_evidence(run)
    extra = trajectory["extra"]
    agent_extra = trajectory["agent"]["extra"]
    if tamper == "component_hashes":
        extra["component_hashes"]["skills"] = "0" * 64
        agent_extra["component_hashes"]["skills"] = "0" * 64
        attestation["component_sha256"]["skills"] = "0" * 64
    elif tamper == "route_contract":
        extra["route_contract_sha256"] = "0" * 64
        agent_extra["route_contract_sha256"] = "0" * 64
        attestation["route_contract_sha256"] = "0" * 64
    elif tamper == "runtime_identity":
        extra["runtime_identity"]["version"] = "0.1.14"
        agent_extra["runtime_identity"]["version"] = "0.1.14"
        attestation["runtime"]["mimocode_version"] = "0.1.14"
    elif tamper == "agent_identity":
        trajectory["agent"]["version"] = "9.9.9"
    elif tamper == "final_metrics":
        trajectory["final_metrics"]["total_cost_usd"] = 0.002
        attestation["usage"]["cost_usd"] = 0.002
    else:  # pragma: no cover - the parametrization is closed above
        raise AssertionError(tamper)
    _rewrite_self_consistent_evidence(
        trajectory_path,
        trajectory,
        attestation_path,
        attestation,
    )
    with pytest.raises(VerificationError):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_credential_like_material(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    path = next((run / "harbor" / "jobs").rglob("result.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["unsafe"] = "sk-or-v1-abcdefghijklmnopqrstuvwxyz012345"
    _write_json(path, value)
    with pytest.raises(VerificationError, match="credential"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_incomplete_task_evidence(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    path = run / "records" / "task_results.jsonl"
    path.write_text("\n".join(path.read_text(encoding="utf-8").splitlines()[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(VerificationError, match="expected 24"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_observed_usage_above_authorized_maximum(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    usage = json.loads(after.read_text(encoding="utf-8"))
    usage["numeric"]["usage"] = 2.21
    after.write_text(json.dumps(usage), encoding="utf-8")

    with pytest.raises(VerificationError, match="authorized USD 1.20 maximum"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("model_route", "provider_update_sampling_determinism_claimed"),
        ("model_route", "update_model_seed_parameter_sent"),
    ],
)
def test_rejects_seed_claim_amendment_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    field: str,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol[section][field] = True
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError):
        pilot_verifier._validate_protocol(path)


def test_rejects_retry_policy_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["runtime"]["openrouter_retry_policy"]["max_retries_per_client_request"] = 3
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="runtime retry policy drifted"):
        pilot_verifier._validate_protocol(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pilot_kind", "leaderboard_result"),
        ("results_status", "verified_completed_real_pilot"),
    ],
)
def test_rejects_claim_boundary_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["claim_boundary"][field] = value
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="protocol claim boundary drifted"):
        pilot_verifier._validate_protocol(path)


def test_rejects_guard_proxy_runtime_evidence_drift(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    health_path = run / "evidence" / "guard-proxy-health.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health["guard_proxy_source_sha256"] = "0" * 64
    _write_json(health_path, health)
    with pytest.raises(VerificationError, match="runtime identity or retry policy drifted"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


@pytest.mark.parametrize("forwarded", [23, 25])
def test_rejects_guard_proxy_logical_request_count_drift(
    tmp_path: Path,
    forwarded: int,
) -> None:
    run, before, after = _fixture(tmp_path)
    health_path = run / "evidence" / "guard-proxy-health.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health["forwarded_requests"] = forwarded
    health["completed_requests"] = forwarded
    health["upstream_attempts"] = forwarded + health["upstream_retries"]
    health["remaining_requests"] = health["request_limit"] - forwarded
    _write_json(health_path, health)
    with pytest.raises(VerificationError, match="completed-run counters are inconsistent"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)
