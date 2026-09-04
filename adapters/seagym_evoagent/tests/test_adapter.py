from __future__ import annotations

import asyncio
import copy
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from seagym_evoagent.baseline import EvoAgentSEAGymBaseline
from seagym_evoagent.canonical import atomic_write_json, read_json, sha256_file, sha256_json
from seagym_evoagent.evidence import (
    IncompleteHarborTrainEvidence,
    INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE,
    MAX_ATIF_BYTES,
    MAX_FAILURE_RECEIPT_BYTES,
    MAX_HARBOR_JSON_BYTES,
    NO_USABLE_ATIF_SKIP_CODE,
    project_train_batch,
)
from seagym_evoagent._compat import NonZeroAgentExitCodeError
from seagym_evoagent.harbor_agent import (
    ADAPTER_VERSION,
    ATTESTATION_FILENAME,
    ATTESTATION_SCHEMA,
    FAILURE_RECEIPT_FILENAME,
    MIMOCODE_AND_SANITIZER_EXIT,
    MIMOCODE_FORCE_KILL_GRACE_SECONDS,
    MIMOCODE_PROCESS_EXIT,
    MIMOCODE_SANITIZATION_MARGIN_SECONDS,
    SANITIZER_REJECT_EXIT,
    EvoAgentMiMo,
)
from seagym_evoagent.mimocode import (
    HARBOR_RUNTIME_COMMIT,
    MIMOCODE_ARCHIVE_ENV,
    MIMOCODE_ARCHIVE_SHA256,
    MIMOCODE_ARCHIVE_URL,
    MIMOCODE_SESSION_TITLE,
    MIMOCODE_VERSION,
    SEAGYM_COMMIT,
    locked_mimocode_config,
    runtime_env,
)
from seagym_evoagent.models import (
    CANONICAL_MODEL_ID,
    HARBOR_MODEL_ID,
    UPDATE_MODEL_ID,
    HarnessSnapshot,
    default_a0,
)
from seagym_evoagent.openrouter import (
    ModelUsage,
    OpenRouterStructuredClient,
    StructuredCompletion,
    safe_probe_failure_code,
)
from seagym_evoagent.routing import expected_route_contract
from seagym_evoagent.runtime_sanitizer import (
    sanitize_runtime_jsonl,
    write_runtime_failure_receipt,
)

try:
    from harbor.models.agent.context import AgentContext as HarborAgentContext
except ImportError:  # Optional dependency is installed in the real workflow.
    HarborAgentContext = None


CANARY = "private-task-CANARY-77"
SECRET = "sk-or-v1-this-must-never-persist-123456"
PROXY_TOKEN = "evoagent-local-proxy-v1-" + "a" * 64


def candidate_payload(*, max_iterations: int = 11) -> dict[str, object]:
    payload = copy.deepcopy(default_a0().components.to_dict())
    payload["policy"]["max_iterations"] = max_iterations
    return payload


class FakeClient:
    def __init__(self, candidate: dict[str, object]) -> None:
        self.candidate = candidate
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> StructuredCompletion:
        self.calls.append(kwargs)
        return StructuredCompletion(
            candidate=self.candidate,
            usage=ModelUsage(100, 20, 120, 0.0025),
            request_sha256="1" * 64,
            response_sha256="2" * 64,
            served_model_id=CANONICAL_MODEL_ID,
            provider="Xiaomi",
        )


def write_raw_atif(
    root: Path,
    *,
    snapshot: HarnessSnapshot | None = None,
    seed: int = 43,
    trial_name: str = "trial-a",
    task_id: str = CANARY,
) -> Path:
    snapshot = snapshot or default_a0()
    result_path = _write_harbor_result_identity(
        root,
        trial_name=trial_name,
        task_id=task_id,
        errored=False,
    )
    trial = result_path.parent
    atif = trial / "agent" / "trajectory.json"
    atif.parent.mkdir(parents=True, exist_ok=True)
    identity = {
        "api_model_id": UPDATE_MODEL_ID,
        "seed": seed,
        "snapshot_hash": snapshot.snapshot_sha256,
        "component_hashes": dict(snapshot.component_sha256),
        "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
        "route_contract_sha256": sha256_json(expected_route_contract()),
    }
    atomic_write_json(
        atif,
        {
            "schema_version": "ATIF-v1.7",
            "agent": {
                "name": "seagym-evoagent-mimocode",
                "version": ADAPTER_VERSION,
                "model_name": HARBOR_MODEL_ID,
                "extra": identity,
            },
            "steps": [
                {
                    "step_id": 1,
                    "source": "system",
                    "message": "",
                    "extra": {"status": "sanitized"},
                },
                {
                    "step_id": 2,
                    "source": "agent",
                    "message": "",
                    "model_name": HARBOR_MODEL_ID,
                    "metrics": {
                        "prompt_tokens": 25,
                        "completion_tokens": 5,
                        "cached_tokens": 0,
                        "cost_usd": 0.0005,
                    },
                    "llm_call_count": 1,
                    "tool_calls": [
                        {
                            "tool_call_id": "tool-000001",
                            "function_name": "bash",
                            "arguments": {},
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "source_call_id": "tool-000001",
                                "content": "status:success",
                            }
                        ]
                    },
                    "extra": {"status": "success"},
                },
            ],
            "final_metrics": {
                "total_steps": 2,
                "total_prompt_tokens": 25,
                "total_completion_tokens": 5,
                "total_cached_tokens": 0,
                "total_cost_usd": 0.0005,
            },
            "extra": identity,
        },
    )
    atif_sha256 = sha256_file(atif)
    usage = {
        "prompt_tokens": 25,
        "completion_tokens": 5,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0005,
    }
    unsigned = {
        "schema_version": ATTESTATION_SCHEMA,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "component_sha256": dict(snapshot.component_sha256),
        "atif_sha256": atif_sha256,
        "route_contract_sha256": sha256_json(expected_route_contract()),
        "model": {
            "api_id": UPDATE_MODEL_ID,
            "harbor_id": HARBOR_MODEL_ID,
            "openrouter_provider": "xiaomi/fp8",
            "fallbacks_allowed": False,
            "reasoning_enabled": False,
            "credential_transport": "local_guard_proxy_v1",
        },
        "seed": seed,
        "runtime": {
            "adapter_version": ADAPTER_VERSION,
            "mimocode_version": MIMOCODE_VERSION,
            "mimocode_archive_sha256": MIMOCODE_ARCHIVE_SHA256,
            "seagym_commit": SEAGYM_COMMIT,
            "harbor_commit": HARBOR_RUNTIME_COMMIT,
        },
        "usage": usage,
        "runtime_failure_receipt_sha256": None,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "reasoning_persisted": False,
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
        "activation_claimed": False,
    }
    attestation_sha256 = sha256_json(unsigned)
    atomic_write_json(
        atif.parent / ATTESTATION_FILENAME,
        {**unsigned, "attestation_sha256": attestation_sha256},
    )
    result = read_json(result_path)
    result["agent_result"]["rollout_details"] = None
    result["agent_result"]["metadata"] = {
        "attestation_sha256": attestation_sha256,
        "atif_sha256": atif_sha256,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "model_id": UPDATE_MODEL_ID,
        "seed": seed,
        "route_contract_sha256": sha256_json(expected_route_contract()),
        "privacy_projection": True,
    }
    atomic_write_json(result_path, result)
    return result_path


def _write_harbor_result_identity(
    root: Path,
    *,
    trial_name: str,
    task_id: str,
    errored: bool,
) -> Path:
    job_dir = root / f"job-{trial_name}"
    trial = job_dir / trial_name
    result_path = trial / "result.json"
    task_name = task_id
    task_leaf = task_id.rsplit("/", 1)[-1]
    patched_job_dir = root / "_patched_tasksets" / job_dir.name
    task_dir = patched_job_dir / task_leaf
    task_dir.mkdir(parents=True, exist_ok=True)
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    task_path = task_dir.resolve().as_posix()
    trial_id = str(uuid5(NAMESPACE_URL, f"trial:{trial_name}"))
    job_id = str(uuid5(NAMESPACE_URL, f"job:{trial_name}"))
    task_checksum = sha256_json({"task_id": task_id})
    agent_config = {
        "import_path": "seagym_evoagent.harbor_agent:EvoAgentMiMo",
        "model_name": HARBOR_MODEL_ID,
    }
    config = {
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
        "agent": agent_config,
        "environment": {},
        "verifier": {},
        "artifacts": [],
        "extra_instruction_paths": [],
    }
    reward = 0.0 if errored else 1.0
    agent_result = {
        "n_input_tokens": 0 if errored else 25,
        "n_cache_tokens": 0,
        "n_output_tokens": 0 if errored else 5,
        "cost_usd": 0.0 if errored else 0.0005,
        "rollout_details": None,
        "metadata": {},
    }
    result = {
        "id": trial_id,
        "task_name": task_name,
        "trial_name": trial_name,
        "trial_uri": trial.resolve().as_uri(),
        "source": job_dir.name,
        "task_id": {"path": task_path},
        "task_checksum": task_checksum,
        "config": config,
        "agent_info": {
            "name": "evoagent-mimo",
            "version": ADAPTER_VERSION,
            "model_info": {"name": UPDATE_MODEL_ID, "provider": "openrouter"},
        },
        "agent_result": agent_result,
        "exception_info": (
            {
                "exception_type": "HarborErroredResult",
                "exception_message": "bounded_error",
                "exception_traceback": "",
                "occurred_at": "2026-09-01T00:00:00Z",
            }
            if errored
            else None
        ),
        "verifier_result": {"rewards": {"reward": reward}},
        "started_at": "2026-09-01T00:00:00Z",
        "finished_at": "2026-09-01T00:00:04.500000Z",
        "environment_setup": (
            None
            if errored
            else {
                "started_at": "2026-09-01T00:00:00Z",
                "finished_at": "2026-09-01T00:00:00.500000Z",
            }
        ),
        "agent_setup": (
            None
            if errored
            else {
                "started_at": "2026-09-01T00:00:00.500000Z",
                "finished_at": "2026-09-01T00:00:01Z",
            }
        ),
        "agent_execution": (
            None
            if errored
            else {
                "started_at": "2026-09-01T00:00:01Z",
                "finished_at": "2026-09-01T00:00:04Z",
            }
        ),
        "verifier": (
            None
            if errored
            else {
                "started_at": "2026-09-01T00:00:04Z",
                "finished_at": "2026-09-01T00:00:04.500000Z",
            }
        ),
        "step_results": None,
    }
    atomic_write_json(trial / "config.json", config)
    atomic_write_json(result_path, result)
    atomic_write_json(
        job_dir / "config.json",
        {
            "job_name": job_dir.name,
            "jobs_dir": root.resolve().as_posix(),
            "n_attempts": 1,
            "n_concurrent_trials": 1,
            "retry": {"max_retries": 0},
            "tasks": [],
            "datasets": [
                {
                    "path": patched_job_dir.resolve().as_posix(),
                    "task_names": [task_leaf],
                    "n_tasks": 1,
                }
            ],
            "agents": [agent_config],
        },
    )
    _write_harbor_aggregate(result_path)
    return result_path


def _write_harbor_aggregate(result_path: Path) -> None:
    result = read_json(result_path)
    job_dir = result_path.parent.parent
    agent_result = result["agent_result"]
    reward = float(result["verifier_result"]["rewards"]["reward"])
    errored = result["exception_info"] is not None
    eval_key = (
        f"{result['agent_info']['name']}__"
        f"{result['agent_info']['model_info']['name']}__{result['source']}"
    )
    atomic_write_json(
        job_dir / "result.json",
        {
            "finished_at": "2026-09-01T00:00:04.500000Z",
            "id": result["config"]["job_id"],
            "n_total_trials": 1,
            "started_at": "2026-09-01T00:00:00Z",
            "stats": {
                "cost_usd": agent_result["cost_usd"],
                "evals": {
                    eval_key: {
                        "exception_stats": (
                            {
                                result["exception_info"]["exception_type"]: [
                                    result["trial_name"]
                                ]
                            }
                            if errored
                            else {}
                        ),
                        "metrics": [{"mean": reward}],
                        "n_errors": int(errored),
                        "n_trials": 1,
                        "pass_at_k": {},
                        "reward_stats": {
                            "reward": {str(reward): [result["trial_name"]]}
                        },
                    }
                },
                "n_cache_tokens": agent_result["n_cache_tokens"],
                "n_cancelled_trials": 0,
                "n_completed_trials": 1,
                "n_errored_trials": int(errored),
                "n_input_tokens": agent_result["n_input_tokens"],
                "n_output_tokens": agent_result["n_output_tokens"],
                "n_pending_trials": 0,
                "n_retries": 0,
                "n_running_trials": 0,
            },
            "updated_at": "2026-09-01T00:00:04.500000Z",
        },
    )


def train_batch(
    root: Path,
    *,
    snapshot: HarnessSnapshot | None = None,
    trial_name: str = "trial-a",
    task_id: str = CANARY,
) -> SimpleNamespace:
    result_path = write_raw_atif(
        root,
        snapshot=snapshot,
        trial_name=trial_name,
        task_id=task_id,
    )
    return train_batch_from_result(result_path, task_id=task_id)


def train_batch_from_result(
    result_path: Path,
    *,
    task_id: str = CANARY,
) -> SimpleNamespace:
    result = read_json(result_path)
    trial_name = result["trial_name"]
    trajectory = SimpleNamespace(
        task_id=task_id,
        attempt_id=trial_name,
        view_name="train",
        mode="train",
        success=True,
        reward=1.0,
        score=1.0,
        rewards={"reward": 1.0},
        cost={
            "n_input_tokens": 25.0,
            "n_cache_tokens": 0.0,
            "n_output_tokens": 5.0,
            "cost_usd": 0.0005,
        },
        runtime_seconds=4.5,
        error=None,
        refs={
            "result_path": str(result_path),
            "job_dir": str(result_path.parent.parent),
            "trial_name": trial_name,
            "trial_uri": result["trial_uri"],
            "job_id": result["config"]["job_id"],
            "harbor_source": result["source"],
            "harbor_task_name": result["task_name"],
            "task_checksum": result["task_checksum"],
            "harbor_returncode": 0,
            "harbor_stdout": f"raw {SECRET}",
        },
    )
    return SimpleNamespace(
        trajectories=[trajectory],
        task_ids=[task_id],
        view_name="train",
        mode="train",
        batch_index=0,
        epoch=0,
        refs={"raw": SECRET},
    )


def failed_train_trajectory(*, refs: dict[str, object] | None = None) -> SimpleNamespace:
    bound_refs = {} if refs is None else dict(refs)
    raw_result = bound_refs.get("result_path")
    if isinstance(raw_result, str) and raw_result:
        bound_refs.setdefault("job_dir", str(Path(raw_result).parent.parent))
        result = read_json(Path(raw_result))
        attempt_id = result["trial_name"]
        bound_refs.setdefault("trial_name", result["trial_name"])
        bound_refs.setdefault("trial_uri", result["trial_uri"])
        bound_refs.setdefault("job_id", result["config"]["job_id"])
        bound_refs.setdefault("harbor_source", result["source"])
        bound_refs.setdefault("harbor_task_name", result["task_name"])
        bound_refs.setdefault("task_checksum", result["task_checksum"])
        bound_refs.setdefault("harbor_returncode", 0)
        task_id = result["task_name"]
        agent_result = result.get("agent_result") or {}
        cost = {
            key: float(agent_result[key])
            for key in (
                "n_input_tokens",
                "n_cache_tokens",
                "n_output_tokens",
                "cost_usd",
                "total_tokens",
            )
            if isinstance(agent_result.get(key), int | float)
        }
        error = str(result["exception_info"])
        started_at = result.get("started_at")
        finished_at = result.get("finished_at")
        runtime_seconds = (
            (
                datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            ).total_seconds()
            if isinstance(started_at, str)
            and started_at
            and isinstance(finished_at, str)
            and finished_at
            else None
        )
    else:
        attempt_id = "failed-attempt"
        task_id = "failed-task"
        cost = {}
        error = f"Harbor trial failed {SECRET}"
        runtime_seconds = None
    return SimpleNamespace(
        task_id=task_id,
        attempt_id=attempt_id,
        view_name="train",
        mode="train",
        success=False,
        reward=0.0,
        score=0.0,
        rewards={"reward": 0.0},
        cost=cost,
        runtime_seconds=runtime_seconds,
        error=error,
        refs=bound_refs,
    )


def write_unattested_harbor_result(
    root: Path,
    *,
    trial_name: str = "errored-result",
    task_id: str = "failed-task",
) -> Path:
    return _write_harbor_result_identity(
        root,
        trial_name=trial_name,
        task_id=task_id,
        errored=True,
    )


def write_failure_receipt(
    root: Path,
    *,
    snapshot: HarnessSnapshot | None = None,
    seed: int = 43,
    trial_name: str = "failed-trial",
    atif_present: bool = False,
    tamper: str | None = None,
) -> tuple[Path, Path]:
    snapshot = snapshot or default_a0()
    result_path = _write_harbor_result_identity(
        root,
        trial_name=trial_name,
        task_id="failed-task" if trial_name != "trial-a" else CANARY,
        errored=True,
    )
    trial = result_path.parent
    unsigned: dict[str, object] = {
        "schema_version": "evoagent-runtime-failure-v1",
        "failure_class": "runtime_sanitization_failed",
        "failure_stage": "sanitize",
        "mimocode_exit_class": "success",
        "snapshot_sha256": snapshot.snapshot_sha256,
        "component_sha256": dict(snapshot.component_sha256),
        "route_contract_sha256": sha256_json(expected_route_contract()),
        "model": {"api_id": UPDATE_MODEL_ID, "harbor_id": HARBOR_MODEL_ID},
        "seed": seed,
        "runtime": {"name": "mimocode", "version": MIMOCODE_VERSION},
        "atif_present": atif_present,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "reasoning_content_persisted": False,
    }
    receipt: dict[str, object] = {**unsigned, "receipt_sha256": sha256_json(unsigned)}
    if tamper == "hash":
        receipt["failure_stage"] = "mimocode"
    elif tamper == "snapshot":
        receipt["snapshot_sha256"] = "0" * 64
        receipt["receipt_sha256"] = sha256_json({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    elif tamper == "atif_present":
        receipt["atif_present"] = True
        receipt["receipt_sha256"] = sha256_json({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    elif tamper == "classification":
        receipt["mimocode_exit_class"] = "nonzero"
        receipt["receipt_sha256"] = sha256_json({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    receipt_path = trial / "agent" / FAILURE_RECEIPT_FILENAME
    atomic_write_json(receipt_path, receipt)
    result = read_json(result_path)
    if atif_present:
        attestation_path = trial / "agent" / ATTESTATION_FILENAME
        attestation = read_json(attestation_path)
        attestation["runtime_failure_receipt_sha256"] = receipt["receipt_sha256"]
        attestation_unsigned = dict(attestation)
        attestation_unsigned.pop("attestation_sha256")
        attestation["attestation_sha256"] = sha256_json(attestation_unsigned)
        atomic_write_json(attestation_path, attestation)
        usage = attestation["usage"]
        result["agent_result"] = {
            "n_input_tokens": usage["prompt_tokens"],
            "n_cache_tokens": usage["cached_tokens"],
            "n_output_tokens": usage["completion_tokens"],
            "cost_usd": usage["cost_usd"],
            "rollout_details": None,
            "metadata": {
                "attestation_sha256": attestation["attestation_sha256"],
                "atif_sha256": attestation["atif_sha256"],
                "snapshot_sha256": attestation["snapshot_sha256"],
                "model_id": UPDATE_MODEL_ID,
                "seed": seed,
                "route_contract_sha256": attestation["route_contract_sha256"],
                "privacy_projection": True,
                "runtime_failure_receipt_sha256": receipt["receipt_sha256"],
            },
        }
    else:
        result["agent_result"] = {
            "n_input_tokens": 0,
            "n_cache_tokens": 0,
            "n_output_tokens": 0,
            "cost_usd": 0.0,
            "rollout_details": None,
            "metadata": {
                "runtime_failure_receipt_sha256": receipt["receipt_sha256"],
                "runtime_failure_class": receipt["failure_class"],
                "runtime_failure_stage": receipt["failure_stage"],
                "mimocode_exit_class": receipt["mimocode_exit_class"],
                "snapshot_sha256": receipt["snapshot_sha256"],
                "model_id": UPDATE_MODEL_ID,
                "seed": seed,
                "route_contract_sha256": receipt["route_contract_sha256"],
                "privacy_projection": True,
            },
        }
    atomic_write_json(result_path, result)
    _write_harbor_aggregate(result_path)
    return result_path, receipt_path


def write_outer_checkpoint(
    checkpoint_dir: Path,
    *,
    checkpoint_id: str,
    checkpoint_type: str,
    baseline_manifest: dict[str, object],
    run_id: str = "pilot-run",
) -> dict[str, object]:
    update_index = int(baseline_manifest["update_index"])
    outer: dict[str, object] = {
        "checkpoint_id": checkpoint_id,
        "checkpoint_type": checkpoint_type,
        "created_at": "2026-08-29T00:00:00+00:00",
        "run_id": run_id,
        "experiment_id": "evoagent-seagym-pilot",
        "trainer_state": {
            "epoch": 0 if update_index == 0 else 1,
            "train_batch_index": update_index,
            "global_step": update_index,
            "updates_completed": update_index,
            "num_train_tasks_seen": update_index * 3,
            "checkpoint_id": checkpoint_id,
            "previous_update_validation_results": [],
        },
        "metadata": {"kind": checkpoint_type},
        "refs": {
            "baseline_state": baseline_manifest["state_ref"],
            "batch_plan": "/original/run/inputs/batch_plan.json",
            "config": "/original/run/inputs/experiment_config.json",
        },
        "baseline": baseline_manifest,
    }
    atomic_write_json(checkpoint_dir / "checkpoint.json", outer)
    return outer


class BaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _assert_update_fails_closed(
        self,
        *,
        baseline: EvoAgentSEAGymBaseline,
        state: object,
        batch: object,
        client: FakeClient,
        case_root: Path,
    ) -> None:
        with self.assertRaises(ValueError):
            baseline.update(batch, state)
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 0)
        self.assertEqual(list((case_root / "state" / "attempts").glob("*.json")), [])

    def test_train_projection_requires_a_regular_result_bound_to_its_trial(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        result_path = Path(batch.trajectories[0].refs["result_path"])
        result_path.unlink()

        with self.assertRaisesRegex(ValueError, "result|missing"):
            baseline.update(batch, state)

    def test_train_projection_rejects_cross_trial_result_reference(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor", trial_name="trial-a")
        other = train_batch(self.root / "harbor", trial_name="trial-b")
        batch.trajectories[0].refs = dict(other.trajectories[0].refs)

        with self.assertRaisesRegex(ValueError, "attempt_id|trial"):
            baseline.update(batch, state)

    def test_train_projection_rejects_cross_task_result_identity(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        result_path = Path(batch.trajectories[0].refs["result_path"])
        payload = read_json(result_path)
        other_task = self.root / "harbor" / "_tasks" / "other-task"
        other_task.mkdir(parents=True)
        other_path = other_task.resolve().as_posix()
        payload["task_name"] = "other-task"
        payload["task_id"] = {"path": other_path}
        payload["config"]["task"] = {"path": other_path}
        atomic_write_json(result_path, payload)

        with self.assertRaisesRegex(ValueError, "task identity"):
            baseline.update(batch, state)

    def test_train_projection_accepts_canonical_task_name_with_local_path_leaf(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        task_id = "terminal-bench/fix-git"
        batch = train_batch(self.root / "harbor", task_id=task_id)
        result_path = Path(batch.trajectories[0].refs["result_path"])
        payload = read_json(result_path)

        self.assertEqual(payload["task_name"], task_id)
        self.assertEqual(Path(payload["task_id"]["path"]).name, "fix-git")
        result = baseline.update(batch, state)

        self.assertEqual(result.status, "updated")
        self.assertEqual(len(client.calls), 1)

    def test_train_projection_rejects_leaf_alias_for_canonical_task_name(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(
            self.root / "harbor",
            task_id="terminal-bench/fix-git",
        )
        result_path = Path(batch.trajectories[0].refs["result_path"])
        payload = read_json(result_path)
        payload["task_name"] = "fix-git"
        batch.trajectories[0].refs["harbor_task_name"] = "fix-git"
        atomic_write_json(result_path, payload)

        with self.assertRaisesRegex(ValueError, "task identity"):
            baseline.update(batch, state)
        self.assertEqual(client.calls, [])

    def test_train_projection_rejects_wrong_local_leaf_for_canonical_task_name(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(
            self.root / "harbor",
            task_id="terminal-bench/fix-git",
        )
        result_path = Path(batch.trajectories[0].refs["result_path"])
        payload = read_json(result_path)
        wrong_task = result_path.parent.parent.parent / "wrong-task"
        wrong_task.mkdir()
        wrong_path = wrong_task.resolve().as_posix()
        payload["task_id"] = {"path": wrong_path}
        payload["config"]["task"] = {"path": wrong_path}
        atomic_write_json(result_path, payload)

        with self.assertRaisesRegex(ValueError, "task binding"):
            baseline.update(batch, state)
        self.assertEqual(client.calls, [])

    def test_train_projection_recomputes_every_pinned_normalized_field_before_call(self) -> None:
        tamper_values = {
            "success": False,
            "score": 0.25,
            "reward": 0.25,
            "rewards": {},
            "cost": {},
            "runtime_seconds": 0.0,
            "error": "tampered_error",
        }
        for field, value in tamper_values.items():
            with self.subTest(field=field):
                case_root = self.root / f"normalized-{field}"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=case_root / "harbor",
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                batch = train_batch(case_root / "harbor")
                setattr(batch.trajectories[0], field, value)

                with self.assertRaises(ValueError):
                    baseline.update(batch, state)

                self.assertEqual(client.calls, [])
                self.assertEqual(baseline.update_index, 0)
                self.assertEqual(list((case_root / "state" / "attempts").glob("*.json")), [])

    def test_train_projection_requires_exact_finite_verifier_reward_before_call(self) -> None:
        missing = object()
        invalid_verifier_results = {
            "missing": missing,
            "null": None,
            "empty": {},
            "extra_verifier_key": {
                "rewards": {"reward": 0.5},
                "unexpected": True,
            },
            "missing_rewards": {"unexpected": {}},
            "null_rewards": {"rewards": None},
            "empty_rewards": {"rewards": {}},
            "extra_reward_key": {"rewards": {"reward": 0.5, "unexpected": 0.0}},
            "non_numeric_reward": {"rewards": {"reward": "0.5"}},
            "boolean_reward": {"rewards": {"reward": True}},
            "non_finite_reward": {"rewards": {"reward": float("nan")}},
        }
        for case, verifier_result in invalid_verifier_results.items():
            with self.subTest(case=case):
                case_root = self.root / f"verifier-reward-{case}"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=case_root / "harbor",
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                batch = train_batch(case_root / "harbor")
                result_path = Path(batch.trajectories[0].refs["result_path"])
                payload = read_json(result_path)
                if verifier_result is missing:
                    payload.pop("verifier_result")
                else:
                    payload["verifier_result"] = verifier_result
                if case == "non_finite_reward":
                    result_path.write_text(
                        json.dumps(payload, allow_nan=True, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                else:
                    atomic_write_json(result_path, payload)

                with self.assertRaisesRegex(
                    ValueError,
                    "non-finite|verifier_result|reward|root schema",
                ):
                    baseline.update(batch, state)

                self.assertEqual(client.calls, [])
                self.assertEqual(baseline.update_index, 0)
                self.assertEqual(list((case_root / "state" / "attempts").glob("*.json")), [])

    def test_train_projection_requires_valid_completed_harbor_timing_before_call(self) -> None:
        tamper_cases = (
            ("missing_started_at", "pop", "started_at"),
            ("missing_finished_at", "pop", "finished_at"),
            ("malformed_started_at", "set", ("started_at", "not-a-timestamp")),
            ("naive_started_at", "set", ("started_at", "2026-09-01T00:00:00")),
            ("reversed", "set", ("finished_at", "2026-08-31T23:59:59Z")),
            ("missing_environment_setup", "pop", "environment_setup"),
            ("missing_agent_setup", "pop", "agent_setup"),
            ("missing_agent_execution", "pop", "agent_execution"),
            ("missing_verifier", "pop", "verifier"),
            ("missing_phase_finished_at", "phase_pop", ("verifier", "finished_at")),
        )
        for case, operation, argument in tamper_cases:
            with self.subTest(case=case):
                case_root = self.root / f"timing-{case}"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=case_root / "harbor",
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                batch = train_batch(case_root / "harbor")
                result_path = Path(batch.trajectories[0].refs["result_path"])
                payload = read_json(result_path)
                if operation == "pop":
                    payload.pop(argument)
                elif operation == "set":
                    key, value = argument
                    payload[key] = value
                else:
                    phase, key = argument
                    payload[phase].pop(key)
                atomic_write_json(result_path, payload)

                with self.assertRaisesRegex(
                    ValueError,
                    "timing|timestamp|timezone|started_at|finished_at|root schema",
                ):
                    baseline.update(batch, state)

                self.assertEqual(client.calls, [])
                self.assertEqual(baseline.update_index, 0)
                self.assertEqual(list((case_root / "state" / "attempts").glob("*.json")), [])

    def test_train_projection_requires_binary_reward_and_zero_for_errors(self) -> None:
        for case in ("fractional", "errored_nonzero"):
            with self.subTest(case=case):
                case_root = self.root / f"binary-reward-{case}"
                harbor_root = case_root / "harbor"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=harbor_root,
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                if case == "fractional":
                    batch = train_batch(harbor_root)
                    result_path = Path(batch.trajectories[0].refs["result_path"])
                    payload = read_json(result_path)
                    payload["verifier_result"] = {"rewards": {"reward": 0.5}}
                else:
                    result_path = write_unattested_harbor_result(harbor_root)
                    failed = failed_train_trajectory(refs={"result_path": str(result_path)})
                    batch = SimpleNamespace(
                        trajectories=[failed],
                        task_ids=[failed.task_id],
                        view_name="train",
                        mode="train",
                    )
                    payload = read_json(result_path)
                    payload["verifier_result"] = {"rewards": {"reward": 1.0}}
                atomic_write_json(result_path, payload)

                self._assert_update_fails_closed(
                    baseline=baseline,
                    state=state,
                    batch=batch,
                    client=client,
                    case_root=case_root,
                )

    def test_train_projection_mirrors_exact_harbor_child_contract(self) -> None:
        cases = (
            "extra_root",
            "trial_uri",
            "checksum",
            "config_schema",
            "agent_info",
            "agent_context",
            "step_results",
        )
        for case in cases:
            with self.subTest(case=case):
                case_root = self.root / f"child-contract-{case}"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=case_root / "harbor",
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                batch = train_batch(case_root / "harbor")
                result_path = Path(batch.trajectories[0].refs["result_path"])
                payload = read_json(result_path)
                if case == "extra_root":
                    payload["unexpected"] = None
                elif case == "trial_uri":
                    payload["trial_uri"] = result_path.parent.parent.as_uri()
                elif case == "checksum":
                    payload["task_checksum"] = "not-a-checksum"
                elif case == "config_schema":
                    payload["config"]["unexpected"] = True
                elif case == "agent_info":
                    payload["agent_info"]["name"] = "other-agent"
                elif case == "agent_context":
                    payload["agent_result"]["raw_response"] = ""
                else:
                    payload["step_results"] = []
                atomic_write_json(result_path, payload)

                self._assert_update_fails_closed(
                    baseline=baseline,
                    state=state,
                    batch=batch,
                    client=client,
                    case_root=case_root,
                )

    def test_errored_child_rejects_malformed_non_null_phase_timing(self) -> None:
        case_root = self.root / "errored-phase-timing"
        harbor_root = case_root / "harbor"
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=case_root / "state",
            atif_root=harbor_root,
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(case_root / "run")
        result_path = write_unattested_harbor_result(harbor_root)
        payload = read_json(result_path)
        payload["agent_execution"] = {
            "started_at": "not-an-iso-timestamp",
            "finished_at": "2026-09-01T00:00:01Z",
        }
        atomic_write_json(result_path, payload)
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        self._assert_update_fails_closed(
            baseline=baseline,
            state=state,
            batch=batch,
            client=client,
            case_root=case_root,
        )

    def test_train_projection_rejects_agent_inventory_drift_for_every_shape(self) -> None:
        for case in ("normal_file", "normal_directory", "atif_receipt", "receipt_only", "link"):
            with self.subTest(case=case):
                case_root = self.root / f"agent-inventory-{case}"
                harbor_root = case_root / "harbor"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=harbor_root,
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                if case in {"normal_file", "normal_directory", "link"}:
                    batch = train_batch(harbor_root)
                    result_path = Path(batch.trajectories[0].refs["result_path"])
                elif case == "atif_receipt":
                    batch = train_batch(harbor_root)
                    result_path, _receipt = write_failure_receipt(
                        harbor_root,
                        trial_name="trial-a",
                        atif_present=True,
                    )
                    failed = failed_train_trajectory(refs=batch.trajectories[0].refs)
                    batch.trajectories[0] = failed
                    batch.task_ids[0] = failed.task_id
                else:
                    result_path, _receipt = write_failure_receipt(harbor_root)
                    failed = failed_train_trajectory(refs={"result_path": str(result_path)})
                    batch = SimpleNamespace(
                        trajectories=[failed],
                        task_ids=[failed.task_id],
                        view_name="train",
                        mode="train",
                    )
                agent_dir = result_path.parent / "agent"
                if case == "normal_directory":
                    (agent_dir / "unexpected").mkdir()
                elif case == "link":
                    try:
                        (agent_dir / "unexpected-link").symlink_to(result_path)
                    except OSError:
                        continue
                else:
                    (agent_dir / "unexpected.json").write_text("{}\n", encoding="utf-8")

                self._assert_update_fails_closed(
                    baseline=baseline,
                    state=state,
                    batch=batch,
                    client=client,
                    case_root=case_root,
                )

    def test_train_projection_mirrors_single_task_job_and_aggregate_contract(self) -> None:
        cases = (
            "returncode",
            "attempts",
            "concurrency",
            "retry",
            "dataset",
            "agent",
            "child_config",
            "extra_child",
            "aggregate_id",
            "aggregate_count",
            "aggregate_error",
            "aggregate_reward",
            "aggregate_usage",
        )
        for case in cases:
            with self.subTest(case=case):
                case_root = self.root / f"job-contract-{case}"
                harbor_root = case_root / "harbor"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=harbor_root,
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                batch = train_batch(harbor_root)
                result_path = Path(batch.trajectories[0].refs["result_path"])
                job_dir = result_path.parent.parent
                job_config_path = job_dir / "config.json"
                aggregate_path = job_dir / "result.json"
                if case == "returncode":
                    batch.trajectories[0].refs["harbor_returncode"] = 1
                elif case in {"attempts", "concurrency", "retry", "dataset", "agent"}:
                    job_config = read_json(job_config_path)
                    if case == "attempts":
                        job_config["n_attempts"] = 2
                    elif case == "concurrency":
                        job_config["n_concurrent_trials"] = 2
                    elif case == "retry":
                        job_config["retry"]["max_retries"] = 1
                    elif case == "dataset":
                        job_config["datasets"][0]["task_names"] = ["other-task"]
                    else:
                        job_config["agents"] = [{"import_path": "other:Agent"}]
                    atomic_write_json(job_config_path, job_config)
                elif case == "child_config":
                    child_config_path = result_path.parent / "config.json"
                    child_config = read_json(child_config_path)
                    child_config["timeout_multiplier"] = 2.0
                    atomic_write_json(child_config_path, child_config)
                elif case == "extra_child":
                    (job_dir / "unexpected-trial").mkdir()
                else:
                    aggregate = read_json(aggregate_path)
                    eval_stats = next(iter(aggregate["stats"]["evals"].values()))
                    if case == "aggregate_id":
                        aggregate["id"] = str(uuid5(NAMESPACE_URL, "other-job"))
                    elif case == "aggregate_count":
                        aggregate["stats"]["n_completed_trials"] = 0
                    elif case == "aggregate_error":
                        aggregate["stats"]["n_errored_trials"] = 1
                    elif case == "aggregate_reward":
                        eval_stats["metrics"] = [{"mean": 0.0}]
                    else:
                        aggregate["stats"]["n_input_tokens"] += 1
                    atomic_write_json(aggregate_path, aggregate)

                self._assert_update_fails_closed(
                    baseline=baseline,
                    state=state,
                    batch=batch,
                    client=client,
                    case_root=case_root,
                )

    def test_train_projection_scans_every_bound_artifact_for_credentials(self) -> None:
        for case in (
            "result",
            "job_config",
            "child_config",
            "aggregate",
            "atif",
            "attestation",
            "receipt",
        ):
            with self.subTest(case=case):
                case_root = self.root / f"secret-scan-{case}"
                harbor_root = case_root / "harbor"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=harbor_root,
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                if case == "receipt":
                    result_path, receipt_path = write_failure_receipt(harbor_root)
                    failed = failed_train_trajectory(refs={"result_path": str(result_path)})
                    batch = SimpleNamespace(
                        trajectories=[failed],
                        task_ids=[failed.task_id],
                        view_name="train",
                        mode="train",
                    )
                    receipt = read_json(receipt_path)
                    receipt["failure_class"] = SECRET
                    atomic_write_json(receipt_path, receipt)
                else:
                    batch = train_batch(harbor_root)
                    result_path = Path(batch.trajectories[0].refs["result_path"])
                    job_dir = result_path.parent.parent
                    agent_dir = result_path.parent / "agent"
                    if case == "result":
                        payload = read_json(result_path)
                        payload["config"]["environment"] = {"credential": SECRET}
                        atomic_write_json(result_path, payload)
                    elif case == "job_config":
                        path = job_dir / "config.json"
                        payload = read_json(path)
                        payload["credential"] = SECRET
                        atomic_write_json(path, payload)
                    elif case == "child_config":
                        path = result_path.parent / "config.json"
                        payload = read_json(path)
                        payload["environment"] = {"credential": SECRET}
                        atomic_write_json(path, payload)
                    elif case == "aggregate":
                        path = job_dir / "result.json"
                        payload = read_json(path)
                        payload["credential"] = SECRET
                        atomic_write_json(path, payload)
                    elif case == "atif":
                        path = agent_dir / "trajectory.json"
                        payload = read_json(path)
                        payload["steps"][0]["message"] = SECRET
                        atomic_write_json(path, payload)
                    else:
                        path = agent_dir / ATTESTATION_FILENAME
                        payload = read_json(path)
                        payload["credential"] = SECRET
                        atomic_write_json(path, payload)

                self._assert_update_fails_closed(
                    baseline=baseline,
                    state=state,
                    batch=batch,
                    client=client,
                    case_root=case_root,
                )

    def test_train_projection_rejects_every_oversized_bound_artifact(self) -> None:
        limits = {
            "result": MAX_HARBOR_JSON_BYTES,
            "job_config": MAX_HARBOR_JSON_BYTES,
            "child_config": MAX_HARBOR_JSON_BYTES,
            "aggregate": MAX_HARBOR_JSON_BYTES,
            "atif": MAX_ATIF_BYTES,
            "attestation": MAX_FAILURE_RECEIPT_BYTES,
            "receipt": MAX_FAILURE_RECEIPT_BYTES,
        }
        for case, limit in limits.items():
            with self.subTest(case=case):
                case_root = self.root / f"size-limit-{case}"
                harbor_root = case_root / "harbor"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=harbor_root,
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                if case == "receipt":
                    result_path, target = write_failure_receipt(harbor_root)
                    failed = failed_train_trajectory(refs={"result_path": str(result_path)})
                    batch = SimpleNamespace(
                        trajectories=[failed],
                        task_ids=[failed.task_id],
                        view_name="train",
                        mode="train",
                    )
                else:
                    batch = train_batch(harbor_root)
                    result_path = Path(batch.trajectories[0].refs["result_path"])
                    targets = {
                        "result": result_path,
                        "job_config": result_path.parent.parent / "config.json",
                        "child_config": result_path.parent / "config.json",
                        "aggregate": result_path.parent.parent / "result.json",
                        "atif": result_path.parent / "agent" / "trajectory.json",
                        "attestation": result_path.parent / "agent" / ATTESTATION_FILENAME,
                    }
                    target = targets[case]
                with target.open("wb") as handle:
                    handle.truncate(limit + 1)

                self._assert_update_fails_closed(
                    baseline=baseline,
                    state=state,
                    batch=batch,
                    client=client,
                    case_root=case_root,
                )

    def test_train_projection_requires_mandatory_job_refs_and_non_null_atif_timestamp(self) -> None:
        for case in ("job_dir", "task_checksum", "null_timestamp"):
            with self.subTest(case=case):
                case_root = self.root / f"mandatory-binding-{case}"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=case_root / "harbor",
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                batch = train_batch(case_root / "harbor")
                result_path = Path(batch.trajectories[0].refs["result_path"])
                if case in {"job_dir", "task_checksum"}:
                    batch.trajectories[0].refs.pop(case)
                else:
                    atif_path = result_path.parent / "agent" / "trajectory.json"
                    atif = read_json(atif_path)
                    atif["steps"][1]["timestamp"] = None
                    atomic_write_json(atif_path, atif)

                self._assert_update_fails_closed(
                    baseline=baseline,
                    state=state,
                    batch=batch,
                    client=client,
                    case_root=case_root,
                )

    def test_train_projection_rejects_reused_job_uuid_across_distinct_jobs(self) -> None:
        case_root = self.root / "duplicate-job-id"
        harbor_root = case_root / "harbor"
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=case_root / "state",
            atif_root=harbor_root,
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(case_root / "run")
        normal = train_batch(harbor_root, trial_name="normal")
        failed_result, _receipt = write_failure_receipt(
            harbor_root,
            trial_name="failed",
        )
        failed = failed_train_trajectory(refs={"result_path": str(failed_result)})
        first_job_id = read_json(
            Path(normal.trajectories[0].refs["result_path"])
        )["config"]["job_id"]
        failed_payload = read_json(failed_result)
        failed_payload["config"]["job_id"] = first_job_id
        atomic_write_json(failed_result, failed_payload)
        atomic_write_json(failed_result.parent / "config.json", failed_payload["config"])
        _write_harbor_aggregate(failed_result)
        failed.refs["job_id"] = first_job_id
        batch = SimpleNamespace(
            trajectories=[normal.trajectories[0], failed],
            task_ids=[normal.task_ids[0], failed.task_id],
            view_name="train",
            mode="train",
        )

        self._assert_update_fails_closed(
            baseline=baseline,
            state=state,
            batch=batch,
            client=client,
            case_root=case_root,
        )

    def test_train_projection_requires_attestation_and_result_context_before_call(self) -> None:
        for tamper in ("missing", "self_hash", "result_metadata"):
            with self.subTest(tamper=tamper):
                case_root = self.root / f"attestation-{tamper}"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=case_root / "harbor",
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                batch = train_batch(case_root / "harbor")
                result_path = Path(batch.trajectories[0].refs["result_path"])
                attestation_path = result_path.parent / "agent" / ATTESTATION_FILENAME
                if tamper == "missing":
                    attestation_path.unlink()
                elif tamper == "self_hash":
                    attestation = read_json(attestation_path)
                    attestation["attestation_sha256"] = "0" * 64
                    atomic_write_json(attestation_path, attestation)
                else:
                    result = read_json(result_path)
                    result["agent_result"]["metadata"]["raw_response"] = SECRET
                    atomic_write_json(result_path, result)

                with self.assertRaises(ValueError):
                    baseline.update(batch, state)

                self.assertEqual(client.calls, [])
                self.assertEqual(baseline.update_index, 0)
                self.assertEqual(list((case_root / "state" / "attempts").glob("*.json")), [])

    def test_train_projection_binds_batch_task_order_and_rejects_reused_result(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        mismatched = train_batch(self.root / "harbor", trial_name="mismatch")
        mismatched.task_ids[0] = "other-task"
        with self.assertRaisesRegex(ValueError, "task_ids"):
            baseline.update(mismatched, state)

        duplicated = train_batch(self.root / "harbor", trial_name="duplicate")
        original = duplicated.trajectories[0]
        duplicate = SimpleNamespace(**vars(original))
        duplicated.trajectories.append(duplicate)
        duplicated.task_ids.append(original.task_id)
        with self.assertRaisesRegex(ValueError, "task_ids.*unique|attempt_id was reused|result.*reused"):
            baseline.update(duplicated, state)

    def test_train_projection_rejects_two_tasks_from_one_harbor_job(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        first = train_batch(self.root / "harbor", trial_name="trial-a")
        second = train_batch(self.root / "harbor", trial_name="trial-b")
        first_result = Path(first.trajectories[0].refs["result_path"])
        old_second_result = Path(second.trajectories[0].refs["result_path"])
        target_trial = first_result.parent.parent / old_second_result.parent.name
        shutil.move(str(old_second_result.parent), str(target_trial))
        second_result = target_trial / "result.json"
        payload = read_json(second_result)
        other_task_id = "terminal-bench-core/other-task"
        other_task_dir = self.root / "harbor" / "_tasks" / "other-task"
        other_task_dir.mkdir(parents=True)
        other_task_path = other_task_dir.resolve().as_posix()
        first_payload = read_json(first_result)
        payload["task_name"] = "other-task"
        payload["task_id"] = {"path": other_task_path}
        payload["task_checksum"] = sha256_json({"task_id": other_task_id})
        payload["source"] = first_result.parent.parent.name
        payload["trial_uri"] = target_trial.as_uri()
        payload["config"]["task"] = {"path": other_task_path}
        payload["config"]["trials_dir"] = first_result.parent.parent.resolve().as_posix()
        payload["config"]["job_id"] = first_payload["config"]["job_id"]
        atomic_write_json(second_result, payload)
        second_trajectory = second.trajectories[0]
        second_trajectory.task_id = other_task_id
        second_trajectory.refs.update(
            {
                "result_path": str(second_result),
                "job_dir": str(first_result.parent.parent),
                "trial_uri": payload["trial_uri"],
                "job_id": payload["config"]["job_id"],
                "harbor_source": payload["source"],
                "harbor_task_name": payload["task_name"],
                "task_checksum": payload["task_checksum"],
            }
        )
        batch = SimpleNamespace(
            trajectories=[first.trajectories[0], second_trajectory],
            task_ids=[first.task_ids[0], other_task_id],
            view_name="train",
            mode="train",
        )

        with self.assertRaisesRegex(ValueError, "job directory|job.*reused|exactly one child"):
            baseline.update(batch, state)

    def test_update_projects_only_structural_train_evidence_and_persists_immutable_candidate(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        state = baseline.initialize(self.root / "run")
        a0_hash = state.metadata["a0_sha256"]

        result = baseline.update(train_batch(self.root / "harbor"), state)

        self.assertTrue(result.changed)
        self.assertEqual(result.status, "updated")
        self.assertEqual(len(client.calls), 1)
        request_text = json.dumps(client.calls[0], sort_keys=True)
        self.assertNotIn(CANARY, request_text)
        self.assertNotIn(SECRET, request_text)
        self.assertNotIn("raw prompt", request_text)
        projected = client.calls[0]["evidence"]
        self.assertEqual(projected["atif"]["tool_categories"], {"shell": 1})
        self.assertEqual(projected["atif"]["tool_statuses"], {"success": 1})
        report = baseline.report(state)
        self.assertEqual(report["a0_sha256"], a0_hash)
        self.assertNotEqual(report["evaluation_candidate_sha256"], a0_hash)
        self.assertFalse(report["causal_attribution_claimed"])
        self.assertFalse(report["promotion_claimed"])
        all_state = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.root / "state").rglob("*")
            if path.is_file()
        )
        for forbidden in (CANARY, SECRET, "raw prompt", "raw response", "hidden reasoning"):
            self.assertNotIn(forbidden, all_state)
        snapshots = list((self.root / "state" / "snapshots").glob("*.json"))
        self.assertEqual(len(snapshots), 2)
        for path in snapshots:
            snapshot = HarnessSnapshot.from_dict(read_json(path))
            self.assertEqual(path.stem, snapshot.snapshot_sha256)

    def test_two_updates_publish_each_committed_candidate_to_the_live_rollout_state(self) -> None:
        client = FakeClient(candidate_payload(max_iterations=11))
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        a0 = HarnessSnapshot.from_dict(
            read_json(self.root / "state" / "snapshots" / f"{state.metadata['a0_sha256']}.json")
        )

        first = baseline.update(
            train_batch(self.root / "harbor", snapshot=a0, trial_name="train-generation-zero"),
            state,
        )

        self.assertTrue(first.changed)
        a1_hash = first.logs["candidate_sha256"]
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], a1_hash)
        self.assertEqual(Path(state.metadata["prompt_template_path"]).stem, a1_hash)
        a1 = HarnessSnapshot.from_dict(
            read_json(self.root / "state" / "snapshots" / f"{a1_hash}.json")
        )
        rollout_agent = EvoAgentMiMo(
            self.root / "rollout-logs",
            extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN},
            prompt_template_path=state.metadata["prompt_template_path"],
            seed=43,
        )
        self.assertEqual(rollout_agent.snapshot.snapshot_sha256, a1_hash)

        client.candidate = candidate_payload(max_iterations=12)
        second = baseline.update(
            train_batch(self.root / "harbor", snapshot=a1, trial_name="train-generation-one"),
            state,
        )

        self.assertTrue(second.changed)
        a2_hash = second.logs["candidate_sha256"]
        self.assertNotEqual(a2_hash, a1_hash)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], a2_hash)
        self.assertEqual(Path(state.metadata["prompt_template_path"]).stem, a2_hash)
        self.assertEqual(baseline.update_index, 2)

    def test_second_update_rejects_a_stale_failure_receipt_without_advancing_state(self) -> None:
        client = FakeClient(candidate_payload(max_iterations=11))
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        first = baseline.update(train_batch(self.root / "harbor"), state)
        a1_hash = first.logs["candidate_sha256"]
        result_path, _receipt_path = write_failure_receipt(
            self.root / "harbor",
            snapshot=default_a0(),
            trial_name="stale-generation-zero-receipt",
        )
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        stale_batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        with self.assertRaisesRegex(ValueError, "failure receipt snapshot drifted"):
            baseline.update(stale_batch, state)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(baseline.update_index, 1)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], a1_hash)
        self.assertEqual(baseline.report(state)["evaluation_candidate_sha256"], a1_hash)

    def test_second_update_accepts_a_receipted_failure_bound_to_the_live_candidate(self) -> None:
        client = FakeClient(candidate_payload(max_iterations=11))
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        first = baseline.update(train_batch(self.root / "harbor"), state)
        a1_hash = first.logs["candidate_sha256"]
        a1 = HarnessSnapshot.from_dict(
            read_json(self.root / "state" / "snapshots" / f"{a1_hash}.json")
        )
        result_path, _receipt_path = write_failure_receipt(
            self.root / "harbor",
            snapshot=a1,
            trial_name="generation-one-receipt",
        )
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        receipt_batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        second = baseline.update(receipt_batch, state)

        self.assertEqual(second.status, "unchanged")
        self.assertFalse(second.changed)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(baseline.update_index, 2)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], a1_hash)

    def test_second_update_rejects_stale_atif_identity_without_a_receipt(self) -> None:
        client = FakeClient(candidate_payload(max_iterations=11))
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        first = baseline.update(train_batch(self.root / "harbor"), state)
        a1_hash = first.logs["candidate_sha256"]

        with self.assertRaisesRegex(ValueError, "ATIF snapshot identity drifted"):
            baseline.update(
                train_batch(
                    self.root / "harbor",
                    snapshot=default_a0(),
                    trial_name="stale-generation-zero-atif",
                ),
                state,
            )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(baseline.update_index, 1)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], a1_hash)

    def test_stale_live_state_metadata_is_rejected_fail_closed(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        state = baseline.initialize(self.root / "run")
        state.metadata["prompt_template_path"] = str(self.root / "stale.md")

        with self.assertRaisesRegex(ValueError, "current committed candidate"):
            baseline.report(state)

    def test_persistence_failure_does_not_publish_an_uncommitted_candidate(self) -> None:
        client = FakeClient(candidate_payload(max_iterations=11))
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        a0_hash = state.metadata["evaluation_candidate_sha256"]

        with patch.object(baseline, "_write_state_manifest", side_effect=OSError("simulated commit failure")):
            with self.assertRaisesRegex(OSError, "simulated commit failure"):
                baseline.update(train_batch(self.root / "harbor"), state)

        self.assertEqual(baseline.update_index, 0)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], a0_hash)
        self.assertEqual(baseline.report(state)["evaluation_candidate_sha256"], a0_hash)

    def test_atif_identity_drift_is_rejected_before_the_update_model_call(self) -> None:
        cases = (
            "snapshot",
            "components",
            "route",
            "seed",
            "model",
            "runtime",
            "mirrored_extra",
            "missing_identity_field",
        )
        for case in cases:
            with self.subTest(case=case):
                case_root = self.root / case
                harbor_root = case_root / "harbor"
                client = FakeClient(candidate_payload())
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=harbor_root,
                    model_client=client,
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                result_path = write_raw_atif(harbor_root)
                atif_path = result_path.parent / "agent" / "trajectory.json"
                payload = read_json(atif_path)
                if case == "snapshot":
                    payload["extra"]["snapshot_hash"] = "0" * 64
                    payload["agent"]["extra"]["snapshot_hash"] = "0" * 64
                elif case == "components":
                    payload["extra"]["component_hashes"]["policy"] = "0" * 64
                    payload["agent"]["extra"]["component_hashes"]["policy"] = "0" * 64
                elif case == "route":
                    payload["extra"]["route_contract_sha256"] = "0" * 64
                    payload["agent"]["extra"]["route_contract_sha256"] = "0" * 64
                elif case == "seed":
                    payload["extra"]["seed"] = 44
                    payload["agent"]["extra"]["seed"] = 44
                elif case == "model":
                    payload["extra"]["api_model_id"] = "other/model"
                    payload["agent"]["extra"]["api_model_id"] = "other/model"
                elif case == "runtime":
                    payload["extra"]["runtime_identity"]["version"] = "other"
                    payload["agent"]["extra"]["runtime_identity"]["version"] = "other"
                elif case == "mirrored_extra":
                    payload["agent"]["extra"] = {**payload["agent"]["extra"], "seed": 44}
                elif case == "missing_identity_field":
                    payload["extra"].pop("route_contract_sha256")
                    payload["agent"]["extra"].pop("route_contract_sha256")
                atomic_write_json(atif_path, payload)

                with self.assertRaisesRegex(ValueError, "ATIF .*identity"):
                    baseline.update(train_batch_from_result(result_path), state)

                self.assertEqual(client.calls, [])
                self.assertEqual(baseline.update_index, 0)

    def test_invalid_candidate_records_cost_and_hash_but_does_not_change_snapshot(self) -> None:
        payload = candidate_payload()
        payload["skills"][0]["guidance"] = f"memorize {CANARY}"
        client = FakeClient(payload)
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        state = baseline.initialize(self.root / "run")
        before = state.metadata["evaluation_candidate_sha256"]

        result = baseline.update(train_batch(self.root / "harbor"), state)

        self.assertFalse(result.changed)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.metrics["cost_usd"], 0.0025)
        self.assertEqual(baseline.report(state)["evaluation_candidate_sha256"], before)
        attempt = read_json(next((self.root / "state" / "attempts").glob("*.json")))
        self.assertEqual(attempt["status"], "rejected")
        self.assertEqual(attempt["candidate_snapshot_sha256"], before)
        self.assertEqual(attempt["request_sha256"], "1" * 64)
        self.assertNotIn(CANARY, json.dumps(attempt))

    def test_eval_or_mixed_batch_is_rejected_before_model_call(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        batch.mode = "checkpoint_eval"
        result = baseline.update(batch, state)
        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 0)

    def test_mixed_batch_counts_errored_harbor_trial_without_fabricating_atif(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        result_path, _receipt_path = write_failure_receipt(self.root / "harbor")
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch.trajectories.append(failed)
        batch.task_ids.append(failed.task_id)

        result = baseline.update(batch, state)

        self.assertTrue(result.changed)
        evidence = client.calls[0]["evidence"]
        self.assertEqual(evidence["schema_version"], "evoagent-observable-train-evidence-v2")
        self.assertEqual(evidence["num_trajectories"], 2)
        self.assertEqual(evidence["error_count"], 1)
        self.assertEqual(evidence["atif"]["documents"], 1)
        self.assertEqual(evidence["atif"]["missing_error_documents"], 1)
        self.assertEqual(evidence["atif"]["steps"], 2)
        self.assertEqual(evidence["runtime_failures"]["documents"], 1)
        self.assertEqual(
            evidence["runtime_failures"]["failure_classes"],
            {"runtime_sanitization_failed": 1},
        )
        self.assertNotIn(SECRET, json.dumps(client.calls[0], sort_keys=True))
        persisted = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.root / "state").rglob("*")
            if path.is_file()
        )
        self.assertNotIn(SECRET, persisted)

    def test_errored_trial_with_real_atif_validates_matching_failure_receipt(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        batch = train_batch(self.root / "harbor")
        write_failure_receipt(
            self.root / "harbor",
            trial_name="trial-a",
            atif_present=True,
        )
        batch.trajectories[0] = failed_train_trajectory(refs=batch.trajectories[0].refs)
        batch.task_ids[0] = batch.trajectories[0].task_id
        state = baseline.initialize(self.root / "run")

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "updated")
        evidence = client.calls[0]["evidence"]
        self.assertEqual(evidence["atif"]["documents"], 1)
        self.assertEqual(evidence["runtime_failures"]["documents"], 1)

    def test_errored_trial_receipt_atif_bit_must_match_existing_atif(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        batch = train_batch(self.root / "harbor")
        write_failure_receipt(self.root / "harbor", trial_name="trial-a", atif_present=False)
        batch.trajectories[0] = failed_train_trajectory(refs=batch.trajectories[0].refs)
        batch.task_ids[0] = batch.trajectories[0].task_id
        state = baseline.initialize(self.root / "run")

        with self.assertRaisesRegex(ValueError, "ATIF state"):
            baseline.update(batch, state)

        self.assertEqual(client.calls, [])

    def test_all_missing_error_atif_batch_stops_before_model_call(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        (self.root / "harbor").mkdir()
        state = baseline.initialize(self.root / "run")
        failed = failed_train_trajectory()
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 0)

    def test_all_receipted_error_batch_persists_no_call_skip_and_advances_update(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        (self.root / "harbor").mkdir()
        state = baseline.initialize(self.root / "run")
        before = state.metadata["evaluation_candidate_sha256"]
        result_path, _receipt_path = write_failure_receipt(self.root / "harbor")
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "unchanged")
        self.assertFalse(result.changed)
        self.assertFalse(result.logs["model_call_executed"])
        self.assertEqual(result.logs["skip_code"], NO_USABLE_ATIF_SKIP_CODE)
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 1)
        report = baseline.report(state)
        self.assertEqual(report["evaluation_candidate_sha256"], before)
        self.assertEqual(report["update_model_calls"], 0)
        self.assertEqual(report["skipped_updates"], 1)
        attempt = read_json(next((self.root / "state" / "attempts").glob("*.json")))
        self.assertEqual(attempt["status"], "skipped_no_usable_atif")
        self.assertFalse(attempt["model_call_executed"])
        self.assertEqual(attempt["skip_code"], NO_USABLE_ATIF_SKIP_CODE)
        self.assertIsNone(attempt["response_sha256"])
        self.assertEqual(attempt["usage"]["total_tokens"], 0)

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        restored_state = restored.initialize(self.root / "run")
        self.assertEqual(restored.report(restored_state)["skipped_updates"], 1)

        attempt["model_call_executed"] = True
        atomic_write_json(next((self.root / "state" / "attempts").glob("*.json")), attempt)
        tampered = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        with self.assertRaises(ValueError):
            tampered.initialize(self.root / "run")

    def test_mixed_batch_with_unattested_harbor_failure_skips_without_partial_learning(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        before = state.metadata["evaluation_candidate_sha256"]
        batch = train_batch(self.root / "harbor")
        result_path = write_unattested_harbor_result(self.root / "harbor")
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch.trajectories.append(failed)
        batch.task_ids.append(failed.task_id)

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "unchanged")
        self.assertFalse(result.changed)
        self.assertEqual(result.logs["skip_code"], INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE)
        self.assertEqual(result.metrics["unattested_harbor_failures"], 1)
        self.assertEqual(result.metrics["input_tokens"], 0)
        self.assertEqual(result.metrics["output_tokens"], 0)
        self.assertEqual(result.metrics["cost_usd"], 0.0)
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 1)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], before)
        attempt = read_json(next((self.root / "state" / "attempts").glob("*.json")))
        self.assertEqual(attempt["status"], "skipped_incomplete_evidence")
        self.assertEqual(attempt["skip_code"], INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE)
        self.assertFalse(attempt["model_call_executed"])
        self.assertEqual(attempt["usage"]["total_tokens"], 0)
        self.assertNotIn(SECRET, json.dumps(attempt, sort_keys=True))

    def test_canonical_unattested_failure_with_local_leaf_skips_without_model_call(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        task_id = "terminal-bench/vulnerable-secret"
        result_path = write_unattested_harbor_result(
            self.root / "harbor",
            task_id=task_id,
        )
        payload = read_json(result_path)
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[task_id],
            view_name="train",
            mode="train",
            batch_index=0,
            epoch=0,
        )

        self.assertEqual(payload["task_name"], task_id)
        self.assertEqual(Path(payload["task_id"]["path"]).name, "vulnerable-secret")
        result = baseline.update(batch, state)

        self.assertEqual(result.status, "unchanged")
        self.assertEqual(result.logs["skip_code"], INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE)
        self.assertEqual(client.calls, [])

    def test_incomplete_projection_hash_binds_verified_evidence_and_batch_identity(self) -> None:
        batch = train_batch(self.root / "harbor")
        result_path = write_unattested_harbor_result(self.root / "harbor")
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch.trajectories.append(failed)
        batch.task_ids.append(failed.task_id)

        def projection() -> object:
            with self.assertRaises(IncompleteHarborTrainEvidence) as raised:
                project_train_batch(
                    batch,
                    atif_root=self.root / "harbor",
                    expected_snapshot_sha256=default_a0().snapshot_sha256,
                    expected_component_sha256=dict(default_a0().component_sha256),
                    expected_route_contract_sha256=sha256_json(expected_route_contract()),
                    expected_seed=43,
                )
            return raised.exception.projection

        before = projection()
        self.assertEqual(before.summary["verified_atif_documents"], 1)
        self.assertRegex(before.summary["verified_atif_set_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(before.summary["verified_failure_receipt_set_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(before.summary["batch_task_job_identity_sha256"], r"^[0-9a-f]{64}$")

        atif_path = Path(batch.trajectories[0].refs["result_path"]).parent / "agent" / "trajectory.json"
        atif = read_json(atif_path)
        atif["steps"][1]["timestamp"] = "2026-09-01T00:00:00Z"
        atomic_write_json(atif_path, atif)
        attestation_path = atif_path.parent / ATTESTATION_FILENAME
        attestation = read_json(attestation_path)
        attestation["atif_sha256"] = sha256_file(atif_path)
        attestation_unsigned = dict(attestation)
        attestation_unsigned.pop("attestation_sha256")
        attestation["attestation_sha256"] = sha256_json(attestation_unsigned)
        atomic_write_json(attestation_path, attestation)
        result_path = Path(batch.trajectories[0].refs["result_path"])
        result = read_json(result_path)
        result["agent_result"]["metadata"]["atif_sha256"] = attestation["atif_sha256"]
        result["agent_result"]["metadata"]["attestation_sha256"] = attestation[
            "attestation_sha256"
        ]
        atomic_write_json(result_path, result)
        after = projection()

        self.assertNotEqual(after.summary["verified_atif_set_sha256"], before.summary["verified_atif_set_sha256"])
        self.assertNotEqual(after.evidence_sha256, before.evidence_sha256)

    def test_unattested_second_update_preserves_live_disk_and_restored_a1(self) -> None:
        client = FakeClient(candidate_payload(max_iterations=11))
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        first = baseline.update(train_batch(self.root / "harbor"), state)
        a1_hash = first.logs["candidate_sha256"]
        result_path = write_unattested_harbor_result(
            self.root / "harbor",
            trial_name="second-batch-outer-failure",
        )
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        second = baseline.update(batch, state)

        self.assertEqual(second.logs["skip_code"], INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(baseline.update_index, 2)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], a1_hash)
        self.assertEqual(baseline.report(state)["evaluation_candidate_sha256"], a1_hash)
        self.assertEqual(baseline.report(state)["incomplete_evidence_skips"], 1)

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        restored_state = restored.initialize(self.root / "run")
        self.assertEqual(restored.update_index, 2)
        self.assertEqual(restored_state.metadata["evaluation_candidate_sha256"], a1_hash)
        self.assertEqual(restored.report(restored_state)["incomplete_evidence_skips"], 1)

    def test_declared_missing_receipt_is_not_downgraded_to_unattested_failure(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        result_path = write_unattested_harbor_result(self.root / "harbor")
        missing_receipt = result_path.parent / "agent" / FAILURE_RECEIPT_FILENAME
        failed = failed_train_trajectory(
            refs={
                "result_path": str(result_path),
                "failure_receipt_path": str(missing_receipt),
            }
        )
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        with self.assertRaisesRegex(ValueError, "failure receipt is missing"):
            baseline.update(batch, state)

        self.assertEqual(baseline.update_index, 0)
        self.assertEqual(list((self.root / "state" / "attempts").glob("*.json")), [])

        failed.refs = dict(failed.refs)
        failed.refs["failure_receipt_path"] = None
        with self.assertRaisesRegex(ValueError, "must be non-empty text"):
            baseline.update(batch, state)

    def test_unattested_skip_manifest_failure_does_not_publish_and_can_retry(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        before = state.metadata["evaluation_candidate_sha256"]
        result_path = write_unattested_harbor_result(self.root / "harbor")
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        with patch.object(baseline, "_write_state_manifest", side_effect=OSError("commit failed")):
            with self.assertRaisesRegex(OSError, "commit failed"):
                baseline.update(batch, state)

        self.assertEqual(baseline.update_index, 0)
        self.assertEqual(state.metadata["evaluation_candidate_sha256"], before)
        self.assertEqual(baseline.report(state)["evaluation_candidate_sha256"], before)

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        restored_state = restored.initialize(self.root / "run")
        self.assertEqual(restored.update_index, 0)
        retried = restored.update(batch, restored_state)
        self.assertEqual(retried.logs["skip_code"], INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE)
        self.assertEqual(restored.update_index, 1)
        self.assertEqual(restored_state.metadata["evaluation_candidate_sha256"], before)

    def test_partial_agent_evidence_is_not_downgraded_to_unattested_failure(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        result_path = write_unattested_harbor_result(self.root / "harbor")
        atomic_write_json(result_path.parent / "agent" / ATTESTATION_FILENAME, {"partial": True})
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        with self.assertRaisesRegex(ValueError, "partial agent evidence"):
            baseline.update(batch, state)

        self.assertEqual(baseline.update_index, 0)

    def test_any_unclassified_errored_atif_skips_the_whole_batch(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        result_path = Path(batch.trajectories[0].refs["result_path"])
        payload = read_json(result_path)
        payload["exception_info"] = {
            "exception_type": "OuterHarborFailure",
            "exception_message": "bounded_error",
            "exception_traceback": "",
            "occurred_at": "2026-09-01T00:00:05Z",
        }
        payload["verifier_result"] = {"rewards": {"reward": 0.0}}
        atomic_write_json(result_path, payload)
        _write_harbor_aggregate(result_path)
        batch.trajectories[0] = failed_train_trajectory(refs=batch.trajectories[0].refs)
        batch.task_ids[0] = batch.trajectories[0].task_id

        result = baseline.update(batch, state)

        self.assertEqual(result.logs["skip_code"], INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE)
        self.assertFalse(result.logs["model_call_executed"])
        self.assertEqual(result.metrics["unattested_harbor_failures"], 1)
        self.assertEqual(client.calls, [])

    def test_declared_atif_is_mandatory_even_when_the_derived_atif_exists(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        result_path = Path(batch.trajectories[0].refs["result_path"])
        batch.trajectories[0].refs["atif_path"] = str(
            result_path.parent / "agent" / "missing-trajectory.json"
        )

        with self.assertRaisesRegex(ValueError, "declared Harbor ATIF evidence is missing"):
            baseline.update(batch, state)

    def test_declared_atif_cannot_reference_another_harbor_trial(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor", trial_name="trial-one")
        other_result = write_raw_atif(self.root / "harbor", trial_name="trial-two")
        batch.trajectories[0].refs["atif_path"] = str(
            other_result.parent / "agent" / "trajectory.json"
        )

        with self.assertRaisesRegex(ValueError, "does not match its Harbor trial"):
            baseline.update(batch, state)

    def test_harbor_trial_cannot_publish_two_derived_atif_files(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        result_path = Path(batch.trajectories[0].refs["result_path"])
        trajectory_path = result_path.parent / "agent" / "trajectory.json"
        (trajectory_path.parent / "atif.json").write_bytes(trajectory_path.read_bytes())

        with self.assertRaisesRegex(ValueError, "ambiguous ATIF"):
            baseline.update(batch, state)

    def test_unattested_errored_result_rejects_any_unbound_agent_file(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        result_path = write_unattested_harbor_result(self.root / "harbor")
        extra = result_path.parent / "agent" / "partial.tmp"
        extra.parent.mkdir(exist_ok=True)
        extra.write_text("partial", encoding="utf-8")
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        with self.assertRaisesRegex(ValueError, "partial agent evidence"):
            baseline.update(batch, state)

    def test_unattested_errored_result_requires_strict_exception_identity(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        result_path = write_unattested_harbor_result(self.root / "harbor")
        payload = read_json(result_path)
        payload["exception_info"] = {"exception_type": "HarborErroredResult"}
        atomic_write_json(result_path, payload)
        failed = failed_train_trajectory(refs={"result_path": str(result_path)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )

        with self.assertRaisesRegex(ValueError, "invalid ExceptionInfo|ExceptionInfo schema"):
            baseline.update(batch, state)

    def test_unattested_failure_does_not_hide_later_tampered_receipt(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        outer_result = write_unattested_harbor_result(
            self.root / "harbor",
            task_id=CANARY,
        )
        tampered_result, _receipt = write_failure_receipt(
            self.root / "harbor",
            trial_name="later-tampered-receipt",
            tamper="hash",
        )
        outer = failed_train_trajectory(refs={"result_path": str(outer_result)})
        outer.task_id = CANARY
        tampered = failed_train_trajectory(refs={"result_path": str(tampered_result)})
        batch = SimpleNamespace(
            trajectories=[outer, tampered],
            task_ids=[outer.task_id, tampered.task_id],
            view_name="train",
            mode="train",
        )

        with self.assertRaisesRegex(ValueError, "receipt hash is invalid"):
            baseline.update(batch, state)

        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 0)

    def test_receipted_error_batch_rejects_tamper_identity_and_false_atif_claims(self) -> None:
        for tamper in ("hash", "snapshot", "atif_present", "classification"):
            with self.subTest(tamper=tamper):
                case_root = self.root / tamper
                harbor_root = case_root / "harbor"
                harbor_root.mkdir(parents=True)
                baseline = EvoAgentSEAGymBaseline(
                    baseline_id="evo",
                    state_dir=case_root / "state",
                    atif_root=harbor_root,
                    model_client=FakeClient(candidate_payload()),
                    fail_on_update_error=True,
                )
                state = baseline.initialize(case_root / "run")
                result_path, _receipt_path = write_failure_receipt(harbor_root, tamper=tamper)
                failed = failed_train_trajectory(refs={"result_path": str(result_path)})
                batch = SimpleNamespace(
                    trajectories=[failed],
                    task_ids=[failed.task_id],
                    view_name="train",
                    mode="train",
                )

                with self.assertRaises(ValueError):
                    baseline.update(batch, state)

                self.assertEqual(baseline.update_index, 0)
                self.assertEqual(list((case_root / "state" / "attempts").glob("*.json")), [])

    def test_failure_receipt_path_escape_and_link_fail_closed(self) -> None:
        harbor_root = self.root / "harbor"
        harbor_root.mkdir()
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=harbor_root,
            model_client=FakeClient(candidate_payload()),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")
        _result_path, receipt_path = write_failure_receipt(harbor_root)
        outside = self.root / "outside" / FAILURE_RECEIPT_FILENAME
        outside.parent.mkdir()
        outside.write_bytes(receipt_path.read_bytes())
        failed = failed_train_trajectory(refs={"failure_receipt_path": str(outside)})
        batch = SimpleNamespace(
            trajectories=[failed],
            task_ids=[failed.task_id],
            view_name="train",
            mode="train",
        )
        with self.assertRaises(ValueError):
            baseline.update(batch, state)

        link = harbor_root / "linked" / FAILURE_RECEIPT_FILENAME
        link.parent.mkdir()
        try:
            link.symlink_to(receipt_path)
        except OSError:
            return
        linked = failed_train_trajectory(refs={"failure_receipt_path": str(link)})
        linked_batch = SimpleNamespace(
            trajectories=[linked],
            task_ids=[linked.task_id],
            view_name="train",
            mode="train",
        )
        with self.assertRaises(ValueError):
            baseline.update(linked_batch, state)

    def test_completed_unsuccessful_trial_still_requires_atif(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        (self.root / "harbor").mkdir()
        state = baseline.initialize(self.root / "run")
        trajectory = failed_train_trajectory()
        trajectory.error = None
        batch = SimpleNamespace(
            trajectories=[trajectory],
            task_ids=[trajectory.task_id],
            view_name="train",
            mode="train",
        )

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 0)

    def test_errored_trial_cannot_hide_an_atif_path_escape(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        (self.root / "harbor").mkdir()
        outside = self.root / "outside" / "trajectory.json"
        outside.parent.mkdir()
        outside.write_text("{}", encoding="utf-8")
        state = baseline.initialize(self.root / "run")
        trajectory = failed_train_trajectory(refs={"atif_path": str(outside)})
        batch = SimpleNamespace(
            trajectories=[trajectory],
            task_ids=[trajectory.task_id],
            view_name="train",
            mode="train",
        )

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 0)

    def test_errored_trial_cannot_hide_a_nonexistent_external_atif_path(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        (self.root / "harbor").mkdir()
        state = baseline.initialize(self.root / "run")
        trajectory = failed_train_trajectory(
            refs={"atif_path": str(self.root / "outside" / "missing-trajectory.json")}
        )
        batch = SimpleNamespace(
            trajectories=[trajectory],
            task_ids=[trajectory.task_id],
            view_name="train",
            mode="train",
        )

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])

    def test_success_with_error_is_rejected_before_atif_or_model_processing(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        (self.root / "harbor").mkdir()
        state = baseline.initialize(self.root / "run")
        trajectory = failed_train_trajectory()
        trajectory.success = True
        batch = SimpleNamespace(
            trajectories=[trajectory],
            task_ids=[trajectory.task_id],
            view_name="train",
            mode="train",
        )

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])

    def test_errored_trial_with_existing_malformed_atif_is_not_downgraded_to_missing(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        atif = self.root / "harbor" / "trial" / "agent" / "trajectory.json"
        atif.parent.mkdir(parents=True)
        atif.write_text("{}", encoding="utf-8")
        state = baseline.initialize(self.root / "run")
        trajectory = failed_train_trajectory(refs={"atif_path": str(atif)})
        batch = SimpleNamespace(
            trajectories=[trajectory],
            task_ids=[trajectory.task_id],
            view_name="train",
            mode="train",
        )

        result = baseline.update(batch, state)

        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])

    def test_fail_on_update_error_stops_before_later_paid_rollouts(self) -> None:
        class FailingClient:
            def complete(self, **kwargs: object) -> StructuredCompletion:
                del kwargs
                raise RuntimeError("simulated update route failure")

        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FailingClient(),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")

        with self.assertRaisesRegex(RuntimeError, "simulated update route failure"):
            baseline.update(train_batch(self.root / "harbor"), state)

        self.assertEqual(baseline.update_index, 0)
        self.assertEqual(list((self.root / "state" / "attempts").glob("*.json")), [])

    def test_checkpoint_round_trip_reproduces_a0_and_candidate_and_rejects_tamper(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        state = baseline.initialize(self.root / "run")
        baseline.update(train_batch(self.root / "harbor"), state)
        expected = baseline.report(state)
        checkpoint = baseline.save_checkpoint(state, self.root / "checkpoint")

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "restored",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        restored_state = restored.load_checkpoint(checkpoint)
        actual = restored.report(restored_state)
        self.assertEqual(actual["a0_sha256"], expected["a0_sha256"])
        self.assertEqual(actual["evaluation_candidate_sha256"], expected["evaluation_candidate_sha256"])
        self.assertEqual(actual["update_cost_usd"], expected["update_cost_usd"])

        snapshot = next((self.root / "checkpoint" / "baseline_state" / "snapshots").glob("*.json"))
        snapshot.write_text("{}", encoding="utf-8")
        other = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "other",
            atif_root=self.root / "harbor",
        )
        with self.assertRaisesRegex(ValueError, "inventory"):
            other.load_checkpoint(checkpoint)

    def test_pinned_outer_checkpoint_direct_and_final_alias_round_trip_after_relocation(
        self,
    ) -> None:
        checkpoints = self.root / "origin" / "checkpoints"
        source = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "source",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        state = source.initialize(self.root / "run")
        initial = source.save_checkpoint(state, checkpoints / "initial")
        initial_baseline = read_json(checkpoints / "initial" / "checkpoint.json")
        initial_baseline["checkpoint_dir"] = str((checkpoints / "initial").resolve())
        write_outer_checkpoint(
            checkpoints / "initial",
            checkpoint_id="initial",
            checkpoint_type="initial",
            baseline_manifest=initial_baseline,
        )

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "restored-initial",
            atif_root=self.root / "harbor",
        )
        restored_state = restored.load_checkpoint(initial)
        self.assertEqual(restored.report(restored_state)["update_index"], 0)

        source.update(train_batch(self.root / "harbor"), state)
        expected = source.report(state)
        source.save_checkpoint(state, checkpoints / "epoch_0001")
        epoch_baseline = read_json(checkpoints / "epoch_0001" / "checkpoint.json")
        epoch_baseline["checkpoint_dir"] = str((checkpoints / "epoch_0001").resolve())
        write_outer_checkpoint(
            checkpoints / "epoch_0001",
            checkpoint_id="epoch_0001",
            checkpoint_type="epoch",
            baseline_manifest=epoch_baseline,
        )
        final_dir = checkpoints / "final"
        final_baseline = {
            **copy.deepcopy(epoch_baseline),
            "type": "baseline_checkpoint_alias",
            "alias_of": "epoch_0001",
            "source_checkpoint_id": "epoch_0001",
            "checkpoint_dir": str(final_dir.resolve()),
            "state_ref": "../epoch_0001/baseline_state",
        }
        write_outer_checkpoint(
            final_dir,
            checkpoint_id="final",
            checkpoint_type="final",
            baseline_manifest=final_baseline,
        )

        relocated_parent = self.root / "relocated"
        relocated_parent.mkdir()
        relocated_checkpoints = relocated_parent / "checkpoints"
        shutil.move(str(checkpoints), str(relocated_checkpoints))
        relocated_initial = SimpleNamespace(
            checkpoint_dir=relocated_checkpoints / "initial"
        )
        relocated_initial_restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "relocated-initial",
            atif_root=self.root / "harbor",
        )
        relocated_initial_state = relocated_initial_restored.load_checkpoint(
            relocated_initial
        )
        self.assertEqual(
            relocated_initial_restored.report(relocated_initial_state)["update_index"],
            0,
        )
        relocated_final = relocated_checkpoints / "final"
        final_checkpoint = SimpleNamespace(checkpoint_dir=relocated_final)
        final_restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "restored-final",
            atif_root=self.root / "harbor",
        )
        final_state = final_restored.load_checkpoint(final_checkpoint)
        actual = final_restored.report(final_state)
        self.assertEqual(actual["update_index"], 1)
        self.assertEqual(
            actual["evaluation_candidate_sha256"],
            expected["evaluation_candidate_sha256"],
        )

        final_path = relocated_final / "checkpoint.json"
        source_path = relocated_checkpoints / "epoch_0001" / "checkpoint.json"
        clean_final = read_json(final_path)
        clean_source = read_json(source_path)
        tampered = copy.deepcopy(clean_final)
        tampered["baseline"]["checkpoint_dir"] = "/old/run/checkpoints/not-final"
        atomic_write_json(final_path, tampered)
        with self.assertRaisesRegex(ValueError, "directory identity"):
            final_restored.load_checkpoint(final_checkpoint)
        atomic_write_json(final_path, clean_final)

        tampered = copy.deepcopy(clean_final)
        a0 = tampered["baseline"]["state_metadata"]["a0_sha256"]
        tampered["baseline"]["state_metadata"]["evaluation_candidate_sha256"] = a0
        tampered["baseline"]["state_metadata"]["prompt_template"] = (
            f"baseline_state/prompts/{a0}.md"
        )
        atomic_write_json(final_path, tampered)
        with self.assertRaisesRegex(ValueError, "differs from its source"):
            final_restored.load_checkpoint(final_checkpoint)
        atomic_write_json(final_path, clean_final)

        tampered_source = copy.deepcopy(clean_source)
        tampered_source["run_id"] = "different-run"
        atomic_write_json(source_path, tampered_source)
        with self.assertRaisesRegex(ValueError, "source outer identity"):
            final_restored.load_checkpoint(final_checkpoint)
        atomic_write_json(source_path, clean_source)

        tampered = copy.deepcopy(clean_final)
        tampered["baseline"]["state_ref"] = "../../outside/baseline_state"
        tampered["refs"]["baseline_state"] = "../../outside/baseline_state"
        atomic_write_json(final_path, tampered)
        with self.assertRaisesRegex(ValueError, "alias identity"):
            final_restored.load_checkpoint(final_checkpoint)

    def test_semantically_invalid_rehashed_checkpoint_cannot_replace_stable_state(self) -> None:
        source = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "source",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        source_state = source.initialize(self.root / "run")
        source.update(train_batch(self.root / "harbor"), source_state)
        checkpoint = source.save_checkpoint(source_state, self.root / "checkpoint")

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "restored",
            atif_root=self.root / "harbor",
        )
        restored_state = restored.load_checkpoint(checkpoint)
        stable_report = restored.report(restored_state)
        stable_manifest_hash = sha256_file(restored.state_manifest_path)

        checkpoint_state = self.root / "checkpoint" / "baseline_state"
        state_manifest = read_json(checkpoint_state / "state.json")
        state_manifest["evaluation_candidate_sha256"] = state_manifest["a0_sha256"]
        state_manifest["prompt_template"] = f"prompts/{state_manifest['a0_sha256']}.md"
        atomic_write_json(checkpoint_state / "state.json", state_manifest)
        checkpoint_manifest = read_json(self.root / "checkpoint" / "checkpoint.json")
        checkpoint_manifest["state_inventory"]["state.json"] = sha256_file(checkpoint_state / "state.json")
        checkpoint_manifest["state_inventory_sha256"] = sha256_json(checkpoint_manifest["state_inventory"])
        checkpoint_manifest["state_metadata"]["evaluation_candidate_sha256"] = state_manifest["a0_sha256"]
        checkpoint_manifest["state_metadata"]["prompt_template"] = (
            f"baseline_state/prompts/{state_manifest['a0_sha256']}.md"
        )
        atomic_write_json(self.root / "checkpoint" / "checkpoint.json", checkpoint_manifest)

        with self.assertRaisesRegex(ValueError, "candidate"):
            restored.load_checkpoint(checkpoint)
        self.assertEqual(sha256_file(restored.state_manifest_path), stable_manifest_hash)
        self.assertEqual(restored.report(restored_state), stable_report)

    def test_from_config_freezes_route_governance_and_derives_contained_atif_root(self) -> None:
        run_dir = self.root / "run"
        run_dir.mkdir()
        base = EvoAgentSEAGymBaseline.from_config(
            name="evo",
            config={
                "route_contract": expected_route_contract(),
                "automatic_promotion": False,
                "causal_attribution_claimed": False,
                "fail_on_update_error": True,
            },
            models={
                "update_model": {
                    "provider": "openrouter",
                    "model": UPDATE_MODEL_ID,
                    "api_base": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                }
            },
            state_dir=self.root / "state",
            run_dir=run_dir,
            base_dir=self.root,
        )
        self.assertEqual(base.atif_root, run_dir / "harbor" / "jobs")
        self.assertTrue(base.fail_on_update_error)
        bad_contract = expected_route_contract()
        bad_contract["provider"]["allow_fallbacks"] = True
        with self.assertRaisesRegex(ValueError, "route_contract"):
            EvoAgentSEAGymBaseline.from_config(
                name="evo",
                config={
                    "route_contract": bad_contract,
                    "automatic_promotion": False,
                    "causal_attribution_claimed": False,
                },
                models={
                    "update_model": {
                        "provider": "openrouter",
                        "model": UPDATE_MODEL_ID,
                        "api_base": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                    }
                },
                state_dir=self.root / "bad",
                run_dir=run_dir,
                base_dir=self.root,
            )


class OpenRouterTests(unittest.TestCase):
    def test_probe_failure_codes_are_bounded_and_secret_free(self) -> None:
        self.assertEqual(
            safe_probe_failure_code(RuntimeError("OpenRouter returned HTTP 429")),
            "openrouter_http_429",
        )
        self.assertEqual(
            safe_probe_failure_code(RuntimeError("OpenRouter request failed (TimeoutError)")),
            "openrouter_transport_failure",
        )
        self.assertEqual(
            safe_probe_failure_code(RuntimeError("provider body " + SECRET)),
            "openrouter_runtime_failure",
        )
        self.assertEqual(
            safe_probe_failure_code(ValueError("response body " + SECRET)),
            "openrouter_response_validation_failed",
        )

    def test_request_sends_exact_route_and_accepts_only_empty_reasoning(self) -> None:
        captured: dict[str, object] = {}

        def transport(endpoint: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
            captured.update(endpoint=endpoint, headers=headers, body=body, timeout=timeout)
            return json.dumps(
                {
                    "model": CANONICAL_MODEL_ID,
                    "provider": "Xiaomi",
                    "openrouter_metadata": {
                        "requested": "xiaomi/mimo-v2.5",
                        "strategy": "alias",
                        "attempt": 1,
                        "endpoints": {
                            "available": [
                                {
                                    "provider": "Xiaomi",
                                    "model": CANONICAL_MODEL_ID,
                                    "selected": True,
                                }
                            ]
                        },
                        "attempts": [
                            {"provider": "Xiaomi", "model": CANONICAL_MODEL_ID, "status": 200}
                        ],
                        "pipeline": [],
                    },
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning": None,
                                "reasoning_content": "",
                                "reasoning_details": [],
                                "tool_calls": [
                                    {
                                        "id": "call_verified",
                                        "type": "function",
                                        "function": {
                                            "name": "evoagent_harness_components",
                                            "arguments": json.dumps(candidate_payload()),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, "cost": 0.001},
                }
            ).encode()

        previous = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = SECRET
        try:
            completion = OpenRouterStructuredClient(transport=transport).complete(
                evidence={"safe": 1},
                current_components=default_a0().components.to_dict(),
                seed=43,
            )
        finally:
            if previous is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = previous
        body = json.loads(captured["body"])
        self.assertEqual(body["provider"], expected_route_contract()["provider"])
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tools"][0]["function"]["name"], "evoagent_harness_components")
        self.assertNotIn("seed", body)
        self.assertNotIn("response_format", body)
        self.assertEqual(captured["headers"]["X-OpenRouter-Cache"], "false")
        self.assertEqual(captured["headers"]["X-OpenRouter-Metadata"], "enabled")
        self.assertNotIn(SECRET, captured["body"].decode())
        self.assertEqual(completion.served_model_id, CANONICAL_MODEL_ID)
        self.assertEqual(completion.provider, "Xiaomi")

    def test_response_model_provider_and_nonempty_reasoning_drift_fail_closed(self) -> None:
        base = {
            "model": CANONICAL_MODEL_ID,
            "provider": "Xiaomi",
            "openrouter_metadata": {
                "requested": "xiaomi/mimo-v2.5",
                "strategy": "alias",
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {"provider": "Xiaomi", "model": CANONICAL_MODEL_ID, "selected": True}
                    ]
                },
                "attempts": [
                    {"provider": "Xiaomi", "model": CANONICAL_MODEL_ID, "status": 200}
                ],
                "pipeline": [],
            },
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_verified",
                                "type": "function",
                                "function": {
                                    "name": "evoagent_harness_components",
                                    "arguments": json.dumps(candidate_payload()),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0},
        }
        previous = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "safe-test-key"
        try:
            for mutation in (
                {"model": "other/model"},
                {"provider": "Other"},
                {"reasoning": "nonempty"},
                {"reasoning_content": ["nonempty"]},
            ):
                with self.subTest(mutation=mutation):
                    response = {**base, **mutation}
                    client = OpenRouterStructuredClient(
                        transport=lambda *_args, response=response: json.dumps(response).encode()
                    )
                    with self.assertRaises(ValueError):
                        client.complete(evidence={"safe": 1}, current_components=default_a0().components.to_dict(), seed=1)

            wrong_tool = json.loads(json.dumps(base))
            wrong_tool["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "wrong_tool"
            client = OpenRouterStructuredClient(
                transport=lambda *_args: json.dumps(wrong_tool).encode()
            )
            with self.assertRaisesRegex(ValueError, "wrong candidate Tool"):
                client.complete(evidence={"safe": 1}, current_components=default_a0().components.to_dict(), seed=1)

            for metadata in (
                None,
                {**base["openrouter_metadata"], "attempt": 2},
                {**base["openrouter_metadata"], "pipeline": [{"type": "plugin", "name": "web-search"}]},
                {
                    **base["openrouter_metadata"],
                    "endpoints": {
                        "available": [
                            {"provider": "Other", "model": CANONICAL_MODEL_ID, "selected": True}
                        ]
                    },
                },
            ):
                with self.subTest(router_metadata=metadata):
                    response = {**base, "openrouter_metadata": metadata}
                    client = OpenRouterStructuredClient(
                        transport=lambda *_args, response=response: json.dumps(response).encode()
                    )
                    with self.assertRaisesRegex(ValueError, "router metadata|materially altered|selected endpoint"):
                        client.complete(
                            evidence={"safe": 1},
                            current_components=default_a0().components.to_dict(),
                            seed=1,
                        )
        finally:
            if previous is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = previous


class FakeExecResult:
    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code
        self.stdout = ""
        self.stderr = ""


class FakeEnvironment:
    def __init__(self, return_codes: list[int] | None = None) -> None:
        self.commands: list[dict[str, object]] = []
        self.uploads: dict[str, bytes] = {}
        self.return_codes = list(return_codes or [])

    async def exec(self, **kwargs: object) -> FakeExecResult:
        self.commands.append(kwargs)
        return FakeExecResult(self.return_codes.pop(0) if self.return_codes else 0)

    async def upload_file(self, source: Path, target: str) -> None:
        self.uploads[target] = Path(source).read_bytes()


class HarborAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
        )
        self.state = baseline.initialize(self.root / "run")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _agent(self, **kwargs: object) -> EvoAgentMiMo:
        parameters: dict[str, object] = {
            "logs_dir": self.root / "logs",
            "model_name": HARBOR_MODEL_ID,
            "prompt_template_path": self.state.metadata["prompt_template_path"],
            "seed": 43,
            "route_contract": expected_route_contract(),
            "mimocode_asset_sha256": MIMOCODE_ARCHIVE_SHA256,
            "mimocode_asset_url": MIMOCODE_ARCHIVE_URL,
            "mimocode_version": MIMOCODE_VERSION,
        }
        parameters.update(kwargs)
        return EvoAgentMiMo(**parameters)

    def test_setup_and_run_use_pinned_asset_secret_free_command_and_exact_config(self) -> None:
        agent = self._agent(extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN})
        environment = FakeEnvironment()
        context = SimpleNamespace(metadata=None)
        archive = self.root / "mimocode-linux-x64.tar.gz"
        archive.write_bytes(b"locked-test-archive")
        previous_archive = os.environ.get(MIMOCODE_ARCHIVE_ENV)
        os.environ[MIMOCODE_ARCHIVE_ENV] = str(archive)
        try:
            with patch(
                "seagym_evoagent.harbor_agent.sha256_file",
                return_value=MIMOCODE_ARCHIVE_SHA256,
            ):
                asyncio.run(agent.setup(environment))
        finally:
            if previous_archive is None:
                os.environ.pop(MIMOCODE_ARCHIVE_ENV, None)
            else:
                os.environ[MIMOCODE_ARCHIVE_ENV] = previous_archive
        asyncio.run(agent.run(f"do private work {CANARY} {SECRET}", environment, context))

        setup_command = environment.commands[1]["command"]
        self.assertIn(MIMOCODE_ARCHIVE_SHA256, setup_command)
        self.assertNotIn(MIMOCODE_ARCHIVE_URL, setup_command)
        self.assertIn("command -v timeout >/dev/null", setup_command)
        self.assertEqual(
            environment.uploads["/tmp/evoagent-mimocode-install/archive.tar.gz"],
            b"locked-test-archive",
        )
        run_call = environment.commands[-1]
        command = run_call["command"]
        self.assertNotIn(CANARY, command)
        self.assertNotIn(SECRET, command)
        self.assertNotIn("--thinking", command)
        self.assertIn(
            "timeout --signal=TERM "
            f"--kill-after={MIMOCODE_FORCE_KILL_GRACE_SECONDS}s "
            f"{agent.timeout_seconds - MIMOCODE_SANITIZATION_MARGIN_SECONDS}s ",
            command,
        )
        self.assertIn(f"--model {HARBOR_MODEL_ID}", command)
        self.assertIn(f"--title {MIMOCODE_SESSION_TITLE}", command)
        self.assertIn("--mimocode-exit-code \"$mimo_status\"", command)
        self.assertIn(f"--failure-receipt /logs/agent/{FAILURE_RECEIPT_FILENAME}", command)
        self.assertIn(f"exit {MIMOCODE_PROCESS_EXIT}", command)
        self.assertIn(f"exit {SANITIZER_REJECT_EXIT}", command)
        self.assertIn(f"exit {MIMOCODE_AND_SANITIZER_EXIT}", command)
        self.assertNotIn("mimo_status=$?; set -e", command)
        config = json.loads(environment.uploads["/tmp/evoagent-mimo-runtime/mimocode.json"])
        self.assertEqual(
            config,
            locked_mimocode_config(
                expected_route_contract(),
                max_iterations=agent.snapshot.components.policy.max_iterations,
            ),
        )
        self.assertTrue(config["provider"]["openrouter"]["only_configured_models"])
        model_options = config["provider"]["openrouter"]["models"][UPDATE_MODEL_ID]["options"]
        self.assertEqual(model_options["provider"], expected_route_contract()["provider"])
        self.assertEqual(model_options["reasoning"], {"enabled": False})
        self.assertEqual(
            config["agent"]["build"],
            {
                "permission": {"actor": "deny"},
                "steps": agent.snapshot.components.policy.max_iterations,
                "tool_allowlist": ["bash", "read", "write", "edit", "glob", "grep"],
            },
        )
        self.assertEqual(config["agent"]["title"], {"disable": True})
        self.assertEqual(config["experimental"], {"predict_next_prompt": False})
        self.assertEqual(config["compaction"], {"auto": True, "prune": True})
        self.assertEqual(config["dream"], {"auto": False})
        self.assertEqual(config["distill"], {"auto": False})
        self.assertEqual(config["mcp"], {})
        self.assertEqual(
            config["permission"],
            {
                "actor": "deny",
                "cron": "deny",
                "mcp_sampling": "deny",
                "mcp_tool_search": "deny",
            },
        )

        with self.assertRaises(TypeError):
            locked_mimocode_config(expected_route_contract())
        for invalid in (True, 0, 33):
            with self.subTest(max_iterations=invalid):
                with self.assertRaises(ValueError):
                    locked_mimocode_config(expected_route_contract(), max_iterations=invalid)
        self.assertNotIn(SECRET, json.dumps(config))
        self.assertIn(CANARY.encode(), environment.uploads["/tmp/evoagent-mimo-runtime/projected-task.md"])
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_PROVIDER_ENV"], "1")
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_PROJECT_CONFIG"], "1")
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_CLAUDE_CODE_COMMANDS"], "1")
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_CLAUDE_IMPORT"], "1")
        self.assertEqual(run_call["env"]["OPENROUTER_API_KEY"], PROXY_TOKEN)
        self.assertEqual(
            {
                key: run_call["env"][key]
                for key in (
                    "HOME",
                    "USERPROFILE",
                    "MIMOCODE_HOME",
                    "MIMOCODE_CONFIG_CONTENT",
                    "MIMOCODE_PURE",
                    "MIMOCODE_EXPERIMENTAL",
                    "MIMOCODE_EXPERIMENTAL_CRON",
                    "MIMOCODE_DISABLE_CRON",
                    "MIMOCODE_DISABLE_CHECKPOINT",
                    "MIMOCODE_EXPERIMENTAL_ORCHESTRATOR",
                    "MIMOCODE_EXPERIMENTAL_WORKFLOW_TOOL",
                    "MIMOCODE_EXPERIMENTAL_MCP_TOOL_SEARCH",
                    "MIMOCODE_ENABLE_EXEC_TOOL",
                )
            },
            {
                "HOME": "/tmp/evoagent-mimo-runtime/home",
                "USERPROFILE": "/tmp/evoagent-mimo-runtime/home",
                "MIMOCODE_HOME": "/tmp/evoagent-mimo-runtime/home",
                "MIMOCODE_CONFIG_CONTENT": "{}",
                "MIMOCODE_PURE": "1",
                "MIMOCODE_EXPERIMENTAL": "0",
                "MIMOCODE_EXPERIMENTAL_CRON": "0",
                "MIMOCODE_DISABLE_CRON": "1",
                "MIMOCODE_DISABLE_CHECKPOINT": "1",
                "MIMOCODE_EXPERIMENTAL_ORCHESTRATOR": "0",
                "MIMOCODE_EXPERIMENTAL_WORKFLOW_TOOL": "0",
                "MIMOCODE_EXPERIMENTAL_MCP_TOOL_SEARCH": "0",
                "MIMOCODE_ENABLE_EXEC_TOOL": "0",
            },
        )

    def test_locked_config_disables_unattested_auxiliary_and_actor_model_calls(self) -> None:
        for max_iterations in (1, 12, 32):
            with self.subTest(max_iterations=max_iterations):
                config = locked_mimocode_config(
                    expected_route_contract(),
                    max_iterations=max_iterations,
                )
                self.assertEqual(
                    config["agent"],
                    {
                        "build": {
                            "permission": {"actor": "deny"},
                            "steps": max_iterations,
                            "tool_allowlist": ["bash", "read", "write", "edit", "glob", "grep"],
                        },
                        "checkpoint-writer": {"disable": True},
                        "distill": {"disable": True},
                        "dream": {"disable": True},
                        "max": {"disable": True},
                        "orchestrator": {"disable": True},
                        "summary": {"disable": True},
                        "title": {"disable": True},
                    },
                )
                self.assertEqual(
                    config["experimental"],
                    {"predict_next_prompt": False},
                )
                self.assertEqual(config["compaction"], {"auto": True, "prune": True})
                self.assertEqual(config["memory"], {"disable_write": True})
                self.assertEqual(config["dream"], {"auto": False})
                self.assertEqual(config["distill"], {"auto": False})
                self.assertEqual(config["mcp"], {})
                self.assertEqual(
                    config["permission"],
                    {
                        "actor": "deny",
                        "cron": "deny",
                        "mcp_sampling": "deny",
                        "mcp_tool_search": "deny",
                    },
                )
                self.assertEqual(config["small_model"], config["model"])

    def test_runtime_environment_exactly_isolates_mimocode_state_and_features(self) -> None:
        self.assertEqual(
            runtime_env("/runtime/mimocode.json", "/runtime/home", proxy_token=PROXY_TOKEN),
            {
                "HOME": "/runtime/home",
                "OPENROUTER_API_KEY": PROXY_TOKEN,
                "USERPROFILE": "/runtime/home",
                "MIMOCODE_CONFIG": "/runtime/mimocode.json",
                "MIMOCODE_CONFIG_CONTENT": "{}",
                "MIMOCODE_HOME": "/runtime/home",
                "MIMOCODE_PURE": "1",
                "MIMOCODE_EXPERIMENTAL": "0",
                "MIMOCODE_EXPERIMENTAL_CRON": "0",
                "MIMOCODE_DISABLE_CRON": "1",
                "MIMOCODE_DISABLE_CHECKPOINT": "1",
                "MIMOCODE_EXPERIMENTAL_ORCHESTRATOR": "0",
                "MIMOCODE_EXPERIMENTAL_WORKFLOW_TOOL": "0",
                "MIMOCODE_EXPERIMENTAL_MCP_TOOL_SEARCH": "0",
                "MIMOCODE_ENABLE_EXEC_TOOL": "0",
                "MIMOCODE_DISABLE_PROVIDER_ENV": "1",
                "MIMOCODE_DISABLE_EXTERNAL_SKILLS": "1",
                "MIMOCODE_DISABLE_BUILTIN_SKILLS": "1",
                "MIMOCODE_DISABLE_CLAUDE_CODE": "1",
                "MIMOCODE_DISABLE_CLAUDE_CODE_COMMANDS": "1",
                "MIMOCODE_DISABLE_CLAUDE_CODE_ENV": "1",
                "MIMOCODE_DISABLE_CLAUDE_IMPORT": "1",
                "MIMOCODE_DISABLE_CLAUDE_CODE_MCP": "1",
                "MIMOCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
                "MIMOCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
                "MIMOCODE_DISABLE_PROJECT_CONFIG": "1",
                "MIMOCODE_AUTO_SHARE": "0",
                "MIMOCODE_DISABLE_AUTOUPDATE": "1",
                "NO_COLOR": "1",
            },
        )

    def test_timeout_must_leave_the_frozen_sanitization_margin(self) -> None:
        with self.assertRaisesRegex(ValueError, "sanitization margin"):
            self._agent(timeout_seconds=MIMOCODE_SANITIZATION_MARGIN_SECONDS)

    @unittest.skipIf(HarborAgentContext is None, "optional pinned Harbor is not installed")
    def test_run_leaves_real_harbor_context_empty_for_post_run_hook(self) -> None:
        agent = self._agent(extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN})
        environment = FakeEnvironment()
        context = HarborAgentContext()

        self.assertTrue(context.is_empty())
        asyncio.run(agent.run("complete the official task", environment, context))
        self.assertTrue(context.is_empty())

    def test_run_refuses_a_real_account_credential_in_the_task_container(self) -> None:
        agent = self._agent(extra_env={"OPENROUTER_API_KEY": SECRET})
        with self.assertRaisesRegex(RuntimeError, "local proxy capability"):
            asyncio.run(agent.run("complete the official task", FakeEnvironment(), SimpleNamespace()))

    def test_classified_shell_failures_raise_harbor_nonzero_agent_error(self) -> None:
        cases = {
            MIMOCODE_PROCESS_EXIT: "mimocode_process_failed",
            SANITIZER_REJECT_EXIT: "runtime_sanitization_failed",
            MIMOCODE_AND_SANITIZER_EXIT: "mimocode_and_sanitization_failed",
        }
        for return_code, failure_class in cases.items():
            with self.subTest(failure_class=failure_class):
                agent = self._agent(extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN})
                environment = FakeEnvironment(return_codes=[0, return_code])
                with self.assertRaisesRegex(NonZeroAgentExitCodeError, failure_class):
                    asyncio.run(
                        agent.run(
                            "complete the official task",
                            environment,
                            SimpleNamespace(),
                        )
                    )

    def test_missing_atif_requires_valid_receipt_then_populates_only_safe_failure_metadata(self) -> None:
        agent = self._agent()
        logs = self.root / "logs"
        logs.mkdir()
        context = SimpleNamespace(metadata=None)
        with self.assertRaisesRegex(RuntimeError, "ATIF output and runtime failure receipt are missing"):
            agent.populate_context_post_run(context)

        receipt = write_runtime_failure_receipt(
            logs / FAILURE_RECEIPT_FILENAME,
            mimocode_exit_code=1,
            sanitization_failed=True,
            atif_present=False,
            metadata={
                "snapshot_hash": agent.snapshot.snapshot_sha256,
                "component_hashes": dict(agent.snapshot.component_sha256),
                "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
                "route_contract_sha256": sha256_json(expected_route_contract()),
            },
            model=HARBOR_MODEL_ID,
            seed=43,
        )
        agent.populate_context_post_run(context)
        self.assertEqual(context.metadata["runtime_failure_class"], "mimocode_and_sanitization_failed")
        self.assertEqual(context.metadata["runtime_failure_receipt_sha256"], receipt["receipt_sha256"])
        self.assertEqual(context.n_input_tokens, 0)
        self.assertEqual(context.n_output_tokens, 0)
        self.assertEqual(context.cost_usd, 0.0)
        self.assertFalse((logs / ATTESTATION_FILENAME).exists())
        rendered = json.dumps(context.metadata, sort_keys=True)
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn(SECRET, rendered)

        tampered_receipts: list[dict[str, object]] = []
        stale_hash = dict(receipt)
        stale_hash["failure_stage"] = "mimocode"
        tampered_receipts.append(stale_hash)
        atif_drift = dict(receipt)
        atif_drift["atif_present"] = True
        atif_drift["receipt_sha256"] = sha256_json(
            {key: value for key, value in atif_drift.items() if key != "receipt_sha256"}
        )
        tampered_receipts.append(atif_drift)
        privacy_drift = dict(receipt)
        privacy_drift["raw_response_persisted"] = True
        privacy_drift["receipt_sha256"] = sha256_json(
            {key: value for key, value in privacy_drift.items() if key != "receipt_sha256"}
        )
        tampered_receipts.append(privacy_drift)
        extra_field = dict(receipt)
        extra_field["message"] = "MUST-NOT-BE-ACCEPTED"
        tampered_receipts.append(extra_field)
        for index, tampered in enumerate(tampered_receipts):
            with self.subTest(tamper=index):
                atomic_write_json(logs / FAILURE_RECEIPT_FILENAME, tampered)
                with self.assertRaises(RuntimeError):
                    agent.populate_context_post_run(SimpleNamespace())

    def test_atif_with_mimocode_failure_receipt_binds_reasoning_usage_to_attestation(self) -> None:
        agent = self._agent()
        logs = self.root / "logs"
        logs.mkdir()
        raw = self.root / "raw-reasoning.jsonl"
        raw.write_text(
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {
                        "type": "step-finish",
                        "cost": 0.001,
                        "tokens": {
                            "input": 5,
                            "output": 3,
                            "reasoning": 1,
                            "cache": {"read": 2, "write": 0},
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        metadata = {
            "snapshot_hash": agent.snapshot.snapshot_sha256,
            "component_hashes": dict(agent.snapshot.component_sha256),
            "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
            "route_contract_sha256": sha256_json(expected_route_contract()),
        }
        sanitize_runtime_jsonl(
            raw,
            logs / "trajectory.json",
            model=HARBOR_MODEL_ID,
            seed=43,
            snapshot_metadata_json=json.dumps(metadata),
        )
        receipt = write_runtime_failure_receipt(
            logs / FAILURE_RECEIPT_FILENAME,
            mimocode_exit_code=1,
            sanitization_failed=False,
            atif_present=True,
            metadata=metadata,
            model=HARBOR_MODEL_ID,
            seed=43,
        )
        context = SimpleNamespace()
        agent.populate_context_post_run(context)
        attestation = read_json(logs / ATTESTATION_FILENAME)
        self.assertEqual(attestation["usage"]["completion_tokens"], 3)
        self.assertEqual(attestation["usage"]["reasoning_tokens"], 1)
        self.assertEqual(
            attestation["runtime_failure_receipt_sha256"],
            receipt["receipt_sha256"],
        )
        self.assertEqual(
            context.metadata["runtime_failure_receipt_sha256"],
            receipt["receipt_sha256"],
        )

    def test_post_run_attestation_binds_snapshot_atif_model_seed_runtime_and_usage(self) -> None:
        agent = self._agent()
        logs = self.root / "logs"
        logs.mkdir()
        raw = self.root / "raw.jsonl"
        raw.write_text(
            json.dumps(
                {
                    "type": "tool_call",
                    "tool_name": "bash",
                    "status": "completed",
                    "content": f"raw {CANARY} {SECRET}",
                    "usage": {"input_tokens": 12, "output_tokens": 3, "cached_tokens": 2, "cost_usd": 0.001},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        sanitize_runtime_jsonl(
            raw,
            logs / "trajectory.json",
            model=HARBOR_MODEL_ID,
            seed=43,
            snapshot_metadata_json=json.dumps(
                {
                    "snapshot_hash": agent.snapshot.snapshot_sha256,
                    "component_hashes": dict(agent.snapshot.component_sha256),
                    "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
                    "route_contract_sha256": sha256_json(expected_route_contract()),
                }
            ),
        )
        context = SimpleNamespace(
            n_input_tokens=None,
            n_cache_tokens=None,
            n_output_tokens=None,
            cost_usd=None,
            rollout_details=[],
            metadata={"raw": CANARY},
        )
        agent.populate_context_post_run(context)
        attestation = read_json(logs / ATTESTATION_FILENAME)
        self.assertEqual(attestation["schema_version"], "evoagent-harbor-attestation-v1")
        self.assertEqual(attestation["snapshot_sha256"], agent.snapshot.snapshot_sha256)
        self.assertEqual(attestation["component_sha256"], dict(agent.snapshot.component_sha256))
        self.assertEqual(attestation["seed"], 43)
        self.assertEqual(attestation["model"]["api_id"], UPDATE_MODEL_ID)
        self.assertEqual(attestation["route_contract_sha256"], sha256_json(expected_route_contract()))
        self.assertEqual(attestation["runtime"]["mimocode_archive_sha256"], MIMOCODE_ARCHIVE_SHA256)
        self.assertEqual(attestation["usage"]["cost_usd"], 0.001)
        self.assertEqual(attestation["usage"]["reasoning_tokens"], 0)
        self.assertIsNone(attestation["runtime_failure_receipt_sha256"])
        unsigned = dict(attestation)
        digest = unsigned.pop("attestation_sha256")
        self.assertEqual(digest, sha256_json(unsigned))
        persisted = (logs / "trajectory.json").read_text() + (logs / ATTESTATION_FILENAME).read_text()
        self.assertNotIn(CANARY, persisted)
        self.assertNotIn(SECRET, persisted)
        self.assertEqual(context.metadata["attestation_sha256"], digest)
        self.assertNotIn("raw", context.metadata)

    def test_post_run_rejects_prose_tool_identity_usage_and_route_tampering(self) -> None:
        agent = self._agent()
        raw = self.root / "raw-template.jsonl"
        raw.write_text(
            json.dumps(
                {
                    "type": "tool_call",
                    "tool_name": "bash",
                    "status": "completed",
                    "usage": {"input_tokens": 12, "output_tokens": 3, "cached_tokens": 2, "cost_usd": 0.001},
                }
            ) + "\n",
            encoding="utf-8",
        )
        template_path = self.root / "safe-template.json"
        safe = sanitize_runtime_jsonl(
            raw,
            template_path,
            model=HARBOR_MODEL_ID,
            seed=43,
            snapshot_metadata_json=json.dumps(
                {
                    "snapshot_hash": agent.snapshot.snapshot_sha256,
                    "component_hashes": dict(agent.snapshot.component_sha256),
                    "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
                    "route_contract_sha256": sha256_json(expected_route_contract()),
                }
            ),
        )
        tampered: list[dict[str, object]] = []
        prose = copy.deepcopy(safe)
        prose["steps"][1]["message"] = "raw response"
        tampered.append(prose)
        tool = copy.deepcopy(safe)
        tool["steps"][1]["tool_calls"][0]["function_name"] = "private_CANARY_tool"
        tampered.append(tool)
        usage = copy.deepcopy(safe)
        usage["final_metrics"]["total_cost_usd"] = 999.0
        tampered.append(usage)
        route = copy.deepcopy(safe)
        route["extra"]["route_contract_sha256"] = "0" * 64
        route["agent"]["extra"]["route_contract_sha256"] = "0" * 64
        tampered.append(route)

        for index, payload in enumerate(tampered):
            with self.subTest(index=index):
                logs = self.root / f"tampered-{index}"
                logs.mkdir()
                atomic_write_json(logs / "trajectory.json", payload)
                guarded = self._agent(logs_dir=logs)
                with self.assertRaises(ValueError):
                    guarded.populate_context_post_run(SimpleNamespace())
                self.assertFalse((logs / ATTESTATION_FILENAME).exists())

    def test_asset_and_route_drift_are_rejected_before_base_agent_passthrough(self) -> None:
        with self.assertRaisesRegex(ValueError, "asset_sha256"):
            self._agent(mimocode_asset_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "asset_url"):
            self._agent(mimocode_asset_url="https://example.invalid/mimo.tar.gz")
        with self.assertRaisesRegex(ValueError, "mimocode_version"):
            self._agent(mimocode_version="0.1.14")
        drift = expected_route_contract()
        drift["provider"]["allow_fallbacks"] = True
        with self.assertRaisesRegex(ValueError, "route_contract"):
            self._agent(route_contract=drift)
        with self.assertRaisesRegex(ValueError, "unsupported constructor fields"):
            self._agent(unrecognized_runtime_switch=True)

    def test_pinned_harbor_base_constructor_fields_are_explicitly_preserved(self) -> None:
        logger = logging.getLogger("seagym-evoagent-test")
        mcp_servers = [SimpleNamespace(name="test-mcp")]
        agent = self._agent(
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir="/skills",
            extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN},
        )
        self.assertEqual(agent.extra_env, {"OPENROUTER_API_KEY": PROXY_TOKEN})
        self.assertEqual(agent.mcp_servers, mcp_servers)
        self.assertEqual(agent.skills_dir, "/skills")


if __name__ == "__main__":
    unittest.main()
