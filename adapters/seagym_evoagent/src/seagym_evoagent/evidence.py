"""Privacy projection from train-only SEAGym/Harbor results."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import UUID

from .canonical import contained_path, sha256_file, sha256_json, strict_json_loads
from .harbor_agent import (
    ADAPTER_VERSION,
    ATTESTATION_FILENAME,
    ATTESTATION_SCHEMA,
    SAFE_OBSERVATIONS,
    SAFE_TOOL_NAMES,
)
from .mimocode import (
    HARBOR_RUNTIME_COMMIT,
    MIMOCODE_ARCHIVE_SHA256,
    MIMOCODE_VERSION,
    SEAGYM_COMMIT,
)
from .models import HARBOR_MODEL_ID, UPDATE_MODEL_ID


MAX_HARBOR_JSON_BYTES = 16 * 1024 * 1024
MAX_ATIF_BYTES = 8 * 1024 * 1024
MAX_FAILURE_RECEIPT_BYTES = 64 * 1024
FAILURE_RECEIPT_FILENAME = "evoagent-runtime-failure.json"
FAILURE_RECEIPT_SCHEMA = "evoagent-runtime-failure-v1"
NO_USABLE_ATIF_SKIP_CODE = "no_usable_harbor_atif_evidence"
INCOMPLETE_HARBOR_EVIDENCE_SKIP_CODE = "incomplete_unattested_harbor_evidence"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
FAILURE_RECEIPT_CLASSES = {
    "mimocode_process_failed",
    "runtime_sanitization_failed",
    "mimocode_and_sanitization_failed",
}
FAILURE_RECEIPT_STAGES = {"mimocode", "sanitize"}
MIMOCODE_EXIT_CLASSES = {"nonzero", "signal", "timeout", "spawn_failed", "success", "unknown"}
EXPECTED_HARBOR_AGENT_INFO = {
    "name": "evoagent-mimo",
    "version": ADAPTER_VERSION,
    "model_info": {"name": UPDATE_MODEL_ID, "provider": "openrouter"},
}
EXPECTED_TRIAL_RESULT_KEYS = {
    "id",
    "task_name",
    "trial_name",
    "trial_uri",
    "task_id",
    "source",
    "task_checksum",
    "config",
    "agent_info",
    "agent_result",
    "verifier_result",
    "exception_info",
    "started_at",
    "finished_at",
    "environment_setup",
    "agent_setup",
    "agent_execution",
    "verifier",
    "step_results",
}
EXPECTED_TRIAL_CONFIG_KEYS = {
    "task",
    "trial_name",
    "trials_dir",
    "install_only",
    "timeout_multiplier",
    "agent_timeout_multiplier",
    "verifier_timeout_multiplier",
    "agent_setup_timeout_multiplier",
    "environment_build_timeout_multiplier",
    "agent",
    "environment",
    "verifier",
    "artifacts",
    "extra_instruction_paths",
    "job_id",
}
EXPECTED_AGENT_CONTEXT_KEYS = {
    "n_input_tokens",
    "n_cache_tokens",
    "n_output_tokens",
    "cost_usd",
    "rollout_details",
    "metadata",
}
EXPECTED_AGGREGATE_KEYS = {
    "finished_at",
    "id",
    "n_total_trials",
    "started_at",
    "stats",
    "updated_at",
}
EXPECTED_AGGREGATE_STATS_KEYS = {
    "cost_usd",
    "evals",
    "n_cache_tokens",
    "n_cancelled_trials",
    "n_completed_trials",
    "n_errored_trials",
    "n_input_tokens",
    "n_output_tokens",
    "n_pending_trials",
    "n_retries",
    "n_running_trials",
}
EXPECTED_EVAL_STATS_KEYS = {
    "exception_stats",
    "metrics",
    "n_errors",
    "n_trials",
    "pass_at_k",
    "reward_stats",
}
SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
FAILURE_RECEIPT_KEYS = {
    "schema_version",
    "failure_class",
    "failure_stage",
    "mimocode_exit_class",
    "snapshot_sha256",
    "component_sha256",
    "route_contract_sha256",
    "model",
    "seed",
    "runtime",
    "atif_present",
    "raw_prompt_persisted",
    "raw_response_persisted",
    "reasoning_content_persisted",
    "receipt_sha256",
}
SAFE_STATUS_ALIASES = {
    "pending": "pending",
    "running": "running",
    "success": "success",
    "completed": "success",
    "error": "error",
    "failed": "error",
    "timeout": "timeout",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "unknown": "unknown",
}
TOOL_CATEGORIES = {
    "read": "filesystem_read",
    "read_file": "filesystem_read",
    "write": "filesystem_write",
    "write_file": "filesystem_write",
    "edit": "filesystem_write",
    "apply_patch": "filesystem_write",
    "bash": "shell",
    "shell": "shell",
    "exec": "shell",
    "execute": "shell",
    "terminal": "shell",
    "grep": "search",
    "glob": "search",
    "search": "search",
    "find": "search",
    "webfetch": "network",
    "web_fetch": "network",
    "websearch": "network",
    "web_search": "network",
    "browser": "network",
    "test": "verification",
    "pytest": "verification",
    "task": "delegation",
    "subagent": "delegation",
}


@dataclass(frozen=True)
class EvidenceProjection:
    summary: dict[str, Any]
    evidence_sha256: str
    forbidden_fragments: tuple[str, ...]


class NoUsableHarborATIFEvidence(ValueError):
    """A fully attested error-only batch for which no update may be learned."""

    def __init__(self, projection: EvidenceProjection) -> None:
        super().__init__("train batch contains no usable Harbor ATIF evidence")
        self.projection = projection


class IncompleteHarborTrainEvidence(ValueError):
    """A batch containing unattested Harbor error evidence that cannot drive learning."""

    def __init__(self, projection: EvidenceProjection) -> None:
        super().__init__("train batch contains incomplete unattested Harbor evidence")
        self.projection = projection


def project_train_batch(
    batch: Any,
    *,
    atif_root: Path,
    expected_snapshot_sha256: str,
    expected_component_sha256: dict[str, str],
    expected_route_contract_sha256: str,
    expected_seed: int,
    max_trajectories: int = 64,
) -> EvidenceProjection:
    if getattr(batch, "mode", None) != "train" or getattr(batch, "view_name", None) != "train":
        raise ValueError("EvoAgent updates accept only view_name=train and mode=train")
    trajectories = getattr(batch, "trajectories", None)
    task_ids = getattr(batch, "task_ids", None)
    if not isinstance(trajectories, list) or not trajectories:
        raise ValueError("train batch must contain trajectories")
    if not isinstance(task_ids, list) or not all(isinstance(item, str) and item for item in task_ids):
        raise ValueError("train batch task_ids must be a list of non-empty strings")
    if len(task_ids) != len(trajectories):
        raise ValueError("train batch task_ids must match its trajectories")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("train batch task_ids must be unique")
    if len(trajectories) > max_trajectories:
        raise ValueError("train batch exceeds the configured trajectory bound")
    root = atif_root.resolve(strict=True)

    successes = 0
    scores: list[float] = []
    rewards: list[float] = []
    runtimes: list[float] = []
    input_tokens = output_tokens = cache_tokens = 0
    cost_usd = 0.0
    error_count = 0
    atif_digests: list[str] = []
    missing_error_atif = 0
    failure_receipt_digests: list[str] = []
    unattested_result_digests: list[str] = []
    unattested_result_paths: set[Path] = set()
    unattested_trial_ids: set[str] = set()
    result_paths: set[Path] = set()
    job_dirs: set[Path] = set()
    job_ids: set[str] = set()
    trial_ids: set[str] = set()
    attempt_ids: set[str] = set()
    atif_paths: set[Path] = set()
    failure_receipt_paths: set[Path] = set()
    failure_classes: Counter[str] = Counter()
    failure_stages: Counter[str] = Counter()
    mimocode_exit_classes: Counter[str] = Counter()
    tool_categories: Counter[str] = Counter()
    tool_statuses: Counter[str] = Counter()
    atif_steps = 0
    batch_identity_digests: list[str] = []
    job_provenance_digests: list[str] = []
    evidence_bundle_digests: list[str] = []
    fragments: set[str] = {item for item in task_ids if item}

    for expected_task_id, trajectory in zip(task_ids, trajectories, strict=True):
        _require_train_trajectory(trajectory)
        trajectory_task_id = getattr(trajectory, "task_id", None)
        attempt_id = getattr(trajectory, "attempt_id", None)
        if trajectory_task_id != expected_task_id:
            raise ValueError("train batch task_ids do not match trajectory order")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ValueError("train trajectory attempt_id must be non-empty text")
        if attempt_id in attempt_ids:
            raise ValueError("train trajectory attempt_id was reused")
        attempt_ids.add(attempt_id)
        (
            result_path,
            trial_id,
            trial_name,
            job_id,
            result,
            job_provenance,
        ) = _validated_harbor_result_identity(trajectory, root)
        job_dir = result_path.parent.parent
        if result_path in result_paths or trial_id in trial_ids:
            raise ValueError("Harbor result or trial identity was reused")
        if job_dir in job_dirs or job_id in job_ids:
            raise ValueError("Harbor job directory or id was reused within a train batch")
        if attempt_id != trial_name:
            raise ValueError("train trajectory attempt_id does not match its Harbor trial")
        result_paths.add(result_path)
        job_dirs.add(job_dir)
        job_ids.add(job_id)
        trial_ids.add(trial_id)
        batch_identity_digests.append(
            sha256_json(
                {
                    "attempt_id": attempt_id,
                    "job_id": job_id,
                    "task_id": expected_task_id,
                    "trial_id": trial_id,
                }
            )
        )
        job_provenance_digests.append(sha256_json(job_provenance))
        successes += int(getattr(trajectory, "success", False) is True)
        scores.append(_finite_number(getattr(trajectory, "score", None), "score", minimum=-1_000_000, maximum=1_000_000))
        rewards.append(_finite_number(getattr(trajectory, "reward", None), "reward", minimum=-1_000_000, maximum=1_000_000))
        runtime = getattr(trajectory, "runtime_seconds", None)
        if runtime is not None:
            runtimes.append(_finite_number(runtime, "runtime_seconds", minimum=0, maximum=7 * 24 * 3600))
        error_count += int(getattr(trajectory, "error", None) not in (None, ""))
        attempt_id = getattr(trajectory, "attempt_id", None)
        if isinstance(attempt_id, str) and attempt_id:
            fragments.add(attempt_id)

        cost = getattr(trajectory, "cost", {})
        if not isinstance(cost, dict):
            raise ValueError("trajectory.cost must be an object")
        input_tokens += _safe_int(cost.get("n_input_tokens", cost.get("prompt_tokens", 0)), "input tokens")
        output_tokens += _safe_int(cost.get("n_output_tokens", cost.get("completion_tokens", 0)), "output tokens")
        cache_tokens += _safe_int(cost.get("n_cache_tokens", cost.get("cached_tokens", 0)), "cache tokens")
        cost_usd += _finite_number(cost.get("cost_usd", 0), "cost_usd", minimum=0, maximum=100_000)

        atif_path = _resolve_atif_path(trajectory, root)
        if atif_path is None:
            receipt_path = _resolve_failure_receipt_path(
                trajectory,
                root,
                required=True,
                allow_missing_derived=True,
            )
            if receipt_path is None:
                result_digest, result_path, trial_id = _unattested_harbor_result_digest(
                    trajectory,
                    root,
                )
                if result_path in unattested_result_paths or trial_id in unattested_trial_ids:
                    raise ValueError("unattested Harbor errored result was reused")
                unattested_result_paths.add(result_path)
                unattested_trial_ids.add(trial_id)
                unattested_result_digests.append(result_digest)
                continue
            if receipt_path in failure_receipt_paths:
                raise ValueError("Harbor failure receipt path was reused")
            failure_receipt_paths.add(receipt_path)
            receipt = _read_failure_receipt(
                receipt_path,
                root=root,
                expected_snapshot_sha256=expected_snapshot_sha256,
                expected_component_sha256=expected_component_sha256,
                expected_route_contract_sha256=expected_route_contract_sha256,
                expected_seed=expected_seed,
                expected_atif_present=False,
            )
            _validate_failure_result_context(result, receipt)
            _validate_agent_evidence_inventory(
                result_path,
                root,
                {receipt_path},
            )
            missing_error_atif += 1
            failure_receipt_digests.append(receipt["receipt_sha256"])
            evidence_bundle_digests.append(
                sha256_json(
                    {
                        "result_sha256": sha256_file(
                            result_path,
                            max_bytes=MAX_HARBOR_JSON_BYTES,
                        ),
                        "atif_sha256": None,
                        "attestation_sha256": None,
                        "failure_receipt_sha256": receipt["receipt_sha256"],
                        "job_provenance": job_provenance,
                    }
                )
            )
            failure_classes[receipt["failure_class"]] += 1
            failure_stages[receipt["failure_stage"]] += 1
            mimocode_exit_classes[receipt["mimocode_exit_class"]] += 1
            continue
        if atif_path in atif_paths:
            raise ValueError("Harbor ATIF path was reused")
        atif_paths.add(atif_path)
        digest = sha256_file(atif_path, max_bytes=MAX_ATIF_BYTES)
        atif_digests.append(digest)
        structural = _read_atif_structure(
            atif_path,
            expected_snapshot_sha256=expected_snapshot_sha256,
            expected_component_sha256=expected_component_sha256,
            expected_route_contract_sha256=expected_route_contract_sha256,
            expected_seed=expected_seed,
        )
        atif_steps += structural["steps"]
        tool_categories.update(structural["tool_categories"])
        tool_statuses.update(structural["tool_statuses"])
        receipt_path = _resolve_failure_receipt_path(trajectory, root, required=False)
        trajectory_error = getattr(trajectory, "error", None)
        receipt: dict[str, Any] | None = None
        if receipt_path is not None:
            if receipt_path in failure_receipt_paths:
                raise ValueError("Harbor failure receipt path was reused")
            failure_receipt_paths.add(receipt_path)
            if getattr(trajectory, "success", None) is not False or not isinstance(trajectory_error, str) or not trajectory_error:
                raise ValueError("Harbor failure receipt requires an explicit errored trajectory")
            receipt = _read_failure_receipt(
                receipt_path,
                root=root,
                expected_snapshot_sha256=expected_snapshot_sha256,
                expected_component_sha256=expected_component_sha256,
                expected_route_contract_sha256=expected_route_contract_sha256,
                expected_seed=expected_seed,
                expected_atif_present=True,
            )
            failure_receipt_digests.append(receipt["receipt_sha256"])
            failure_classes[receipt["failure_class"]] += 1
            failure_stages[receipt["failure_stage"]] += 1
            mimocode_exit_classes[receipt["mimocode_exit_class"]] += 1
        attestation_path, attestation_sha256 = _validate_atif_attestation(
            atif_path,
            atif_sha256=digest,
            usage=structural["usage"],
            result=result,
            root=root,
            expected_snapshot_sha256=expected_snapshot_sha256,
            expected_component_sha256=expected_component_sha256,
            expected_route_contract_sha256=expected_route_contract_sha256,
            expected_seed=expected_seed,
            failure_receipt_sha256=(receipt["receipt_sha256"] if receipt is not None else None),
        )
        allowed_agent_evidence = {atif_path, attestation_path}
        if receipt_path is not None:
            allowed_agent_evidence.add(receipt_path)
        _validate_agent_evidence_inventory(
            result_path,
            root,
            allowed_agent_evidence,
        )
        evidence_bundle_digests.append(
            sha256_json(
                {
                    "result_sha256": sha256_file(
                        result_path,
                        max_bytes=MAX_HARBOR_JSON_BYTES,
                    ),
                    "atif_sha256": digest,
                    "attestation_sha256": attestation_sha256,
                    "failure_receipt_sha256": (
                        receipt["receipt_sha256"] if receipt is not None else None
                    ),
                    "job_provenance": job_provenance,
                }
            )
        )
        if isinstance(trajectory_error, str) and trajectory_error and receipt is None:
            result_digest, result_path, trial_id = _unattested_harbor_result_digest(
                trajectory,
                root,
                allowed_agent_evidence={atif_path, attestation_path},
            )
            if result_path in unattested_result_paths or trial_id in unattested_trial_ids:
                raise ValueError("unattested Harbor errored result was reused")
            unattested_result_paths.add(result_path)
            unattested_trial_ids.add(trial_id)
            unattested_result_digests.append(result_digest)
            continue

    count = len(trajectories)
    if unattested_result_digests:
        # No score, reward, usage, ATIF, or receipt from this batch is eligible
        # for the update model.  Persist only a bounded proof that a complete
        # batch was rejected as learning evidence; the caller must skip the
        # whole update rather than learn from a convenient subset.
        incomplete_summary = {
            "schema_version": "evoagent-incomplete-train-evidence-v1",
            "num_trajectories": count,
            "unattested_harbor_failures": len(unattested_result_digests),
            "verified_atif_documents": len(atif_digests),
            "verified_failure_receipt_documents": len(failure_receipt_digests),
            "verified_atif_set_sha256": sha256_json(sorted(atif_digests)),
            "verified_failure_receipt_set_sha256": sha256_json(sorted(failure_receipt_digests)),
            "batch_task_job_identity_sha256": sha256_json(sorted(batch_identity_digests)),
            "verified_job_provenance_set_sha256": sha256_json(
                sorted(job_provenance_digests)
            ),
            "verified_evidence_bundle_set_sha256": sha256_json(
                sorted(evidence_bundle_digests)
            ),
            "unattested_result_set_sha256": sha256_json(sorted(unattested_result_digests)),
            "eligible_for_update": False,
        }
        raise IncompleteHarborTrainEvidence(
            EvidenceProjection(
                summary=incomplete_summary,
                evidence_sha256=sha256_json(incomplete_summary),
                forbidden_fragments=tuple(sorted(fragments)),
            )
        )
    summary = {
        "schema_version": "evoagent-observable-train-evidence-v2",
        "num_trajectories": count,
        "success_count": successes,
        "failure_count": count - successes,
        "error_count": error_count,
        "score": _numeric_summary(scores),
        "reward": _numeric_summary(rewards),
        "runtime_seconds": _numeric_summary(runtimes),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_tokens": cache_tokens,
            "cost_usd": round(cost_usd, 12),
        },
        "atif": {
            "documents": len(atif_digests),
            "missing_error_documents": missing_error_atif,
            "steps": atif_steps,
            "set_sha256": sha256_json(sorted(atif_digests)),
            "tool_categories": dict(sorted(tool_categories.items())),
            "tool_statuses": dict(sorted(tool_statuses.items())),
        },
        "runtime_failures": {
            "documents": len(failure_receipt_digests),
            "set_sha256": sha256_json(sorted(failure_receipt_digests)),
            "failure_classes": dict(sorted(failure_classes.items())),
            "failure_stages": dict(sorted(failure_stages.items())),
            "mimocode_exit_classes": dict(sorted(mimocode_exit_classes.items())),
        },
        "evidence_bundles": {
            "documents": len(evidence_bundle_digests),
            "set_sha256": sha256_json(sorted(evidence_bundle_digests)),
        },
        "job_provenance": {
            "documents": len(job_provenance_digests),
            "set_sha256": sha256_json(sorted(job_provenance_digests)),
        },
    }
    projection = EvidenceProjection(
        summary=summary,
        evidence_sha256=sha256_json(summary),
        forbidden_fragments=tuple(sorted(fragments)),
    )
    if not atif_digests:
        # Reaching this branch proves that every trajectory was an explicit
        # error with a contained, immutable, identity-bound failure receipt.
        # The caller may persist a no-call skip, but must not invent evidence.
        raise NoUsableHarborATIFEvidence(projection)
    return projection


def _require_train_trajectory(trajectory: Any) -> None:
    if getattr(trajectory, "mode", None) != "train" or getattr(trajectory, "view_name", None) != "train":
        raise ValueError("every trajectory in an update must be train-only")
    if not isinstance(getattr(trajectory, "success", None), bool):
        raise ValueError("trajectory.success must be boolean")
    error = getattr(trajectory, "error", None)
    if error is not None and not isinstance(error, str):
        raise ValueError("trajectory.error must be text or null")
    if isinstance(error, str) and not error.strip():
        raise ValueError("trajectory.error cannot be blank")
    if getattr(trajectory, "success", False) is True and error not in (None, ""):
        raise ValueError("a successful trajectory cannot contain an error")


def _validated_harbor_result_identity(
    trajectory: Any,
    root: Path,
) -> tuple[Path, str, str, str, dict[str, Any], dict[str, str]]:
    """Bind every train trajectory to a real canonical Harbor child result."""

    refs = getattr(trajectory, "refs", None)
    if not isinstance(refs, dict):
        raise ValueError("trajectory.refs must be an object")
    raw_result = refs.get("result_path")
    if not isinstance(raw_result, str) or not raw_result:
        raise ValueError("train trajectory lacks a Harbor result_path")
    try:
        result_path = contained_path(
            root,
            _root_relative_path(root, raw_result),
            must_exist=True,
        )
    except (OSError, ValueError) as exc:
        raise ValueError("train trajectory result is missing or invalid") from exc
    if result_path.name != "result.json" or not result_path.is_file() or result_path.is_symlink():
        raise ValueError("train trajectory result is not a regular Harbor result file")
    job_dir = result_path.parent.parent
    raw_job_dir = refs.get("job_dir")
    if not isinstance(raw_job_dir, str) or not raw_job_dir:
        raise ValueError("train trajectory has an invalid Harbor job_dir")
    try:
        declared_job_dir = contained_path(
            root,
            _root_relative_path(root, raw_job_dir),
            must_exist=True,
        )
    except (OSError, ValueError) as exc:
        raise ValueError("train trajectory has an invalid Harbor job_dir") from exc
    if declared_job_dir != job_dir:
        raise ValueError("train trajectory result is not bound to its Harbor job")
    if (
        not job_dir.is_dir()
        or job_dir.is_symlink()
        or result_path.parent.parent != job_dir
    ):
        raise ValueError("train trajectory result is not bound to its Harbor job")
    result = _read_scanned_json_artifact(
        result_path,
        root=root,
        max_bytes=MAX_HARBOR_JSON_BYTES,
        label="Harbor child result",
    )
    if not isinstance(result, dict) or set(result) != EXPECTED_TRIAL_RESULT_KEYS:
        raise ValueError("Harbor train result root schema drifted")
    try:
        trial_id = str(UUID(str(result.get("id"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Harbor train result has an invalid trial id") from exc
    if trial_id != result.get("id"):
        raise ValueError("Harbor train result has a non-canonical trial id")
    trial_name = result.get("trial_name")
    task_name = result.get("task_name")
    trajectory_task_id = getattr(trajectory, "task_id", None)
    if not isinstance(trial_name, str) or trial_name != result_path.parent.name:
        raise ValueError("Harbor train result has an invalid trial binding")
    if result.get("trial_uri") != result_path.parent.as_uri():
        raise ValueError("Harbor train result has an invalid trial URI binding")
    pinned_attempt_id = refs.get("attempt_id") or refs.get("trial_name") or refs.get("trial_uri")
    if pinned_attempt_id != getattr(trajectory, "attempt_id", None) or pinned_attempt_id != trial_name:
        raise ValueError("train trajectory attempt_id does not match its Harbor trial")
    if "trial_name" in refs and refs["trial_name"] != trial_name:
        raise ValueError("Harbor train result differs from its declared trial name")
    if "trial_uri" in refs and refs["trial_uri"] != result.get("trial_uri"):
        raise ValueError("Harbor train result differs from its declared trial URI")
    if result.get("source") != job_dir.name:
        raise ValueError("Harbor train result has an invalid job source binding")
    if "harbor_source" in refs and refs["harbor_source"] != result.get("source"):
        raise ValueError("Harbor train result differs from its declared job source")
    if (
        not isinstance(trajectory_task_id, str)
        or not isinstance(task_name, str)
        or task_name not in {trajectory_task_id, trajectory_task_id.rsplit("/", 1)[-1]}
    ):
        raise ValueError("Harbor train result has an invalid task identity")
    if "harbor_task_name" in refs and refs["harbor_task_name"] != task_name:
        raise ValueError("Harbor train result differs from its declared task name")
    serialized_task_id = result.get("task_id")
    if (
        not isinstance(serialized_task_id, dict)
        or set(serialized_task_id) != {"path"}
        or not isinstance(serialized_task_id.get("path"), str)
        or Path(serialized_task_id["path"]).name != task_name
    ):
        raise ValueError("Harbor train result has an invalid task binding")
    task_checksum = result.get("task_checksum")
    if not isinstance(task_checksum, str) or not HEX64.fullmatch(task_checksum):
        raise ValueError("Harbor train result has an invalid task checksum")
    config = result.get("config")
    if (
        not isinstance(config, dict)
        or set(config) != EXPECTED_TRIAL_CONFIG_KEYS
        or config.get("trial_name") != trial_name
        or config.get("install_only") is not False
        or not isinstance(config.get("task"), dict)
        or set(config["task"]) != {"path"}
        or config["task"].get("path") != serialized_task_id["path"]
        or not isinstance(config.get("trials_dir"), str)
        or contained_path(root, Path(config["trials_dir"]), must_exist=True) != job_dir
    ):
        raise ValueError("Harbor train result has an invalid config binding")
    try:
        job_id = str(UUID(str(config.get("job_id"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Harbor train result has an invalid job id") from exc
    if job_id != config.get("job_id"):
        raise ValueError("Harbor train result has a non-canonical job id")
    config_agent = config.get("agent")
    if (
        not isinstance(config_agent, dict)
        or config_agent.get("import_path")
        != "seagym_evoagent.harbor_agent:EvoAgentMiMo"
        or config_agent.get("model_name") != HARBOR_MODEL_ID
    ):
        raise ValueError("Harbor train result has an invalid agent config")
    if result.get("agent_info") != EXPECTED_HARBOR_AGENT_INFO:
        raise ValueError("Harbor train result has an invalid agent identity")
    agent_result = result.get("agent_result")
    if (
        not isinstance(agent_result, dict)
        or set(agent_result) != EXPECTED_AGENT_CONTEXT_KEYS
        or agent_result.get("rollout_details") is not None
        or not isinstance(agent_result.get("metadata"), dict)
    ):
        raise ValueError("Harbor train result AgentContext schema drifted")
    for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens"):
        _strict_nonnegative_int(agent_result.get(key), f"Harbor child {key}")
    _finite_number(
        agent_result.get("cost_usd"),
        "Harbor child cost_usd",
        minimum=0.0,
        maximum=100_000.0,
    )
    if "job_id" in refs and refs["job_id"] != job_id:
        raise ValueError("Harbor train result differs from its declared job id")
    if refs.get("task_checksum") != result.get("task_checksum"):
        raise ValueError("Harbor train result differs from its declared task checksum")
    error = getattr(trajectory, "error", None)
    if (result.get("exception_info") is not None) != (error not in (None, "")):
        raise ValueError("Harbor train result error state differs from its trajectory")
    exception_info = result.get("exception_info")
    if exception_info is not None:
        expected_exception_keys = {
            "exception_type",
            "exception_message",
            "exception_traceback",
            "occurred_at",
        }
        if not isinstance(exception_info, dict) or set(exception_info) != expected_exception_keys:
            raise ValueError("Harbor train result ExceptionInfo schema drifted")
        if not all(isinstance(exception_info.get(key), str) for key in expected_exception_keys):
            raise ValueError("Harbor train result ExceptionInfo contains an invalid field")
        if not exception_info["exception_type"] or not exception_info["occurred_at"]:
            raise ValueError("Harbor train result ExceptionInfo identity is missing")
        _parse_required_iso_timestamp(
            exception_info["occurred_at"],
            "Harbor train result exception occurred_at",
        )
    if result.get("step_results") is not None:
        raise ValueError("Harbor train result contains unexpected step_results")
    _validate_normalized_harbor_result(trajectory, result)
    job_provenance = _validate_harbor_job_provenance(
        trajectory,
        result_path=result_path,
        root=root,
        result=result,
        job_id=job_id,
    )
    return result_path, trial_id, trial_name, job_id, result, job_provenance


def _validate_normalized_harbor_result(trajectory: Any, result: dict[str, Any]) -> None:
    """Recompute the pinned SEAGym Harbor normalization before any learning call."""

    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict) or set(verifier) != {"rewards"}:
        raise ValueError("Harbor train result verifier_result schema is invalid")
    raw_rewards = verifier.get("rewards")
    if not isinstance(raw_rewards, dict) or set(raw_rewards) != {"reward"}:
        raise ValueError("Harbor train result reward schema drifted from the frozen task scoring")
    reward = _finite_number(
        raw_rewards["reward"],
        "Harbor train result reward",
        minimum=0.0,
        maximum=1.0,
    )
    if reward not in {0.0, 1.0}:
        raise ValueError("Harbor train result reward is not binary")
    rewards = {"reward": reward}
    score = reward
    exception_info = result.get("exception_info")
    if exception_info is not None and reward != 0.0:
        raise ValueError("errored Harbor train result must have zero reward")
    error = None if exception_info is None else str(exception_info)
    success = exception_info is None and reward >= 1.0
    for phase in ("environment_setup", "agent_setup", "agent_execution", "verifier"):
        phase_timing = result.get(phase)
        if exception_info is None or phase_timing is not None:
            _validate_required_timing(result.get(phase), f"Harbor train result {phase}")
    agent_result = result.get("agent_result") or {}
    if not isinstance(agent_result, dict):
        raise ValueError("Harbor train result agent_result is invalid")
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
    runtime_seconds = _normalized_trial_runtime_seconds(result)
    expected = {
        "success": success,
        "score": score,
        "reward": max(rewards.values(), default=0.0),
        "rewards": rewards,
        "cost": cost,
        "runtime_seconds": runtime_seconds,
        "error": error,
    }
    actual = {
        "success": getattr(trajectory, "success", None),
        "score": getattr(trajectory, "score", None),
        "reward": getattr(trajectory, "reward", None),
        "rewards": getattr(trajectory, "rewards", None),
        "cost": getattr(trajectory, "cost", None),
        "runtime_seconds": getattr(trajectory, "runtime_seconds", None),
        "error": getattr(trajectory, "error", None),
    }
    if actual != expected:
        mismatched = sorted(key for key in expected if actual[key] != expected[key])
        raise ValueError(
            "train trajectory differs from pinned Harbor normalization: "
            + ",".join(mismatched)
        )


def _parse_required_iso_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _validate_required_timing(value: Any, label: str) -> float:
    if not isinstance(value, dict) or set(value) != {"started_at", "finished_at"}:
        raise ValueError(f"{label} timing schema is invalid")
    started_at = _parse_required_iso_timestamp(value["started_at"], f"{label} started_at")
    finished_at = _parse_required_iso_timestamp(value["finished_at"], f"{label} finished_at")
    elapsed = (finished_at - started_at).total_seconds()
    if elapsed < 0:
        raise ValueError(f"{label} finished_at precedes started_at")
    return elapsed


def _normalized_trial_runtime_seconds(result: dict[str, Any]) -> float:
    return _validate_required_timing(
        {
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
        },
        "Harbor train result",
    )


def _validate_harbor_job_provenance(
    trajectory: Any,
    *,
    result_path: Path,
    root: Path,
    result: dict[str, Any],
    job_id: str,
) -> dict[str, str]:
    refs = getattr(trajectory, "refs", None)
    if not isinstance(refs, dict):
        raise ValueError("trajectory.refs must be an object")
    returncode = refs.get("harbor_returncode")
    if isinstance(returncode, bool) or returncode != 0:
        raise ValueError("Harbor single-task job did not exit successfully")

    job_dir = result_path.parent.parent
    task_name = result["task_name"]
    trial_name = result["trial_name"]
    child_config = result["config"]
    agent_context = result["agent_result"]
    error_present = result["exception_info"] is not None
    reward = float(result["verifier_result"]["rewards"]["reward"])

    job_config_path = job_dir / "config.json"
    job_config = _read_scanned_json_artifact(
        job_config_path,
        root=root,
        max_bytes=MAX_HARBOR_JSON_BYTES,
        label="Harbor job config",
    )
    if not isinstance(job_config, dict) or job_config.get("job_name") != job_dir.name:
        raise ValueError("Harbor single-task job config identity drifted")
    jobs_dir = job_config.get("jobs_dir")
    if (
        not isinstance(jobs_dir, str)
        or not jobs_dir
        or not Path(jobs_dir).is_absolute()
        or contained_path(root, Path(jobs_dir), must_exist=True) != root
    ):
        raise ValueError("Harbor single-task job config root drifted")
    if (
        isinstance(job_config.get("n_attempts"), bool)
        or job_config.get("n_attempts") != 1
        or isinstance(job_config.get("n_concurrent_trials"), bool)
        or job_config.get("n_concurrent_trials") != 1
    ):
        raise ValueError("Harbor job is not one-task/one-attempt serial execution")
    retry = job_config.get("retry")
    if not isinstance(retry, dict) or retry.get("max_retries") != 0:
        raise ValueError("Harbor job config enables retries")
    agents = job_config.get("agents")
    if agents != [child_config["agent"]]:
        raise ValueError("Harbor job AgentConfig differs from its child TrialConfig")
    if job_config.get("tasks") != []:
        raise ValueError("Harbor job config task selection drifted")
    datasets = job_config.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != 1 or not isinstance(datasets[0], dict):
        raise ValueError("Harbor job config dataset selection is invalid")
    dataset = datasets[0]
    patched_job_dir = root / "_patched_tasksets" / job_dir.name
    try:
        controlled_patched_job = contained_path(root, patched_job_dir, must_exist=True)
    except (OSError, ValueError) as exc:
        raise ValueError("Harbor patched taskset is missing or invalid") from exc
    if _is_linklike(patched_job_dir) or not controlled_patched_job.is_dir():
        raise ValueError("Harbor patched taskset is not a regular directory")
    raw_dataset_path = dataset.get("path")
    if (
        not isinstance(raw_dataset_path, str)
        or not Path(raw_dataset_path).is_absolute()
        or contained_path(root, Path(raw_dataset_path), must_exist=True)
        != controlled_patched_job
        or dataset.get("task_names") != [task_name]
        or dataset.get("n_tasks") != 1
    ):
        raise ValueError("Harbor job config patched dataset identity drifted")
    patched_children = list(controlled_patched_job.iterdir())
    if (
        len(patched_children) != 1
        or _is_linklike(patched_children[0])
        or not patched_children[0].is_dir()
        or patched_children[0].name != task_name
    ):
        raise ValueError("Harbor patched taskset does not contain exactly its frozen task")
    serialized_task_path = Path(result["task_id"]["path"])
    if (
        not serialized_task_path.is_absolute()
        or contained_path(root, serialized_task_path, must_exist=True)
        != patched_children[0].resolve(strict=True)
    ):
        raise ValueError("Harbor child task path is not bound to its patched dataset")

    persisted_child_config = _read_scanned_json_artifact(
        result_path.parent / "config.json",
        root=root,
        max_bytes=MAX_HARBOR_JSON_BYTES,
        label="Harbor child config",
    )
    if persisted_child_config != child_config:
        raise ValueError("Harbor child config differs from its embedded TrialConfig")

    direct_job_entries = list(job_dir.iterdir())
    if any(_is_linklike(path) for path in direct_job_entries):
        raise ValueError("Harbor job contains a link-like entry")
    child_directories = {
        path.resolve(strict=True) for path in direct_job_entries if path.is_dir()
    }
    if child_directories != {result_path.parent.resolve(strict=True)}:
        raise ValueError("Harbor job does not contain exactly one child trial")
    child_results = sorted(job_dir.glob("*/result.json"))
    if len(child_results) != 1 or contained_path(root, child_results[0], must_exist=True) != result_path:
        raise ValueError("Harbor job does not contain exactly its bound child result")

    aggregate_path = job_dir / "result.json"
    aggregate = _read_scanned_json_artifact(
        aggregate_path,
        root=root,
        max_bytes=MAX_HARBOR_JSON_BYTES,
        label="Harbor job aggregate",
    )
    if not isinstance(aggregate, dict) or set(aggregate) != EXPECTED_AGGREGATE_KEYS:
        raise ValueError("Harbor job aggregate schema drifted")
    try:
        aggregate_job_id = str(UUID(str(aggregate.get("id"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Harbor job aggregate id is invalid") from exc
    if aggregate_job_id != aggregate.get("id") or aggregate_job_id != job_id:
        raise ValueError("Harbor job aggregate id differs from its child")
    if _strict_nonnegative_int(
        aggregate.get("n_total_trials"),
        "Harbor aggregate n_total_trials",
        maximum=1,
    ) != 1:
        raise ValueError("Harbor aggregate is not a one-task job")
    aggregate_started = _parse_required_iso_timestamp(
        aggregate.get("started_at"),
        "Harbor aggregate started_at",
    )
    aggregate_updated = _parse_required_iso_timestamp(
        aggregate.get("updated_at"),
        "Harbor aggregate updated_at",
    )
    aggregate_finished = _parse_required_iso_timestamp(
        aggregate.get("finished_at"),
        "Harbor aggregate finished_at",
    )
    if not aggregate_started <= aggregate_updated <= aggregate_finished:
        raise ValueError("Harbor aggregate timestamps are inconsistent")

    stats = aggregate.get("stats")
    if not isinstance(stats, dict) or set(stats) != EXPECTED_AGGREGATE_STATS_KEYS:
        raise ValueError("Harbor aggregate stats schema drifted")
    count_keys = (
        "n_cancelled_trials",
        "n_completed_trials",
        "n_errored_trials",
        "n_pending_trials",
        "n_retries",
        "n_running_trials",
    )
    counts = {
        key: _strict_nonnegative_int(stats.get(key), f"Harbor aggregate {key}", maximum=1)
        for key in count_keys
    }
    expected_errored = int(error_present)
    if (
        counts["n_completed_trials"] != 1
        or counts["n_errored_trials"] != expected_errored
        or any(
            counts[key] != 0
            for key in (
                "n_cancelled_trials",
                "n_pending_trials",
                "n_retries",
                "n_running_trials",
            )
        )
    ):
        raise ValueError("Harbor aggregate completion counts differ from its child")
    for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens"):
        if _strict_nonnegative_int(stats.get(key), f"Harbor aggregate {key}") != agent_context[key]:
            raise ValueError("Harbor aggregate token usage differs from its child")
    aggregate_cost = _finite_number(
        stats.get("cost_usd"),
        "Harbor aggregate cost_usd",
        minimum=0.0,
        maximum=100_000.0,
    )
    if not math.isclose(aggregate_cost, float(agent_context["cost_usd"]), abs_tol=1e-9):
        raise ValueError("Harbor aggregate cost differs from its child")

    evals = stats.get("evals")
    agent_info = result["agent_info"]
    expected_eval_key = (
        f"{agent_info['name']}__{agent_info['model_info']['name']}__{result['source']}"
    )
    if not isinstance(evals, dict) or set(evals) != {expected_eval_key}:
        raise ValueError("Harbor aggregate eval identity differs from its child")
    eval_stats = evals[expected_eval_key]
    if not isinstance(eval_stats, dict) or set(eval_stats) != EXPECTED_EVAL_STATS_KEYS:
        raise ValueError("Harbor aggregate eval stats schema drifted")
    if (
        _strict_nonnegative_int(eval_stats.get("n_trials"), "Harbor eval n_trials", maximum=1)
        != 1
        or _strict_nonnegative_int(eval_stats.get("n_errors"), "Harbor eval n_errors", maximum=1)
        != expected_errored
        or not isinstance(eval_stats.get("pass_at_k"), dict)
    ):
        raise ValueError("Harbor aggregate eval counts are inconsistent")
    if eval_stats.get("metrics") != [{"mean": reward}]:
        raise ValueError("Harbor aggregate metrics differ from its child")
    reward_stats = eval_stats.get("reward_stats")
    if not isinstance(reward_stats, dict) or set(reward_stats) != {"reward"}:
        raise ValueError("Harbor aggregate reward inventory differs from its child")
    per_value = reward_stats["reward"]
    if not isinstance(per_value, dict) or len(per_value) != 1:
        raise ValueError("Harbor aggregate reward stats are invalid")
    serialized_value, trial_names = next(iter(per_value.items()))
    try:
        parsed_value = float(serialized_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Harbor aggregate reward value is invalid") from exc
    if not math.isclose(parsed_value, reward, abs_tol=1e-9) or trial_names != [trial_name]:
        raise ValueError("Harbor aggregate reward stats differ from its child")
    expected_exception_stats = (
        {result["exception_info"]["exception_type"]: [trial_name]}
        if error_present
        else {}
    )
    if eval_stats.get("exception_stats") != expected_exception_stats:
        raise ValueError("Harbor aggregate exception stats differ from its child")
    return {
        "aggregate_sha256": sha256_file(
            aggregate_path,
            max_bytes=MAX_HARBOR_JSON_BYTES,
        ),
        "child_config_sha256": sha256_file(
            result_path.parent / "config.json",
            max_bytes=MAX_HARBOR_JSON_BYTES,
        ),
        "job_config_sha256": sha256_file(
            job_config_path,
            max_bytes=MAX_HARBOR_JSON_BYTES,
        ),
    }


def _resolve_atif_path(trajectory: Any, root: Path) -> Path | None:
    refs = getattr(trajectory, "refs", None)
    if not isinstance(refs, dict):
        raise ValueError("trajectory.refs must be an object")
    explicit_paths: list[Path] = []
    for key in ("atif_path", "trajectory_path"):
        if key not in refs:
            continue
        explicit = refs[key]
        if not isinstance(explicit, str) or not explicit:
            raise ValueError("trajectory ATIF reference must be non-empty text")
        explicit_paths.append(_root_relative_path(root, explicit))
    result_path = refs.get("result_path")
    derived_paths: list[Path] = []
    if result_path is not None:
        if not isinstance(result_path, str) or not result_path:
            raise ValueError("trajectory result_path must be non-empty text")
        controlled_result = contained_path(
            root,
            _root_relative_path(root, result_path),
            must_exist=False,
        )
        if controlled_result.name != "result.json":
            raise ValueError("trajectory result_path is not a Harbor result file")
        trial_dir = controlled_result.parent
        derived_paths = [
            trial_dir / "agent" / "trajectory.json",
            trial_dir / "agent" / "atif.json",
        ]

    resolved_explicit: list[Path] = []
    for candidate in explicit_paths:
        unresolved = contained_path(root, candidate, must_exist=False)
        if not unresolved.exists():
            raise ValueError("declared Harbor ATIF evidence is missing")
        resolved = contained_path(root, candidate, must_exist=True)
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("train trajectory ATIF reference is not a regular file")
        resolved_explicit.append(resolved)
    if resolved_explicit:
        if not derived_paths:
            raise ValueError("declared Harbor ATIF evidence is not bound to a result")
        resolved_derived = {
            contained_path(root, candidate, must_exist=False) for candidate in derived_paths
        }
        if any(candidate not in resolved_derived for candidate in resolved_explicit):
            raise ValueError("declared Harbor ATIF evidence does not match its Harbor trial")
        if len(set(resolved_explicit)) != 1:
            raise ValueError("trajectory declares conflicting Harbor ATIF references")

    existing_derived: list[Path] = []
    for candidate in derived_paths:
        unresolved = contained_path(root, candidate, must_exist=False)
        if not unresolved.exists():
            continue
        resolved = contained_path(root, candidate, must_exist=True)
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("train trajectory ATIF reference is not a regular file")
        existing_derived.append(resolved)
    if len(existing_derived) > 1:
        raise ValueError("Harbor trial contains ambiguous ATIF evidence")
    if resolved_explicit:
        if not existing_derived or existing_derived[0] != resolved_explicit[0]:
            raise ValueError("declared Harbor ATIF evidence does not match its Harbor trial")
        return resolved_explicit[0]
    if existing_derived:
        return existing_derived[0]
    error = getattr(trajectory, "error", None)
    if getattr(trajectory, "success", None) is False and isinstance(error, str) and error:
        # SEAGym creates zero-score results for errored/cancelled Harbor trials
        # that never produced result.json or an agent directory. Preserve the
        # observable failure count, but never invent an ATIF document or relax
        # containment for a declared reference.
        return None
    raise ValueError("train trajectory does not reference a contained Harbor ATIF file")


def _resolve_failure_receipt_path(
    trajectory: Any,
    root: Path,
    *,
    required: bool,
    allow_missing_derived: bool = False,
) -> Path | None:
    refs = getattr(trajectory, "refs", None)
    if not isinstance(refs, dict):
        raise ValueError("trajectory.refs must be an object")
    candidates: list[Path] = []
    explicit_declared = "failure_receipt_path" in refs
    explicit = refs.get("failure_receipt_path")
    if explicit_declared:
        if not isinstance(explicit, str) or not explicit:
            raise ValueError("trajectory failure_receipt_path must be non-empty text")
        candidates.append(_root_relative_path(root, explicit))
    result_path = refs.get("result_path")
    if result_path is not None:
        if not isinstance(result_path, str) or not result_path:
            raise ValueError("trajectory result_path must be non-empty text")
        trial_dir = _root_relative_path(root, result_path).parent
        derived = trial_dir / "agent" / FAILURE_RECEIPT_FILENAME
        if candidates:
            explicit_resolved = contained_path(root, candidates[0], must_exist=False)
            derived_resolved = contained_path(root, derived, must_exist=False)
            if explicit_resolved != derived_resolved:
                raise ValueError("failure receipt reference does not match the Harbor trial")
        candidates.append(derived)
    if not candidates:
        if not required:
            return None
        raise ValueError("errored train trajectory lacks a Harbor failure receipt reference")
    candidate = candidates[0]
    resolved = contained_path(root, candidate, must_exist=False)
    if not resolved.exists():
        if explicit_declared or (required and not allow_missing_derived):
            raise ValueError("Harbor failure receipt is missing")
        return None
    resolved = contained_path(root, candidate, must_exist=True)
    if resolved.name != FAILURE_RECEIPT_FILENAME or not resolved.is_file() or resolved.is_symlink():
        raise ValueError("Harbor failure receipt is not a regular contract file")
    return resolved


def _unattested_harbor_result_digest(
    trajectory: Any,
    root: Path,
    *,
    allowed_agent_evidence: set[Path] | None = None,
) -> tuple[str, Path, str]:
    """Bind an unattested Harbor errored result without treating it as evidence."""

    refs = getattr(trajectory, "refs", None)
    if not isinstance(refs, dict):
        raise ValueError("trajectory.refs must be an object")
    if "failure_receipt_path" in refs:
        # A declared receipt may never be downgraded to unattested evidence.
        raise ValueError("declared Harbor failure receipt is missing")
    raw_result = refs.get("result_path")
    if not isinstance(raw_result, str) or not raw_result:
        raise ValueError("unattested Harbor errored evidence lacks a result reference")
    result_path = contained_path(root, _root_relative_path(root, raw_result), must_exist=True)
    if not result_path.is_file() or result_path.is_symlink() or result_path.name != "result.json":
        raise ValueError("unattested Harbor errored result is not a regular result file")
    result = _read_scanned_json_artifact(
        result_path,
        root=root,
        max_bytes=MAX_HARBOR_JSON_BYTES,
        label="Harbor child result",
    )
    if not isinstance(result, dict):
        raise ValueError("unattested Harbor errored result is not an object")
    exception_info = result.get("exception_info")
    expected_exception_keys = {
        "exception_type",
        "exception_message",
        "exception_traceback",
        "occurred_at",
    }
    if not isinstance(exception_info, dict) or set(exception_info) != expected_exception_keys:
        raise ValueError("unattested Harbor errored result has invalid ExceptionInfo")
    for key in expected_exception_keys:
        if not isinstance(exception_info.get(key), str):
            raise ValueError("unattested Harbor errored result has invalid ExceptionInfo")
    if not exception_info["exception_type"] or not exception_info["occurred_at"]:
        raise ValueError("unattested Harbor errored result lacks exception identity")
    try:
        datetime.fromisoformat(exception_info["occurred_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("unattested Harbor errored result has invalid exception time") from exc
    try:
        trial_id = str(UUID(str(result.get("id"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("unattested Harbor errored result has invalid trial id") from exc
    if trial_id != result.get("id"):
        raise ValueError("unattested Harbor errored result has non-canonical trial id")
    trial_name = result.get("trial_name")
    task_name = result.get("task_name")
    trajectory_task_id = getattr(trajectory, "task_id", None)
    if not isinstance(trial_name, str) or trial_name != result_path.parent.name:
        raise ValueError("unattested Harbor errored result has invalid trial identity")
    if (
        not isinstance(trajectory_task_id, str)
        or not isinstance(task_name, str)
        or task_name not in {trajectory_task_id, trajectory_task_id.rsplit("/", 1)[-1]}
    ):
        raise ValueError("unattested Harbor errored result has invalid task identity")
    serialized_task_id = result.get("task_id")
    if (
        not isinstance(serialized_task_id, dict)
        or set(serialized_task_id) != {"path"}
        or not isinstance(serialized_task_id.get("path"), str)
        or Path(serialized_task_id["path"]).name != task_name
    ):
        raise ValueError("unattested Harbor errored result has invalid task binding")
    config = result.get("config")
    if (
        not isinstance(config, dict)
        or config.get("trial_name") != trial_name
        or not isinstance(config.get("task"), dict)
        or config["task"].get("path") != serialized_task_id["path"]
    ):
        raise ValueError("unattested Harbor errored result has invalid config binding")
    allowed = set() if allowed_agent_evidence is None else {
        contained_path(root, path, must_exist=True) for path in allowed_agent_evidence
    }
    try:
        _validate_agent_evidence_inventory(result_path, root, allowed)
    except ValueError as exc:
        raise ValueError(
            "unattested Harbor errored result conflicts with partial agent evidence"
        ) from exc
    return (
        sha256_file(result_path, max_bytes=MAX_HARBOR_JSON_BYTES),
        result_path,
        trial_id,
    )


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _scan_credential_bytes(raw: bytes, label: str) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8 JSON") from exc
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise ValueError(f"credential-like material detected in {label}")


def _read_scanned_json_artifact(
    path: Path,
    *,
    root: Path,
    max_bytes: int,
    label: str,
) -> Any:
    try:
        controlled = contained_path(root, path, must_exist=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} is missing or invalid") from exc
    if not controlled.is_file() or _is_linklike(path):
        raise ValueError(f"{label} is not a regular file")
    with controlled.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    _scan_credential_bytes(raw, label)
    return strict_json_loads(raw, max_bytes=max_bytes)


def _strict_nonnegative_int(value: Any, label: str, *, maximum: int = 10_000_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{label} must be a bounded non-negative integer")
    return value


def _validate_agent_evidence_inventory(
    result_path: Path,
    root: Path,
    allowed_files: set[Path],
) -> None:
    trial_dir = result_path.parent
    agent_dir = trial_dir / "agent"
    if _is_linklike(agent_dir) or not agent_dir.exists() or not agent_dir.is_dir():
        raise ValueError("Harbor agent evidence directory is missing or invalid")
    actual: set[Path] = set()
    for candidate in agent_dir.iterdir():
        if _is_linklike(candidate) or not candidate.is_file():
            raise ValueError("Harbor agent evidence directory contains an unexpected entry")
        if candidate.name in {"trajectory.json", "atif.json"}:
            max_bytes = MAX_ATIF_BYTES
        elif candidate.name in {ATTESTATION_FILENAME, FAILURE_RECEIPT_FILENAME}:
            max_bytes = MAX_FAILURE_RECEIPT_BYTES
        else:
            raise ValueError("Harbor agent evidence directory contains an unexpected file")
        if candidate.stat().st_size > max_bytes:
            raise ValueError("Harbor agent evidence file exceeds its size boundary")
        actual.add(contained_path(root, candidate, must_exist=True))
    allowed = {contained_path(root, path, must_exist=True) for path in allowed_files}
    if actual != allowed:
        raise ValueError("Harbor agent evidence inventory is inconsistent")


def _read_failure_receipt(
    path: Path,
    *,
    root: Path,
    expected_snapshot_sha256: str,
    expected_component_sha256: dict[str, str],
    expected_route_contract_sha256: str,
    expected_seed: int,
    expected_atif_present: bool,
) -> dict[str, Any]:
    controlled = contained_path(root, path, must_exist=True)
    with controlled.open("rb") as handle:
        raw = handle.read(MAX_FAILURE_RECEIPT_BYTES + 1)
    _scan_credential_bytes(raw, "Harbor failure receipt")
    receipt = strict_json_loads(raw, max_bytes=MAX_FAILURE_RECEIPT_BYTES)
    if not isinstance(receipt, dict) or set(receipt) != FAILURE_RECEIPT_KEYS:
        raise ValueError("Harbor failure receipt has an invalid shape")
    if receipt.get("schema_version") != FAILURE_RECEIPT_SCHEMA:
        raise ValueError("Harbor failure receipt schema drifted")
    unsigned = dict(receipt)
    claimed_hash = unsigned.pop("receipt_sha256", None)
    if not isinstance(claimed_hash, str) or not HEX64.fullmatch(claimed_hash) or sha256_json(unsigned) != claimed_hash:
        raise ValueError("Harbor failure receipt hash is invalid")
    for key in ("failure_class", "failure_stage", "mimocode_exit_class"):
        if not isinstance(receipt.get(key), str) or not SAFE_FAILURE_CODE.fullmatch(receipt[key]):
            raise ValueError(f"Harbor failure receipt {key} is invalid")
    failure_class = receipt["failure_class"]
    failure_stage = receipt["failure_stage"]
    exit_class = receipt["mimocode_exit_class"]
    if failure_class not in FAILURE_RECEIPT_CLASSES:
        raise ValueError("Harbor failure receipt class is invalid")
    if failure_stage not in FAILURE_RECEIPT_STAGES or exit_class not in MIMOCODE_EXIT_CLASSES:
        raise ValueError("Harbor failure receipt classification is invalid")
    expected_pair = {
        "mimocode_process_failed": ("mimocode", False),
        "runtime_sanitization_failed": ("sanitize", True),
        "mimocode_and_sanitization_failed": ("sanitize", False),
    }[failure_class]
    if failure_stage != expected_pair[0] or (exit_class == "success") is not expected_pair[1]:
        raise ValueError("Harbor failure receipt classification is inconsistent")
    if receipt.get("snapshot_sha256") != expected_snapshot_sha256:
        raise ValueError("Harbor failure receipt snapshot drifted")
    if receipt.get("component_sha256") != expected_component_sha256:
        raise ValueError("Harbor failure receipt component hashes drifted")
    if receipt.get("route_contract_sha256") != expected_route_contract_sha256:
        raise ValueError("Harbor failure receipt route contract drifted")
    if receipt.get("seed") != expected_seed:
        raise ValueError("Harbor failure receipt seed drifted")
    expected_model = {"api_id": UPDATE_MODEL_ID, "harbor_id": HARBOR_MODEL_ID}
    if receipt.get("model") != expected_model:
        raise ValueError("Harbor failure receipt model route drifted")
    expected_runtime = {"name": "mimocode", "version": MIMOCODE_VERSION}
    if receipt.get("runtime") != expected_runtime:
        raise ValueError("Harbor failure receipt runtime identity drifted")
    if receipt.get("atif_present") is not expected_atif_present:
        raise ValueError("Harbor failure receipt ATIF state drifted")
    for key in (
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_content_persisted",
    ):
        if receipt.get(key) is not False:
            raise ValueError(f"Harbor failure receipt violates boundary: {key}")
    return receipt


def _root_relative_path(root: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else root / candidate


def _read_atif_structure(
    path: Path,
    *,
    expected_snapshot_sha256: str,
    expected_component_sha256: dict[str, str],
    expected_route_contract_sha256: str,
    expected_seed: int,
) -> dict[str, Any]:
    with path.open("rb") as handle:
        raw = handle.read(MAX_ATIF_BYTES + 1)
    _scan_credential_bytes(raw, "Harbor ATIF")
    data = strict_json_loads(raw, max_bytes=MAX_ATIF_BYTES)
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "agent",
        "steps",
        "final_metrics",
        "extra",
    }:
        raise ValueError("ATIF root shape drifted")
    if data.get("schema_version") != "ATIF-v1.7":
        raise ValueError("unsupported ATIF schema version")
    _validate_atif_identity(
        data,
        expected_snapshot_sha256=expected_snapshot_sha256,
        expected_component_sha256=expected_component_sha256,
        expected_route_contract_sha256=expected_route_contract_sha256,
        expected_seed=expected_seed,
    )
    steps = data["steps"]
    if not isinstance(steps, list):
        raise ValueError("ATIF root must contain a steps array")
    if not 1 <= len(steps) <= 10_000:
        raise ValueError("ATIF steps exceed structural bounds")
    categories: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    aggregate: dict[str, int | float] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
    }
    seen_metrics: set[str] = set()
    saw_reasoning = False
    for expected_step_id, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or step.get("step_id") != expected_step_id:
            raise ValueError("ATIF steps must be ordered objects")
        if expected_step_id == 1:
            if step != {
                "step_id": 1,
                "source": "system",
                "message": "",
                "extra": {"status": "sanitized"},
            }:
                raise ValueError("ATIF privacy-boundary system step is invalid")
            continue
        allowed_step_keys = {
            "step_id",
            "source",
            "message",
            "model_name",
            "timestamp",
            "metrics",
            "llm_call_count",
            "tool_calls",
            "observation",
            "extra",
        }
        if (
            set(step) - allowed_step_keys
            or step.get("source") != "agent"
            or step.get("message") != ""
            or step.get("model_name") != HARBOR_MODEL_ID
        ):
            raise ValueError("ATIF sanitized agent step is invalid")
        if "timestamp" in step:
            timestamp = step["timestamp"]
            if not isinstance(timestamp, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z",
                timestamp,
            ):
                raise ValueError("ATIF sanitized timestamp is invalid")
        metrics = step.get("metrics")
        if metrics is not None:
            allowed_metric_keys = {
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
                "cost_usd",
                "extra",
            }
            if not isinstance(metrics, dict) or not metrics or set(metrics) - allowed_metric_keys:
                raise ValueError("ATIF sanitized metrics are invalid")
            for key, value in metrics.items():
                if key == "extra":
                    if not isinstance(value, dict) or set(value) != {"reasoning_tokens"}:
                        raise ValueError("ATIF reasoning telemetry is invalid")
                    aggregate["reasoning_tokens"] += _safe_int(
                        value["reasoning_tokens"],
                        "ATIF reasoning tokens",
                    )
                    saw_reasoning = True
                elif key == "cost_usd":
                    aggregate[key] += _finite_number(
                        value,
                        "ATIF cost",
                        minimum=0,
                        maximum=1_000_000_000,
                    )
                    seen_metrics.add(key)
                else:
                    aggregate[key] += _safe_int(value, f"ATIF {key}")
                    seen_metrics.add(key)
            if metrics.get("cached_tokens", 0) > metrics.get("prompt_tokens", 0):
                raise ValueError("ATIF cached tokens exceed prompt tokens")
            if step.get("llm_call_count") != 1:
                raise ValueError("ATIF metric step must represent exactly one model call")
        elif "llm_call_count" in step:
            raise ValueError("ATIF llm_call_count requires metrics")
        tool_calls = step.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list) or len(tool_calls) != 1:
                raise ValueError("ATIF tool step must contain one sanitized call")
            call = tool_calls[0]
            if (
                not isinstance(call, dict)
                or set(call) != {"tool_call_id", "function_name", "arguments"}
                or not isinstance(call.get("tool_call_id"), str)
                or not re.fullmatch(r"tool-\d{6}", call["tool_call_id"])
                or call.get("function_name") not in SAFE_TOOL_NAMES
                or call.get("arguments") != {}
            ):
                raise ValueError("ATIF sanitized tool call is invalid")
            observation = step.get("observation")
            results = (
                observation.get("results")
                if isinstance(observation, dict) and set(observation) == {"results"}
                else None
            )
            if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
                raise ValueError("ATIF sanitized observation is invalid")
            observed = results[0]
            if (
                set(observed) != {"source_call_id", "content"}
                or observed.get("source_call_id") != call["tool_call_id"]
                or observed.get("content") not in SAFE_OBSERVATIONS
            ):
                raise ValueError("ATIF sanitized observation result is invalid")
            status = observed["content"].split(":", 1)[1]
            if step.get("extra") != {"status": status}:
                raise ValueError("ATIF tool status metadata is inconsistent")
            categories[_tool_category(call["function_name"])] += 1
            statuses[status] += 1
        elif any(key in step for key in ("observation", "extra")):
            raise ValueError("ATIF observation metadata requires a tool call")
        if metrics is None and tool_calls is None:
            raise ValueError("ATIF agent step lacks structural evidence")
    final_metrics = data.get("final_metrics")
    expected_metric_keys = {"total_steps"} | {
        ("total_cost_usd" if name == "cost_usd" else f"total_{name}")
        for name in seen_metrics
    }
    if saw_reasoning:
        expected_metric_keys.add("extra")
    if (
        not isinstance(final_metrics, dict)
        or set(final_metrics) != expected_metric_keys
        or final_metrics.get("total_steps") != len(steps)
    ):
        raise ValueError("ATIF final metrics are invalid")
    usage = {
        "prompt_tokens": _safe_int(final_metrics.get("total_prompt_tokens", 0), "ATIF prompt tokens"),
        "completion_tokens": _safe_int(
            final_metrics.get("total_completion_tokens", 0),
            "ATIF completion tokens",
        ),
        "cached_tokens": _safe_int(final_metrics.get("total_cached_tokens", 0), "ATIF cached tokens"),
        "reasoning_tokens": 0,
        "cost_usd": _finite_number(
            final_metrics.get("total_cost_usd", 0.0),
            "ATIF total cost",
            minimum=0,
            maximum=1_000_000_000,
        ),
    }
    if saw_reasoning:
        extra = final_metrics.get("extra")
        if not isinstance(extra, dict) or set(extra) != {"total_reasoning_tokens"}:
            raise ValueError("ATIF final reasoning telemetry is invalid")
        usage["reasoning_tokens"] = _safe_int(
            extra["total_reasoning_tokens"],
            "ATIF final reasoning tokens",
        )
    expected_usage = {
        "prompt_tokens": int(aggregate["prompt_tokens"]),
        "completion_tokens": int(aggregate["completion_tokens"]),
        "cached_tokens": int(aggregate["cached_tokens"]),
        "reasoning_tokens": int(aggregate["reasoning_tokens"]),
        "cost_usd": float(aggregate["cost_usd"]),
    }
    if usage != expected_usage:
        raise ValueError("ATIF final usage differs from its sanitized steps")
    return {
        "steps": len(steps),
        "tool_categories": categories,
        "tool_statuses": statuses,
        "usage": usage,
    }


def _validate_atif_identity(
    data: dict[str, Any],
    *,
    expected_snapshot_sha256: str,
    expected_component_sha256: dict[str, str],
    expected_route_contract_sha256: str,
    expected_seed: int,
) -> None:
    identity_keys = {
        "api_model_id",
        "seed",
        "snapshot_hash",
        "component_hashes",
        "runtime_identity",
        "route_contract_sha256",
    }
    agent = data.get("agent")
    if not isinstance(agent, dict) or set(agent) != {"name", "version", "model_name", "extra"}:
        raise ValueError("ATIF agent identity shape drifted")
    if (
        agent.get("name") != "seagym-evoagent-mimocode"
        or agent.get("version") != "0.1.0"
        or agent.get("model_name") != HARBOR_MODEL_ID
    ):
        raise ValueError("ATIF agent identity drifted")
    extra = data.get("extra")
    if not isinstance(extra, dict) or set(extra) != identity_keys:
        raise ValueError("ATIF evidence identity shape drifted")
    if agent.get("extra") != extra:
        raise ValueError("ATIF mirrored evidence identity drifted")
    if extra.get("snapshot_hash") != expected_snapshot_sha256:
        raise ValueError("ATIF snapshot identity drifted")
    if extra.get("component_hashes") != expected_component_sha256:
        raise ValueError("ATIF component identity drifted")
    if extra.get("route_contract_sha256") != expected_route_contract_sha256:
        raise ValueError("ATIF route identity drifted")
    seed = extra.get("seed")
    if isinstance(seed, bool) or seed != expected_seed:
        raise ValueError("ATIF seed identity drifted")
    if extra.get("api_model_id") != UPDATE_MODEL_ID:
        raise ValueError("ATIF model identity drifted")
    if extra.get("runtime_identity") != {"name": "mimocode", "version": MIMOCODE_VERSION}:
        raise ValueError("ATIF runtime identity drifted")


def _validate_atif_attestation(
    atif_path: Path,
    *,
    atif_sha256: str,
    usage: dict[str, Any],
    result: dict[str, Any],
    root: Path,
    expected_snapshot_sha256: str,
    expected_component_sha256: dict[str, str],
    expected_route_contract_sha256: str,
    expected_seed: int,
    failure_receipt_sha256: str | None,
) -> tuple[Path, str]:
    try:
        attestation_path = contained_path(
            root,
            atif_path.parent / ATTESTATION_FILENAME,
            must_exist=True,
        )
    except (OSError, ValueError) as exc:
        raise ValueError("Harbor ATIF attestation is missing or invalid") from exc
    if not attestation_path.is_file() or attestation_path.is_symlink():
        raise ValueError("Harbor ATIF attestation is not a regular file")
    with attestation_path.open("rb") as handle:
        raw = handle.read(MAX_FAILURE_RECEIPT_BYTES + 1)
    _scan_credential_bytes(raw, "Harbor ATIF attestation")
    attestation = strict_json_loads(raw, max_bytes=MAX_FAILURE_RECEIPT_BYTES)
    expected_keys = {
        "schema_version",
        "snapshot_sha256",
        "component_sha256",
        "atif_sha256",
        "route_contract_sha256",
        "model",
        "seed",
        "runtime",
        "usage",
        "runtime_failure_receipt_sha256",
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_persisted",
        "causal_attribution_claimed",
        "promotion_claimed",
        "activation_claimed",
        "attestation_sha256",
    }
    if not isinstance(attestation, dict) or set(attestation) != expected_keys:
        raise ValueError("Harbor ATIF attestation shape is invalid")
    unsigned = dict(attestation)
    attestation_sha256 = unsigned.pop("attestation_sha256", None)
    if (
        not isinstance(attestation_sha256, str)
        or not HEX64.fullmatch(attestation_sha256)
        or sha256_json(unsigned) != attestation_sha256
    ):
        raise ValueError("Harbor ATIF attestation self-hash is invalid")
    expected_model = {
        "api_id": UPDATE_MODEL_ID,
        "harbor_id": HARBOR_MODEL_ID,
        "openrouter_provider": "xiaomi/fp8",
        "fallbacks_allowed": False,
        "reasoning_enabled": False,
        "credential_transport": "local_guard_proxy_v1",
    }
    expected_runtime = {
        "adapter_version": ADAPTER_VERSION,
        "mimocode_version": MIMOCODE_VERSION,
        "mimocode_archive_sha256": MIMOCODE_ARCHIVE_SHA256,
        "seagym_commit": SEAGYM_COMMIT,
        "harbor_commit": HARBOR_RUNTIME_COMMIT,
    }
    if (
        attestation.get("schema_version") != ATTESTATION_SCHEMA
        or attestation.get("snapshot_sha256") != expected_snapshot_sha256
        or attestation.get("component_sha256") != expected_component_sha256
        or attestation.get("atif_sha256") != atif_sha256
        or attestation.get("route_contract_sha256") != expected_route_contract_sha256
        or attestation.get("model") != expected_model
        or attestation.get("seed") != expected_seed
        or attestation.get("runtime") != expected_runtime
        or attestation.get("usage") != usage
        or attestation.get("runtime_failure_receipt_sha256") != failure_receipt_sha256
    ):
        raise ValueError("Harbor ATIF attestation identity or usage drifted")
    for key in (
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_persisted",
        "causal_attribution_claimed",
        "promotion_claimed",
        "activation_claimed",
    ):
        if attestation.get(key) is not False:
            raise ValueError(f"Harbor ATIF attestation violates boundary: {key}")
    agent_result = result.get("agent_result")
    expected_agent_keys = {
        "n_input_tokens",
        "n_cache_tokens",
        "n_output_tokens",
        "cost_usd",
        "rollout_details",
        "metadata",
    }
    if not isinstance(agent_result, dict) or set(agent_result) != expected_agent_keys:
        raise ValueError("Harbor ATIF result context shape is invalid")
    expected_metadata = {
        "attestation_sha256": attestation_sha256,
        "atif_sha256": atif_sha256,
        "snapshot_sha256": expected_snapshot_sha256,
        "model_id": UPDATE_MODEL_ID,
        "seed": expected_seed,
        "route_contract_sha256": expected_route_contract_sha256,
        "privacy_projection": True,
    }
    if failure_receipt_sha256 is not None:
        expected_metadata["runtime_failure_receipt_sha256"] = failure_receipt_sha256
    if (
        agent_result.get("n_input_tokens") != usage["prompt_tokens"]
        or agent_result.get("n_cache_tokens") != usage["cached_tokens"]
        or agent_result.get("n_output_tokens") != usage["completion_tokens"]
        or agent_result.get("cost_usd") != usage["cost_usd"]
        or agent_result.get("rollout_details") is not None
        or agent_result.get("metadata") != expected_metadata
    ):
        raise ValueError("Harbor ATIF result context differs from its attestation")
    return attestation_path, attestation_sha256


def _validate_failure_result_context(
    result: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    agent_result = result.get("agent_result")
    expected_metadata = {
        "runtime_failure_receipt_sha256": receipt["receipt_sha256"],
        "runtime_failure_class": receipt["failure_class"],
        "runtime_failure_stage": receipt["failure_stage"],
        "mimocode_exit_class": receipt["mimocode_exit_class"],
        "snapshot_sha256": receipt["snapshot_sha256"],
        "model_id": receipt["model"]["api_id"],
        "seed": receipt["seed"],
        "route_contract_sha256": receipt["route_contract_sha256"],
        "privacy_projection": True,
    }
    if agent_result != {
        "n_input_tokens": 0,
        "n_cache_tokens": 0,
        "n_output_tokens": 0,
        "cost_usd": 0.0,
        "rollout_details": None,
        "metadata": expected_metadata,
    }:
        raise ValueError("Harbor failure result context differs from its receipt")


def _tool_category(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 128:
        return "other"
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    for token, category in TOOL_CATEGORIES.items():
        if normalized == token or normalized.endswith(f"_{token}"):
            return category
    return "other"


def _safe_status(extra: Any) -> str:
    if not isinstance(extra, dict):
        return "unknown"
    value = extra.get("status")
    if not isinstance(value, str):
        return "unknown"
    return SAFE_STATUS_ALIASES.get(value.strip().casefold(), "unknown")


def _finite_number(value: Any, label: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{label} is outside the permitted finite range")
    return number


def _safe_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 10_000_000_000 or not number.is_integer():
        raise ValueError(f"{label} must be a bounded non-negative integer")
    return int(number)


def _numeric_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    items = list(values)
    if not items:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(items),
        "min": min(items),
        "max": max(items),
        "mean": round(sum(items) / len(items), 12),
    }
