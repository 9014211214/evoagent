"""Privacy projection from train-only SEAGym/Harbor results."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable

from .canonical import contained_path, sha256_file, sha256_json, strict_json_loads


MAX_ATIF_BYTES = 32 * 1024 * 1024
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


def project_train_batch(
    batch: Any,
    *,
    atif_root: Path,
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
            missing_error_atif += 1
            continue
        digest = sha256_file(atif_path, max_bytes=MAX_ATIF_BYTES)
        atif_digests.append(digest)
        structural = _read_atif_structure(atif_path)
        atif_steps += structural["steps"]
        tool_categories.update(structural["tool_categories"])
        tool_statuses.update(structural["tool_statuses"])

    count = len(trajectories)
    if not atif_digests:
        raise ValueError("train batch contains no usable Harbor ATIF evidence")
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
    }
    return EvidenceProjection(
        summary=summary,
        evidence_sha256=sha256_json(summary),
        forbidden_fragments=tuple(sorted(fragments)),
    )


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
