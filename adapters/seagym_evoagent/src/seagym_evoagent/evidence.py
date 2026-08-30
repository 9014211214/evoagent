"""Privacy projection from train-only SEAGym/Harbor results."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Iterable

from .canonical import contained_path, sha256_file, sha256_json, strict_json_loads
from .mimocode import MIMOCODE_VERSION
from .models import HARBOR_MODEL_ID, UPDATE_MODEL_ID


MAX_ATIF_BYTES = 32 * 1024 * 1024
MAX_FAILURE_RECEIPT_BYTES = 64 * 1024
FAILURE_RECEIPT_FILENAME = "evoagent-runtime-failure.json"
FAILURE_RECEIPT_SCHEMA = "evoagent-runtime-failure-v1"
NO_USABLE_ATIF_SKIP_CODE = "no_usable_harbor_atif_evidence"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
FAILURE_RECEIPT_CLASSES = {
    "mimocode_process_failed",
    "runtime_sanitization_failed",
    "mimocode_and_sanitization_failed",
}
FAILURE_RECEIPT_STAGES = {"mimocode", "sanitize"}
MIMOCODE_EXIT_CLASSES = {"nonzero", "signal", "timeout", "spawn_failed", "success", "unknown"}
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
    if not isinstance(task_ids, list) or not all(isinstance(item, str) for item in task_ids):
        raise ValueError("train batch task_ids must be a list of strings")
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
    failure_classes: Counter[str] = Counter()
    failure_stages: Counter[str] = Counter()
    mimocode_exit_classes: Counter[str] = Counter()
    tool_categories: Counter[str] = Counter()
    tool_statuses: Counter[str] = Counter()
    atif_steps = 0
    fragments: set[str] = {item for item in task_ids if item}

    for trajectory in trajectories:
        _require_train_trajectory(trajectory)
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
            receipt = _read_failure_receipt(
                _resolve_failure_receipt_path(trajectory, root, required=True),
                root=root,
                expected_snapshot_sha256=expected_snapshot_sha256,
                expected_component_sha256=expected_component_sha256,
                expected_route_contract_sha256=expected_route_contract_sha256,
                expected_seed=expected_seed,
                expected_atif_present=False,
            )
            missing_error_atif += 1
            failure_receipt_digests.append(receipt["receipt_sha256"])
            failure_classes[receipt["failure_class"]] += 1
            failure_stages[receipt["failure_stage"]] += 1
            mimocode_exit_classes[receipt["mimocode_exit_class"]] += 1
            continue
        digest = sha256_file(atif_path, max_bytes=MAX_ATIF_BYTES)
        atif_digests.append(digest)
        structural = _read_atif_structure(atif_path)
        atif_steps += structural["steps"]
        tool_categories.update(structural["tool_categories"])
        tool_statuses.update(structural["tool_statuses"])
        receipt_path = _resolve_failure_receipt_path(trajectory, root, required=False)
        if receipt_path is not None:
            trajectory_error = getattr(trajectory, "error", None)
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

    count = len(trajectories)
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


def _resolve_atif_path(trajectory: Any, root: Path) -> Path | None:
    refs = getattr(trajectory, "refs", None)
    if not isinstance(refs, dict):
        raise ValueError("trajectory.refs must be an object")
    candidates: list[Path] = []
    for key in ("atif_path", "trajectory_path"):
        if key not in refs:
            continue
        explicit = refs[key]
        if not isinstance(explicit, str) or not explicit:
            raise ValueError("trajectory ATIF reference must be non-empty text")
        candidates.append(_root_relative_path(root, explicit))
    result_path = refs.get("result_path")
    if result_path is not None:
        if not isinstance(result_path, str) or not result_path:
            raise ValueError("trajectory result_path must be non-empty text")
        trial_dir = _root_relative_path(root, result_path).parent
        candidates.extend((trial_dir / "agent" / "trajectory.json", trial_dir / "agent" / "atif.json"))
    for candidate in candidates:
        resolved = contained_path(root, candidate, must_exist=False)
        if not resolved.exists():
            continue
        resolved = contained_path(root, candidate, must_exist=True)
        if resolved.is_file() and not resolved.is_symlink():
            return resolved
        raise ValueError("train trajectory ATIF reference is not a regular file")
    error = getattr(trajectory, "error", None)
    if getattr(trajectory, "success", None) is False and isinstance(error, str) and error:
        # SEAGym creates zero-score results for errored/cancelled Harbor trials
        # that never produced result.json or an agent directory. Preserve the
        # observable failure count, but never invent an ATIF document or relax
        # containment for a declared reference.
        return None
    raise ValueError("train trajectory does not reference a contained Harbor ATIF file")


def _resolve_failure_receipt_path(trajectory: Any, root: Path, *, required: bool) -> Path | None:
    refs = getattr(trajectory, "refs", None)
    if not isinstance(refs, dict):
        raise ValueError("trajectory.refs must be an object")
    candidates: list[Path] = []
    explicit = refs.get("failure_receipt_path")
    if explicit is not None:
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
        if explicit is not None or required:
            raise ValueError("Harbor failure receipt is missing")
        return None
    resolved = contained_path(root, candidate, must_exist=True)
    if resolved.name != FAILURE_RECEIPT_FILENAME or not resolved.is_file() or resolved.is_symlink():
        raise ValueError("Harbor failure receipt is not a regular contract file")
    return resolved


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


def _read_atif_structure(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        raw = handle.read(MAX_ATIF_BYTES + 1)
    data = strict_json_loads(raw, max_bytes=MAX_ATIF_BYTES)
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise ValueError("ATIF root must contain a steps array")
    if data.get("schema_version") not in {"ATIF-v1.6", "ATIF-v1.7"}:
        raise ValueError("unsupported ATIF schema version")
    steps = data["steps"]
    if not 1 <= len(steps) <= 10_000:
        raise ValueError("ATIF steps exceed structural bounds")
    categories: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("ATIF steps must be objects")
        tool_calls = step.get("tool_calls")
        if tool_calls is None:
            continue
        if not isinstance(tool_calls, list) or len(tool_calls) > 256:
            raise ValueError("ATIF tool_calls exceed structural bounds")
        status = _safe_status(step.get("extra"))
        for call in tool_calls:
            if not isinstance(call, dict):
                raise ValueError("ATIF tool calls must be objects")
            categories[_tool_category(call.get("function_name"))] += 1
            statuses[status] += 1
    return {"steps": len(steps), "tool_categories": categories, "tool_statuses": statuses}


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
