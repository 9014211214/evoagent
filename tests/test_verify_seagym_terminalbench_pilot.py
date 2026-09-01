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
    job_dir = run / "harbor" / "jobs" / f"job-{index:02d}"
    job_id = f"00000000-0000-0000-0000-{index:012d}"
    trial_id = f"10000000-0000-0000-0000-{index:012d}"
    trial = job_dir / f"trial-{index:02d}"
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
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "cached_tokens": 1,
            "reasoning_tokens": 0,
            "cost_usd": 0.001,
        },
        "runtime_failure_receipt_sha256": None,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "reasoning_persisted": False,
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
        "activation_claimed": False,
    }
    _write_json(agent / "evoagent-attestation.json", {**unsigned, "attestation_sha256": _canonical_sha(unsigned)})
    checksum = hashlib.sha256(task_id.encode()).hexdigest()
    task_name = task_id.rsplit("/", 1)[-1]
    patched_job_dir = run / "harbor" / "jobs" / "_patched_tasksets" / job_dir.name
    patched_task_dir = patched_job_dir / task_name
    patched_task_dir.mkdir(parents=True, exist_ok=True)
    task_path = patched_task_dir.resolve().as_posix()
    trial_name = f"trial-{index:02d}"
    source = job_dir.name
    eval_key = f"evoagent-mimo__xiaomi/mimo-v2.5__{source}"
    result = {
        "id": trial_id,
        "task_name": task_name,
        "trial_name": trial_name,
        "trial_uri": trial.as_uri(),
        "task_id": {"path": task_path},
        "task_checksum": checksum,
        "source": source,
        "config": {
            "task": {"path": task_path},
            "trial_name": trial_name,
            "trials_dir": job_dir.resolve().as_posix(),
            "install_only": False,
            "timeout_multiplier": 1.0,
            "agent_timeout_multiplier": None,
            "verifier_timeout_multiplier": None,
            "agent_setup_timeout_multiplier": None,
            "environment_build_timeout_multiplier": None,
            "job_id": job_id,
            "agent": {
                "import_path": "seagym_evoagent.harbor_agent:EvoAgentMiMo",
                "model_name": "openrouter/xiaomi/mimo-v2.5",
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
            "environment": {},
            "verifier": {},
            "artifacts": [],
            "extra_instruction_paths": [],
        },
        "agent_info": {
            "name": "evoagent-mimo",
            "version": "0.1.0",
            "model_info": {"name": "xiaomi/mimo-v2.5", "provider": "openrouter"},
        },
        "agent_result": {
            "n_input_tokens": 10,
            "n_cache_tokens": 1,
            "n_output_tokens": 2,
            "cost_usd": 0.001,
            "rollout_details": None,
            "metadata": {"attestation_sha256": _canonical_sha(unsigned)},
        },
        "verifier_result": {"rewards": {"reward": float(score)}},
        "exception_info": None,
        "started_at": "2026-08-29T00:00:00Z",
        "finished_at": "2026-08-29T00:00:01Z",
        "environment_setup": {
            "started_at": "2026-08-29T00:00:00Z",
            "finished_at": "2026-08-29T00:00:01Z",
        },
        "agent_setup": {
            "started_at": "2026-08-29T00:00:00Z",
            "finished_at": "2026-08-29T00:00:01Z",
        },
        "agent_execution": {
            "started_at": "2026-08-29T00:00:00Z",
            "finished_at": "2026-08-29T00:00:01Z",
        },
        "verifier": {
            "started_at": "2026-08-29T00:00:00Z",
            "finished_at": "2026-08-29T00:00:01Z",
        },
        "step_results": None,
    }
    _write_json(trial / "config.json", result["config"])
    _write_json(trial / "result.json", result)
    _write_json(
        job_dir / "config.json",
        {
            "job_name": job_dir.name,
            "jobs_dir": (run / "harbor" / "jobs").resolve().as_posix(),
            "n_attempts": 1,
            "n_concurrent_trials": 1,
            "retry": {"max_retries": 0},
            "tasks": [],
            "datasets": [
                {
                    "path": patched_job_dir.resolve().as_posix(),
                    "task_names": [task_name],
                    "n_tasks": 1,
                }
            ],
            "agents": [result["config"]["agent"]],
        },
    )
    _write_json(
        job_dir / "result.json",
        {
            "finished_at": "2026-08-29T00:00:01Z",
            "id": job_id,
            "n_total_trials": 1,
            "started_at": "2026-08-29T00:00:00Z",
            "stats": {
                "cost_usd": 0.001,
                "evals": {
                    eval_key: {
                        "exception_stats": {},
                        "metrics": [{"mean": float(score)}],
                        "n_errors": 0,
                        "n_trials": 1,
                        "pass_at_k": {},
                        "reward_stats": {"reward": {str(float(score)): [trial_name]}},
                    }
                },
                "n_cache_tokens": 1,
                "n_cancelled_trials": 0,
                "n_completed_trials": 1,
                "n_errored_trials": 0,
                "n_input_tokens": 10,
                "n_output_tokens": 2,
                "n_pending_trials": 0,
                "n_retries": 0,
                "n_running_trials": 0,
            },
            "updated_at": "2026-08-29T00:00:01Z",
        },
    )
    return trial / "result.json", checksum


def _replace_trial_with_receipted_runtime_failure(
    result_path: Path,
    snapshot: dict[str, object],
) -> None:
    agent_dir = result_path.parent / "agent"
    (agent_dir / "trajectory.json").unlink()
    (agent_dir / "evoagent-attestation.json").unlink()
    unsigned = {
        "schema_version": "evoagent-runtime-failure-v1",
        "failure_class": "runtime_sanitization_failed",
        "failure_stage": "sanitize",
        "mimocode_exit_class": "success",
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "component_sha256": snapshot["component_sha256"],
        "route_contract_sha256": ROUTE_CONTRACT_SHA256,
        "model": {
            "api_id": "xiaomi/mimo-v2.5",
            "harbor_id": "openrouter/xiaomi/mimo-v2.5",
        },
        "seed": 42,
        "runtime": {"name": "mimocode", "version": "0.1.13"},
        "atif_present": False,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "reasoning_content_persisted": False,
    }
    _write_json(
        agent_dir / "evoagent-runtime-failure.json",
        {**unsigned, "receipt_sha256": _canonical_sha(unsigned)},
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["agent_result"] = {
        "n_input_tokens": 0,
        "n_cache_tokens": 0,
        "n_output_tokens": 0,
        "cost_usd": 0.0,
        "rollout_details": None,
        "metadata": {"runtime_failure": True},
    }
    payload["verifier_result"] = {"rewards": {"reward": 0.0}}
    payload["exception_info"] = {
        "exception_type": "RuntimeFailure",
        "exception_message": "classified_failure",
        "exception_traceback": "",
        "occurred_at": "2026-08-29T00:00:01Z",
    }
    _write_json(result_path, payload)
    aggregate_path = result_path.parent.parent / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["stats"]["n_errored_trials"] = 1
    aggregate["stats"]["n_input_tokens"] = 0
    aggregate["stats"]["n_cache_tokens"] = 0
    aggregate["stats"]["n_output_tokens"] = 0
    aggregate["stats"]["cost_usd"] = 0.0
    eval_stats = next(iter(aggregate["stats"]["evals"].values()))
    eval_stats["n_errors"] = 1
    eval_stats["metrics"] = [{"mean": 0.0}]
    eval_stats["reward_stats"] = {"reward": {"0.0": [payload["trial_name"]]}}
    eval_stats["exception_stats"] = {"RuntimeFailure": [payload["trial_name"]]}
    _write_json(aggregate_path, aggregate)


def _add_atif_present_failure_receipt(run: Path, row_index: int) -> None:
    rows_path = run / "records" / "task_results.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    row = rows[row_index]
    result_path = Path(row["refs"]["result_path"])
    attestation_path = result_path.parent / "agent" / "evoagent-attestation.json"
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    unsigned = {
        "schema_version": "evoagent-runtime-failure-v1",
        "failure_class": "mimocode_process_failed",
        "failure_stage": "mimocode",
        "mimocode_exit_class": "nonzero",
        "snapshot_sha256": attestation["snapshot_sha256"],
        "component_sha256": attestation["component_sha256"],
        "route_contract_sha256": ROUTE_CONTRACT_SHA256,
        "model": {
            "api_id": "xiaomi/mimo-v2.5",
            "harbor_id": "openrouter/xiaomi/mimo-v2.5",
        },
        "seed": 42,
        "runtime": {"name": "mimocode", "version": "0.1.13"},
        "atif_present": True,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "reasoning_content_persisted": False,
    }
    receipt_hash = _canonical_sha(unsigned)
    _write_json(
        result_path.parent / "agent" / "evoagent-runtime-failure.json",
        {**unsigned, "receipt_sha256": receipt_hash},
    )
    attestation["runtime_failure_receipt_sha256"] = receipt_hash
    attestation_unsigned = dict(attestation)
    attestation_unsigned.pop("attestation_sha256")
    attestation["attestation_sha256"] = _canonical_sha(attestation_unsigned)
    _write_json(attestation_path, attestation)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["verifier_result"] = {"rewards": {"reward": 0.0}}
    payload["exception_info"] = {
        "exception_type": "RuntimeFailure",
        "exception_message": "classified_failure",
        "exception_traceback": "",
        "occurred_at": "2026-08-29T00:00:01Z",
    }
    _write_json(result_path, payload)
    aggregate_path = result_path.parent.parent / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["stats"]["n_errored_trials"] = 1
    eval_stats = next(iter(aggregate["stats"]["evals"].values()))
    eval_stats["n_errors"] = 1
    eval_stats["metrics"] = [{"mean": 0.0}]
    eval_stats["reward_stats"] = {"reward": {"0.0": [payload["trial_name"]]}}
    eval_stats["exception_stats"] = {"RuntimeFailure": [payload["trial_name"]]}
    _write_json(aggregate_path, aggregate)
    row["error"] = "mimocode_process_failed"
    row["rewards"] = {"reward": 0.0}
    row["score"] = 0.0
    row["success"] = False
    rows_path.write_text(
        "\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in rows) + "\n",
        encoding="utf-8",
    )


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


def _fixture(tmp_path: Path, *, skip_second_update: bool = False) -> tuple[Path, Path, Path]:
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
    final_candidate = e1 if skip_second_update else at
    _checkpoint(run, "final", a0, final_candidate, 2)
    updates = [
        (1, e1),
        (2, final_candidate),
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
                    "changed": not (skip_second_update and index == 2),
                    "status": "unchanged" if skip_second_update and index == 2 else "updated",
                    "metrics": (
                        {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
                        if skip_second_update and index == 2
                        else {"input_tokens": 5, "output_tokens": 1, "cost_usd": 0.001}
                    ),
                    "logs": {
                        "model_call_executed": not (skip_second_update and index == 2),
                        "candidate_sha256": snapshot["snapshot_sha256"],
                        "evidence_sha256": hashlib.sha256(f"batch-{index}".encode()).hexdigest(),
                        **(
                            {"skip_code": "no_usable_harbor_atif_evidence"}
                            if skip_second_update and index == 2
                            else {}
                        ),
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
        ("train", "train", None, 2, split["train"][3:], [0, 0, 0] if skip_second_update else [0, 1, 1], e1),
        ("validation", "update_validation", None, 2, split["val"], [1, 1, 0], final_candidate),
        ("replay", "replay", None, 2, plan["views"]["replay"], [1, 1, 1], final_candidate),
        ("final", "id_test", "A_T", 2, split["test"], [1, 0, 1], final_candidate),
        ("final_baseline", "id_test", "A_0", 2, split["test"], [0, 0, 1], a0),
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
            runtime_failure = skip_second_update and mode == "train" and batch == 2
            if runtime_failure:
                _replace_trial_with_receipted_runtime_failure(result_path, snapshot)
            point = "E_T" if role else ("E_0" if mode == "validation" and batch == 0 else f"E_{batch}" if mode in {"validation", "replay"} else None)
            checkpoint_id = (
                "final"
                if role == "A_T"
                else "initial"
                if role == "A_0"
                else f"E_{batch}"
                if mode == "replay"
                else None
            )
            _append_jsonl(
                run / "records" / "task_results.jsonl",
                {
                    "agent_checkpoint_id": checkpoint_id,
                    "agent_id": "evoagent",
                    "attributes": {"domain": task_domains[task_id]},
                    "baseline_role": role,
                    "cost": (
                        {}
                        if runtime_failure
                        else {"n_input_tokens": 10, "n_cache_tokens": 1, "n_output_tokens": 2, "cost_usd": 0.001}
                    ),
                    "error": "runtime_sanitization_failed" if runtime_failure else None,
                    "evaluation_point_id": point,
                    "experiment_id": plan["experiment_id"],
                    "global_update_index": batch if mode == "train" else None,
                    "mode": mode,
                    "num_train_tasks_seen": batch * 3,
                    "num_updates_per_batch": 1 if mode == "train" else None,
                    "refs": {
                        "harbor_returncode": 0,
                        "job_dir": str(result_path.parent.parent),
                        "result_path": str(result_path),
                        "task_checksum": checksum,
                    },
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
                "num_records_with_tokens": 21 if skip_second_update else 24,
                "input_tokens": 210.0 if skip_second_update else 240.0,
                "cache_tokens": 21.0 if skip_second_update else 24.0,
                "output_tokens": 42.0 if skip_second_update else 48.0,
                "total_tokens": 273.0 if skip_second_update else 312.0,
                "cost_usd": 0.021 if skip_second_update else 0.024,
            },
            "update": {
                "num_records": 2,
                "num_records_with_tokens": 1 if skip_second_update else 2,
                "input_tokens": 5.0 if skip_second_update else 10.0,
                "cache_tokens": 0.0,
                "output_tokens": 1.0 if skip_second_update else 2.0,
                "total_tokens": 6.0 if skip_second_update else 12.0,
                "cost_usd": 0.001 if skip_second_update else 0.002,
            },
            "overall": {
                "num_records": 26,
                "num_records_with_tokens": 22 if skip_second_update else 26,
                "input_tokens": 215.0 if skip_second_update else 250.0,
                "cache_tokens": 21.0 if skip_second_update else 24.0,
                "output_tokens": 43.0 if skip_second_update else 50.0,
                "total_tokens": 279.0 if skip_second_update else 324.0,
                "cost_usd": 0.022 if skip_second_update else 0.026,
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
    attempt_error_classes["http_4xx"] = 4
    status_buckets = {name: 0 for name in pilot_verifier.PROXY_HTTP_STATUS_BUCKETS}
    status_buckets["404"] = 4
    _write_json(
        run / "evidence" / "guard-proxy-health.json",
        {
            "active_requests": 0,
            "completed_requests": 24,
            "credential_persisted": False,
            "forwarded_requests": 24,
            "guard_proxy_source_sha256": pilot_verifier.EXPECTED_GUARD_PROXY_RUNTIME["source_sha256"],
            "limits": pilot_verifier.EXPECTED_GUARD_PROXY_RUNTIME["limits"],
            "max_upstream_retries": 4,
            "normalizations": {"tool_choice_none_to_no_tools": 4},
            "ready": True,
            "rejected_requests": 0,
            "rejection_classes": {"concurrency_limit": 0, "request_limit": 0, "other": 0},
            "remaining_requests": 744,
            "request_profiles": {
                "inbound_tool_choice": {
                    "absent": 20,
                    "auto": 0,
                    "required": 0,
                    "none": 4,
                    "named": 0,
                },
                "outbound_tool_choice": {
                    "absent": 24,
                    "auto": 0,
                    "required": 0,
                    "none": 0,
                    "named": 0,
                },
                "final_upstream_errors_by_outbound_tool_choice": {
                    "absent": 0,
                    "auto": 0,
                    "required": 0,
                    "none": 0,
                    "named": 0,
                },
            },
            "request_limit": 768,
            "root_session_binding_enabled": True,
            "root_session_rejections": 0,
            "root_sessions_limit": 24,
            "root_sessions_observed": 24,
            "retry_policy": pilot_verifier.EXPECTED_RETRY_POLICY,
            "schema_version": "openrouter-guard-proxy-health-v5",
            "upstream_attempt_error_classes": attempt_error_classes,
            "upstream_attempts": 28,
            "upstream_error_classes": {name: 0 for name in pilot_verifier.PROXY_ERROR_CLASSES},
            "upstream_errors": 0,
            "upstream_http_statuses": status_buckets,
            "upstream_retries": 4,
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
    assert result["evidence"]["guard_proxy_health"]["upstream_retries"] == 4
    assert result["evidence"]["guard_proxy_health"]["normalizations"] == {
        "tool_choice_none_to_no_tools": 4
    }
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


def test_accepts_receipted_error_batch_as_incomplete_without_counting_a_model_update(
    tmp_path: Path,
) -> None:
    run, before, after = _fixture(tmp_path, skip_second_update=True)

    result, rows, updates = verify_pilot(
        run_dir=run,
        protocol_path=PROTOCOL,
        usage_before=before,
        usage_after=after,
    )

    assert result["results_status"] == "completed_with_incomplete_training_evidence"
    assert result["comparison"]["errors"] == 3
    assert result["comparison"]["runtime_failure_classes"] == {
        "runtime_sanitization_failed": 3
    }
    assert result["comparison"]["runtime_failure_stages"] == {"sanitize": 3}
    assert result["comparison"]["mimocode_exit_classes"] == {"success": 3}
    assert result["evidence"]["runtime_failure_receipt_trials"] == 3
    assert result["evidence"]["missing_atif_failure_receipt_trials"] == 3
    assert result["evidence"]["attested_rollout_model_calls"] == 21
    assert result["evidence"]["verified_rollout_model_calls"] == 24
    assert result["evidence"]["verified_update_model_calls"] == 1
    assert result["evidence"]["skipped_update_attempts"] == 1
    assert result["evidence"]["verified_total_logical_model_requests"] == 25
    failed = [row for row in rows if row["failure_receipt_sha256"] is not None]
    assert len(failed) == 3
    assert all(row["score"] == 0.0 and row["error_present"] for row in failed)
    assert updates[1]["model_call_executed"] is False
    assert updates[1]["skip_code"] == "no_usable_harbor_atif_evidence"


def test_accepts_atif_present_failure_receipt_but_marks_result_incomplete(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    _add_atif_present_failure_receipt(run, 3)

    result, rows, _updates = verify_pilot(
        run_dir=run,
        protocol_path=PROTOCOL,
        usage_before=before,
        usage_after=after,
    )

    assert result["results_status"] == "completed_with_incomplete_training_evidence"
    assert result["comparison"]["errors"] == 1
    assert result["comparison"]["runtime_failure_classes"] == {"mimocode_process_failed": 1}
    assert result["evidence"]["runtime_failure_receipt_trials"] == 1
    assert result["evidence"]["missing_atif_failure_receipt_trials"] == 0
    assert result["evidence"]["attested_rollout_model_calls"] == 24
    assert result["evidence"]["verified_update_model_calls"] == 2
    assert rows[3]["training_evidence_complete"] is True
    assert rows[3]["score"] == 0.0


@pytest.mark.parametrize(
    "tamper",
    ["hash", "schema", "snapshot", "classification", "privacy", "atif_present"],
)
def test_rejects_malformed_or_tampered_runtime_failure_receipt(
    tmp_path: Path,
    tamper: str,
) -> None:
    run, before, after = _fixture(tmp_path, skip_second_update=True)
    path = next((run / "harbor" / "jobs").rglob("evoagent-runtime-failure.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "hash":
        value["receipt_sha256"] = "0" * 64
    elif tamper == "schema":
        value["unexpected"] = False
    elif tamper == "snapshot":
        value["snapshot_sha256"] = "0" * 64
    elif tamper == "classification":
        value["mimocode_exit_class"] = "nonzero"
    elif tamper == "privacy":
        value["raw_response_persisted"] = True
    else:
        value["atif_present"] = True
    if tamper != "hash":
        unsigned = dict(value)
        unsigned.pop("receipt_sha256")
        value["receipt_sha256"] = _canonical_sha(unsigned)
    _write_json(path, value)

    with pytest.raises(VerificationError, match="failure receipt"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_missing_or_escaped_runtime_failure_receipt(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path, skip_second_update=True)
    path = next((run / "harbor" / "jobs").rglob("evoagent-runtime-failure.json"))
    path.unlink()
    with pytest.raises(VerificationError):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)

    run, before, after = _fixture(tmp_path / "escape", skip_second_update=True)
    rows_path = run / "records" / "task_results.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    failed_index = next(index for index, row in enumerate(rows) if row.get("error"))
    outside = tmp_path / "outside" / "evoagent-runtime-failure.json"
    outside.parent.mkdir()
    source = next((run / "harbor" / "jobs").rglob("evoagent-runtime-failure.json"))
    outside.write_bytes(source.read_bytes())
    rows[failed_index]["refs"]["failure_receipt_path"] = str(outside)
    rows_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(VerificationError, match="path escapes"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_symlinked_runtime_failure_receipt(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path, skip_second_update=True)
    path = next((run / "harbor" / "jobs").rglob("evoagent-runtime-failure.json"))
    target = path.with_name("receipt-target.json")
    path.replace(target)
    try:
        path.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(VerificationError, match="symlinked evidence path"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_no_call_update_when_train_batch_has_usable_atif(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    path = run / "records" / "agent_updates.jsonl"
    updates = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    updates[1]["summary"]["changed"] = False
    updates[1]["summary"]["status"] = "unchanged"
    updates[1]["summary"]["metrics"] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    updates[1]["summary"]["logs"]["model_call_executed"] = False
    updates[1]["summary"]["logs"]["skip_code"] = "no_usable_harbor_atif_evidence"
    path.write_text(
        "\n".join(json.dumps(update, sort_keys=True, separators=(",", ":")) for update in updates) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(VerificationError):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


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


def test_rejects_frozen_execution_block_swap(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    rows_path = run / "records" / "task_results.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    rows[0:6] = rows[3:6] + rows[0:3]
    rows_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="global frozen 24-slot execution order drifted"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_pending_single_task_harbor_job(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    aggregate_path = run / "harbor" / "jobs" / "job-01" / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["stats"]["n_completed_trials"] = 0
    aggregate["stats"]["n_pending_trials"] = 1
    _write_json(aggregate_path, aggregate)

    with pytest.raises(VerificationError, match="aggregate is incomplete"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_unreferenced_partial_harbor_job(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    partial = run / "harbor" / "jobs" / "job-25-partial"
    _write_json(
        partial / "config.json",
        {
            "job_name": partial.name,
            "jobs_dir": partial.parent.resolve().as_posix(),
            "n_attempts": 1,
            "n_concurrent_trials": 1,
            "retry": {"max_retries": 0},
            "tasks": [{"path": "/tmp/fix-git"}],
            "datasets": [],
            "agents": [{}],
        },
    )

    with pytest.raises(VerificationError, match="job inventory differs"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_unreferenced_partial_trial_in_referenced_job(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    _write_json(
        run / "harbor" / "jobs" / "job-01" / "partial-trial" / "config.json",
        {"trial_name": "partial-trial"},
    )

    with pytest.raises(VerificationError, match="exactly one child result/trial directory"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_missing_patched_taskset_support_tree(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    shutil.rmtree(run / "harbor" / "jobs" / "_patched_tasksets")

    with pytest.raises(VerificationError, match="patched task path is missing"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_child_source_outside_its_patched_dataset(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    child_path = run / "harbor" / "jobs" / "job-01" / "trial-01" / "result.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child["source"] = "local"
    _write_json(child_path, child)

    with pytest.raises(VerificationError, match="path/source is not bound"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_child_path_outside_its_patched_dataset(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    child_path = run / "harbor" / "jobs" / "job-01" / "trial-01" / "result.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))
    alternate_task = run / "alternate" / child["task_name"]
    alternate_task.mkdir(parents=True)
    child["task_id"]["path"] = alternate_task.resolve().as_posix()
    child["config"]["task"]["path"] = alternate_task.resolve().as_posix()
    _write_json(child_path, child)
    _write_json(child_path.with_name("config.json"), child["config"])

    with pytest.raises(VerificationError, match="path/source is not bound"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_direct_task_job_config_instead_of_patched_dataset(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    job_config_path = run / "harbor" / "jobs" / "job-01" / "config.json"
    job_config = json.loads(job_config_path.read_text(encoding="utf-8"))
    task_path = (
        run
        / "harbor"
        / "jobs"
        / "_patched_tasksets"
        / "job-01"
        / "cancel-async-tasks"
    ).resolve().as_posix()
    job_config["tasks"] = [{"path": task_path}]
    job_config["datasets"] = []
    _write_json(job_config_path, job_config)

    with pytest.raises(VerificationError, match="exactly one patched dataset"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_job_agent_identity_drift_from_child(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    job_config_path = run / "harbor" / "jobs" / "job-01" / "config.json"
    job_config = json.loads(job_config_path.read_text(encoding="utf-8"))
    job_config["agents"][0]["model_name"] = "openrouter/another/model"
    _write_json(job_config_path, job_config)

    with pytest.raises(VerificationError, match="AgentConfig differs"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_nonzero_single_task_harbor_job(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    rows_path = run / "records" / "task_results.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["refs"]["harbor_returncode"] = 1
    rows_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="did not exit successfully"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_reused_single_task_harbor_job(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    rows_path = run / "records" / "task_results.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    rows[1]["refs"]["result_path"] = rows[0]["refs"]["result_path"]
    rows[1]["refs"]["job_dir"] = rows[0]["refs"]["job_dir"]
    rows_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="child result was reused"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_extra_child_result_in_single_task_harbor_job(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    source = run / "harbor" / "jobs" / "job-01" / "trial-01" / "result.json"
    extra = run / "harbor" / "jobs" / "job-01" / "unreferenced-trial" / "result.json"
    extra.parent.mkdir(parents=True)
    shutil.copyfile(source, extra)

    with pytest.raises(VerificationError, match="exactly one child result"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_incomplete_harbor_job_aggregate_schema(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    aggregate_path = run / "harbor" / "jobs" / "job-01" / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate.pop("n_total_trials")
    _write_json(aggregate_path, aggregate)

    with pytest.raises(VerificationError, match="aggregate schema drifted"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_child_result_with_different_harbor_job_id(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    child_path = run / "harbor" / "jobs" / "job-01" / "trial-01" / "result.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child["config"]["job_id"] = "00000000-0000-0000-0000-999999999999"
    _write_json(child_path, child)
    _write_json(child_path.with_name("config.json"), child["config"])

    with pytest.raises(VerificationError, match="not bound to its aggregate job"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_placeholder_shaped_harbor_child_result(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    child_path = run / "harbor" / "jobs" / "job-01" / "trial-01" / "result.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child.pop("id")
    _write_json(child_path, child)

    with pytest.raises(VerificationError, match="TrialResult schema drifted"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_harbor_child_agent_info_drift(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    child_path = run / "harbor" / "jobs" / "job-01" / "trial-01" / "result.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child["agent_info"]["name"] = "placeholder"
    _write_json(child_path, child)

    with pytest.raises(VerificationError, match="AgentInfo drifted"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_harbor_aggregate_usage_drift(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    aggregate_path = run / "harbor" / "jobs" / "job-01" / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["stats"]["n_input_tokens"] = 999999
    _write_json(aggregate_path, aggregate)

    with pytest.raises(VerificationError, match="token usage differs"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_harbor_aggregate_eval_placeholder(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    aggregate_path = run / "harbor" / "jobs" / "job-01" / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["stats"]["evals"] = {}
    _write_json(aggregate_path, aggregate)

    with pytest.raises(VerificationError, match="eval identity differs"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_non_iso_harbor_aggregate_timestamp(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    aggregate_path = run / "harbor" / "jobs" / "job-01" / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["finished_at"] = "not-a-timestamp"
    _write_json(aggregate_path, aggregate)

    with pytest.raises(VerificationError, match="ISO-8601 timestamp"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_unreferenced_complete_harbor_job(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    source = run / "harbor" / "jobs" / "job-01"
    extra = run / "harbor" / "jobs" / "job-25-unreferenced"
    shutil.copytree(source, extra)
    extra_job_id = "00000000-0000-0000-0000-999999999998"

    aggregate_path = extra / "result.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["id"] = extra_job_id
    _write_json(aggregate_path, aggregate)

    child_path = extra / "trial-01" / "result.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))
    child["config"]["job_id"] = extra_job_id
    _write_json(child_path, child)

    with pytest.raises(VerificationError, match="job inventory differs"):
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


def test_rejects_score_blind_v8_auxiliary_call_policy_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["prior_amendment_v8"]["generated_runtime_config_change"][
        "title_agent_enabled"
    ] = True
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="preserved v8 protocol amendment drifted"):
        pilot_verifier._validate_protocol(path)


def test_rejects_preserved_v7_amendment_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["prior_amendment_v7"]["diagnostic_artifact_sha256"] = "0" * 64
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="preserved v7 protocol amendment drifted"):
        pilot_verifier._validate_protocol(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor_subsessions_enabled", True),
        ("automatic_checkpoint_enabled", True),
        ("automatic_cron_enabled", True),
        ("automatic_distill_enabled", True),
        ("automatic_dream_enabled", True),
        ("mcp_sampling_enabled", True),
        ("title_agent_enabled", True),
        ("next_prompt_prediction_enabled", True),
        ("unattested_model_calls_allowed", True),
    ],
)
def test_rejects_unattested_auxiliary_model_call_runtime_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: bool,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["runtime"]["mimocode"]["auxiliary_model_calls"][field] = value
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="MiMoCode runtime identity drifted"):
        pilot_verifier._validate_protocol(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("build_tool_allowlist", ["bash", "read", "write", "edit", "glob", "grep", "actor"]),
        ("compaction_auto_enabled", False),
        ("config_content_overlay", '{"mcp":{"host":{}}}'),
        ("disposable_home_environment", ["MIMOCODE_HOME"]),
        ("fixed_session_title", ""),
        ("mcp_servers_configured", True),
        ("proxy_session_affinity_header", "x-parent-session-id"),
        ("proxy_session_affinity_required", False),
        ("pure_mode_enabled", False),
        ("root_session_only", False),
    ],
)
def test_rejects_mimocode_execution_isolation_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["runtime"]["mimocode"]["execution_isolation"][field] = value
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="MiMoCode runtime identity drifted"):
        pilot_verifier._validate_protocol(path)


def test_rejects_guard_proxy_root_session_protocol_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["runtime"]["guard_proxy"]["root_session_binding"]["full_pilot_limit"] = 23
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="runtime guard-proxy identity drifted"):
        pilot_verifier._validate_protocol(path)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("model_route", "request_model", "qwen/qwen3.8-flash"),
        ("route_contract", "response_provider", "auto"),
        ("provider", "only", ["auto"]),
    ],
)
def test_rejects_score_blind_v8_model_or_provider_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    field: str,
    value: object,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if section == "model_route":
        protocol["model_route"][field] = value
    elif section == "route_contract":
        protocol["model_route"]["route_contract"][field] = value
    else:
        protocol["model_route"]["route_contract"]["provider"][field] = value
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="protocol .*route.* drifted"):
        pilot_verifier._validate_protocol(path)


def test_rejects_harbor_concurrency_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    protocol["resources"]["harbor_concurrency"] = 2
    path = tmp_path / "protocol.json"
    _write_json(path, protocol)
    monkeypatch.setattr(pilot_verifier, "_repo_root", lambda _path: REPO_ROOT)
    with pytest.raises(VerificationError, match="protocol resource or budget guard drifted"):
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("root_session_binding_enabled", False),
        ("root_session_rejections", 1),
        ("root_sessions_limit", 23),
        ("root_sessions_observed", 23),
    ],
)
def test_rejects_guard_proxy_root_session_binding_drift(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    run, before, after = _fixture(tmp_path)
    health_path = run / "evidence" / "guard-proxy-health.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health[field] = value
    _write_json(health_path, health)
    with pytest.raises(VerificationError, match="completed-run counters are inconsistent"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_accepts_content_free_profile_for_none_without_tools(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    health_path = run / "evidence" / "guard-proxy-health.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health["normalizations"]["tool_choice_none_to_no_tools"] = 3
    health["request_profiles"]["outbound_tool_choice"]["absent"] = 23
    health["request_profiles"]["outbound_tool_choice"]["none"] = 1
    _write_json(health_path, health)

    result, _, _ = verify_pilot(
        run_dir=run,
        protocol_path=PROTOCOL,
        usage_before=before,
        usage_after=after,
    )

    assert result["evidence"]["guard_proxy_health"]["normalizations"] == {
        "tool_choice_none_to_no_tools": 3
    }


@pytest.mark.parametrize("tamper", ["normalization_count", "outbound_none", "raw_dynamic_field"])
def test_rejects_guard_proxy_v6_request_profile_drift(tmp_path: Path, tamper: str) -> None:
    run, before, after = _fixture(tmp_path)
    health_path = run / "evidence" / "guard-proxy-health.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    if tamper == "normalization_count":
        health["normalizations"]["tool_choice_none_to_no_tools"] = 3
    elif tamper == "outbound_none":
        health["request_profiles"]["outbound_tool_choice"]["absent"] = 23
        health["request_profiles"]["outbound_tool_choice"]["none"] = 1
    else:
        health["request_profiles"]["raw_request_sha256"] = "0" * 64
    _write_json(health_path, health)
    with pytest.raises(VerificationError, match="guard-proxy (request-profile|normalization)"):
        verify_pilot(run_dir=run, protocol_path=PROTOCOL, usage_before=before, usage_after=after)


def test_rejects_final_upstream_404(tmp_path: Path) -> None:
    run, before, after = _fixture(tmp_path)
    health_path = run / "evidence" / "guard-proxy-health.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health["upstream_errors"] = 1
    health["upstream_error_classes"]["http_4xx"] = 1
    _write_json(health_path, health)
    with pytest.raises(VerificationError, match="completed-run counters are inconsistent"):
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
