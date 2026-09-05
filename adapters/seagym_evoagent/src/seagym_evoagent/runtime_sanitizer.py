"""Convert MiMoCode JSONL into a deliberately lossy ATIF-v1.7 record.

This module is a privacy boundary, not a general MiMoCode log converter.  It
never copies messages, reasoning, identifiers, or tool payloads from the input.
Only small, typed operational facts cross the boundary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, Iterable


ATIF_VERSION = "ATIF-v1.7"
MODEL_NAME = "openrouter/xiaomi/mimo-v2.5"
API_MODEL_ID = "xiaomi/mimo-v2.5"
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 10_000
MAX_DEPTH = 32
MAX_STRING_CHARS = 16 * 1024 * 1024
MAX_METADATA_CHARS = 65_536
MAX_TOKENS = 10**12
MAX_COST_USD = 10**9
FAILURE_RECEIPT_SCHEMA = "evoagent-runtime-failure-v1"
FAILURE_RECEIPT_FILENAME = "evoagent-runtime-failure.json"

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
_COMPONENT_NAMES = frozenset(
    {
        "skills",
        "memory",
        "router",
        "policy",
    }
)
_RUNTIME_NAMES = frozenset({"mimocode", "seagym-evoagent"})
_FAILURE_CLASSES = frozenset(
    {
        "mimocode_process_failed",
        "runtime_sanitization_failed",
        "mimocode_and_sanitization_failed",
    }
)
_FAILURE_STAGES = frozenset({"mimocode", "sanitize"})
_MIMOCODE_EXIT_CLASSES = frozenset(
    {"nonzero", "signal", "timeout", "spawn_failed", "success", "unknown"}
)
_TOOL_ALIASES = {
    "apply_patch": "apply_patch",
    "bash": "bash",
    "browser": "browser",
    "edit": "edit",
    "edit_file": "edit_file",
    "exec": "exec",
    "exec_command": "exec_command",
    "execute": "execute",
    "execute_command": "execute_command",
    "glob": "glob",
    "grep": "grep",
    "python": "python",
    "read": "read",
    "read_file": "read_file",
    "search": "search",
    "shell": "shell",
    "websearch": "web_search",
    "web_search": "web_search",
    "webfetch": "webfetch",
    "codesearch": "codesearch",
    "actor": "actor",
    "skill": "skill",
    "write": "write",
    "write_file": "write_file",
}
_STATUS_ALIASES = {
    "pending": "pending",
    "running": "running",
    "ok": "success",
    "success": "success",
    "succeeded": "success",
    "complete": "success",
    "completed": "success",
    "error": "error",
    "failed": "error",
    "failure": "error",
    "timeout": "timeout",
    "timed_out": "timeout",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
_METRIC_ALIASES = {
    "prompt_tokens": "prompt_tokens",
    "input_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "output_tokens": "completion_tokens",
    "cached_tokens": "cached_tokens",
    "cache_read_input_tokens": "cached_tokens",
    "cost_usd": "cost_usd",
    "cost": "cost_usd",
}


class SanitizationError(ValueError):
    """Raised when untrusted runtime data is not safely parseable."""


def _reject_constant(value: str) -> None:
    raise SanitizationError(f"non-finite JSON number is forbidden: {value}")


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SanitizationError("duplicate JSON object key")
        result[key] = value
    return result


def _validate_json_shape(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise SanitizationError("JSON nesting limit exceeded")
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise SanitizationError("JSON string limit exceeded")
        return
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise SanitizationError("non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_shape(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SanitizationError("JSON object keys must be strings")
            _validate_json_shape(key, depth + 1)
            _validate_json_shape(item, depth + 1)
        return
    raise SanitizationError("unsupported JSON value")


def _reject_reasoning_payload(
    value: Any,
    *,
    _path: tuple[str, ...] = (),
    _root_event_type: str | None = None,
) -> None:
    """Reject reasoning content while allowing only numeric token telemetry.

    MiMoCode reports an observable token count at the exact path
    ``step_finish.part.tokens.reasoning``.  That bounded integer is usage
    telemetry, not reasoning content.  No other non-empty reasoning-shaped
    value is admitted.
    """

    if isinstance(value, dict):
        root_event_type = _root_event_type
        if not _path:
            candidate = value.get("type")
            root_event_type = candidate.strip().lower() if isinstance(candidate, str) else None
        event_type = value.get("type")
        if isinstance(event_type, str) and event_type.strip().lower() == "reasoning":
            raise SanitizationError("reasoning event violates the runtime lock")
        for key, item in value.items():
            if key in {"reasoning", "reasoning_content", "reasoning_details"}:
                is_mimocode_token_telemetry = (
                    key == "reasoning"
                    and root_event_type == "step_finish"
                    and _path == ("part", "tokens")
                )
                if is_mimocode_token_telemetry:
                    _metric_number("reasoning_tokens", item)
                    continue
                if not _empty_reasoning(item):
                    raise SanitizationError("reasoning content violates the runtime lock")
            _reject_reasoning_payload(
                item,
                _path=(*_path, key),
                _root_event_type=root_event_type,
            )
    elif isinstance(value, list):
        for item in value:
            _reject_reasoning_payload(
                item,
                _path=_path,
                _root_event_type=_root_event_type,
            )


def _empty_reasoning(value: Any) -> bool:
    if value in (None, "", False, 0):
        return True
    return isinstance(value, (list, dict)) and not value


def _load_metadata(encoded: str) -> dict[str, Any]:
    if not isinstance(encoded, str) or len(encoded) > MAX_METADATA_CHARS:
        raise SanitizationError("snapshot metadata JSON is invalid")
    try:
        value = json.loads(
            encoded,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_no_duplicates,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise SanitizationError("snapshot metadata is not valid JSON") from exc
    _validate_json_shape(value)
    if not isinstance(value, dict):
        raise SanitizationError("snapshot metadata must be an object")
    if set(value) != {"snapshot_hash", "component_hashes", "runtime_identity", "route_contract_sha256"}:
        raise SanitizationError("snapshot metadata has an unexpected shape")

    snapshot_hash = value.get("snapshot_hash")
    if not isinstance(snapshot_hash, str) or not _HASH_RE.fullmatch(snapshot_hash):
        raise SanitizationError("snapshot_hash must be a lowercase SHA-256 hex digest")
    route_contract_sha256 = value.get("route_contract_sha256")
    if not isinstance(route_contract_sha256, str) or not _HASH_RE.fullmatch(route_contract_sha256):
        raise SanitizationError("route_contract_sha256 must be a lowercase SHA-256 hex digest")

    raw_components = value.get("component_hashes")
    if not isinstance(raw_components, dict) or set(raw_components) != _COMPONENT_NAMES:
        raise SanitizationError("component_hashes must contain exactly the four harness components")
    component_hashes: dict[str, str] = {}
    for name, digest in raw_components.items():
        if name not in _COMPONENT_NAMES:
            raise SanitizationError("component hash name is not allowlisted")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise SanitizationError("component hash must be a lowercase SHA-256 digest")
        component_hashes[name] = digest

    raw_runtime = value.get("runtime_identity")
    if not isinstance(raw_runtime, dict):
        raise SanitizationError("runtime_identity must be an object")
    runtime_name = raw_runtime.get("name")
    runtime_version = raw_runtime.get("version")
    if runtime_name not in _RUNTIME_NAMES:
        raise SanitizationError("runtime name is not allowlisted")
    if not isinstance(runtime_version, str) or not _VERSION_RE.fullmatch(runtime_version):
        raise SanitizationError("runtime version must be numeric and bounded")

    return {
        "snapshot_hash": snapshot_hash,
        "component_hashes": dict(sorted(component_hashes.items())),
        "runtime_identity": {"name": runtime_name, "version": runtime_version},
        "route_contract_sha256": route_contract_sha256,
    }


def _parse_timestamp(value: Any) -> str:
    if isinstance(value, bool):
        raise SanitizationError("timestamp has the wrong type")
    if isinstance(value, (int, float)):
        if (isinstance(value, float) and not math.isfinite(value)) or value < 0 or value > 253_402_300_799_000:
            raise SanitizationError("timestamp is out of range")
        seconds = value / 1000 if value > 253_402_300_799 else value
        moment = dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)
    elif isinstance(value, str) and len(value) <= 64:
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            moment = dt.datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise SanitizationError("timestamp must be ISO 8601") from exc
        if moment.tzinfo is None:
            raise SanitizationError("timestamp must include a timezone")
        moment = moment.astimezone(dt.timezone.utc)
    else:
        raise SanitizationError("timestamp has the wrong type")
    rendered = moment.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return rendered.replace(".000000Z", "Z")


def _event_timestamp(event: dict[str, Any]) -> str | None:
    for key in ("timestamp", "created_at", "time"):
        if key in event:
            return _parse_timestamp(event[key])
    return None


def _tool_name(event: dict[str, Any]) -> str | None:
    candidates: list[Any] = []
    for key in ("tool_name", "function_name"):
        if key in event:
            if not isinstance(event[key], str):
                raise SanitizationError(f"{key} has the wrong type")
            candidates.append(event[key])
    for key in ("tool", "function", "tool_call"):
        nested = event.get(key)
        if isinstance(nested, str):
            candidates.append(nested)
        elif isinstance(nested, dict):
            candidates.extend((nested.get("name"), nested.get("function_name")))
            function = nested.get("function")
            if isinstance(function, dict):
                candidates.append(function.get("name"))
    if event.get("type") == "tool_use":
        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "tool" or not isinstance(part.get("tool"), str):
            raise SanitizationError("MiMoCode tool_use event has an invalid part")
        candidates.append(part["tool"])
    for candidate in candidates:
        if isinstance(candidate, str):
            normalized = candidate.strip().lower()
            if normalized in _TOOL_ALIASES:
                return _TOOL_ALIASES[normalized]
    return None


def _event_status(event: dict[str, Any]) -> str:
    values: list[Any] = []
    if "status" in event:
        values.append(event["status"])
    for key in ("tool", "tool_call"):
        nested = event.get(key)
        if isinstance(nested, dict) and "status" in nested:
            values.append(nested["status"])
    if event.get("type") == "tool_use":
        part = event.get("part")
        state = part.get("state") if isinstance(part, dict) else None
        if not isinstance(state, dict) or "status" not in state:
            raise SanitizationError("MiMoCode tool_use event is missing state.status")
        values.append(state["status"])
    for value in values:
        if not isinstance(value, str):
            raise SanitizationError("status has the wrong type")
        normalized = value.strip().lower()
        if normalized not in _STATUS_ALIASES:
            raise SanitizationError("status is not allowlisted")
        return _STATUS_ALIASES[normalized]
    if "success" in event and not isinstance(event["success"], bool):
        raise SanitizationError("success has the wrong type")
    if event.get("success") is True:
        return "success"
    if event.get("success") is False or event.get("error") is not None:
        return "error"
    return "unknown"


def _metric_number(name: str, value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SanitizationError(f"{name} has the wrong type")
    if (isinstance(value, float) and not math.isfinite(value)) or value < 0:
        raise SanitizationError(f"{name} must be finite and non-negative")
    if name == "cost_usd":
        if value > MAX_COST_USD:
            raise SanitizationError("cost exceeds the limit")
        return float(value)
    if not isinstance(value, int) or value > MAX_TOKENS:
        raise SanitizationError(f"{name} must be a bounded integer")
    return value


def _event_metrics(event: dict[str, Any]) -> dict[str, Any]:
    containers: list[dict[str, Any]] = [event]
    reasoning_tokens: int | None = None
    if event.get("type") == "step_finish":
        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            raise SanitizationError("MiMoCode step_finish event has an invalid part")
        tokens = part.get("tokens")
        cache = tokens.get("cache") if isinstance(tokens, dict) else None
        if not isinstance(tokens, dict) or not isinstance(cache, dict):
            raise SanitizationError("MiMoCode step_finish tokens are invalid")
        parsed_reasoning_tokens = _metric_number("reasoning_tokens", tokens.get("reasoning"))
        if not isinstance(parsed_reasoning_tokens, int):
            raise SanitizationError("MiMoCode reasoning token accounting is invalid")
        reasoning_tokens = parsed_reasoning_tokens
        cache_write = _metric_number("cache_write_tokens", cache.get("write"))
        if not isinstance(cache_write, int):
            raise SanitizationError("MiMoCode cache write tokens are invalid")
        non_cached_input = _metric_number("prompt_tokens", tokens.get("input"))
        cache_read = _metric_number("cached_tokens", cache.get("read"))
        if not isinstance(non_cached_input, int) or not isinstance(cache_read, int):
            raise SanitizationError("MiMoCode input token accounting is invalid")
        prompt_with_cache = non_cached_input + cache_read
        if prompt_with_cache > MAX_TOKENS:
            raise SanitizationError("MiMoCode prompt token accounting exceeds the limit")
        containers.append(
            {
                # Harbor AgentContext defines input tokens as including cache.
                # MiMoCode step_finish.input excludes cache read/write, so add
                # cache.read exactly once and retain it separately as well.
                "prompt_tokens": prompt_with_cache,
                "output_tokens": tokens.get("output"),
                "cached_tokens": cache_read,
                "cost_usd": part.get("cost"),
            }
        )
    for key in ("usage", "metrics", "token_usage"):
        if key in event:
            nested = event[key]
            if not isinstance(nested, dict):
                raise SanitizationError(f"{key} must be an object")
            containers.append(nested)
    response = event.get("response")
    if isinstance(response, dict):
        for key in ("usage", "metrics"):
            if key in response:
                nested = response[key]
                if not isinstance(nested, dict):
                    raise SanitizationError(f"response.{key} must be an object")
                containers.append(nested)

    result: dict[str, Any] = {}
    for container in containers:
        for raw_name, safe_name in _METRIC_ALIASES.items():
            if raw_name not in container:
                continue
            parsed = _metric_number(safe_name, container[raw_name])
            previous = result.get(safe_name)
            if previous is not None and previous != parsed:
                raise SanitizationError(f"conflicting values for {safe_name}")
            result[safe_name] = parsed
    # Tool-only events may omit telemetry. A telemetry-bearing event may not
    # omit a field and later acquire a fabricated zero from aggregate defaults.
    if (result or len(containers) > 1) and set(result) != {
        "prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd",
    }:
        raise SanitizationError("model-call usage is incomplete")
    cached = result.get("cached_tokens")
    prompt = result.get("prompt_tokens")
    if cached is not None and prompt is not None and cached > prompt:
        raise SanitizationError("cached_tokens cannot exceed prompt_tokens")
    if reasoning_tokens is not None:
        result["extra"] = {"reasoning_tokens": reasoning_tokens}
    return result


def _read_events(path: Path) -> Iterable[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SanitizationError("input is not readable") from exc
    if not path.is_file() or path.is_symlink():
        raise SanitizationError("input must be a regular, non-symlink file")
    if size > MAX_INPUT_BYTES:
        raise SanitizationError("input exceeds the size limit")
    count = 0
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise SanitizationError("input is not readable") from exc
    with stream:
        for raw_line in stream:
            count += 1
            if count > MAX_EVENTS:
                raise SanitizationError("event count limit exceeded")
            if len(raw_line) > MAX_LINE_BYTES:
                raise SanitizationError("JSONL line exceeds the size limit")
            if not raw_line.strip():
                raise SanitizationError("blank JSONL records are forbidden")
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SanitizationError("input must be UTF-8") from exc
            try:
                event = json.loads(
                    line,
                    parse_constant=_reject_constant,
                    object_pairs_hook=_object_no_duplicates,
                )
            except (json.JSONDecodeError, TypeError) as exc:
                raise SanitizationError("malformed JSONL record") from exc
            _validate_json_shape(event)
            if not isinstance(event, dict):
                raise SanitizationError("each JSONL record must be an object")
            _reject_reasoning_payload(event)
            yield event
    if count == 0:
        raise SanitizationError("input JSONL is empty")


def _build_trajectory(
    events: Iterable[dict[str, Any]], *, seed: int, metadata: dict[str, Any]
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "source": "system",
            "message": "",
            "extra": {"status": "sanitized"},
        }
    ]
    totals: dict[str, int | float] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "reasoning_tokens": 0,
    }
    seen_metrics = set()
    saw_reasoning_telemetry = False
    tool_count = 0
    for event in events:
        timestamp = _event_timestamp(event)
        metrics = _event_metrics(event)
        for name, value in metrics.items():
            if name == "extra":
                reasoning_tokens = value["reasoning_tokens"]
                totals["reasoning_tokens"] += reasoning_tokens
                if totals["reasoning_tokens"] > MAX_TOKENS:
                    raise SanitizationError("aggregate metric exceeds the limit")
                saw_reasoning_telemetry = True
                continue
            totals[name] += value
            if totals[name] > (MAX_COST_USD if name == "cost_usd" else MAX_TOKENS):
                raise SanitizationError("aggregate metric exceeds the limit")
            seen_metrics.add(name)

        tool_name = _tool_name(event)
        if tool_name is None and not metrics:
            continue
        step: dict[str, Any] = {
            "step_id": len(steps) + 1,
            "source": "agent",
            "message": "",
            "model_name": MODEL_NAME,
        }
        if timestamp is not None:
            step["timestamp"] = timestamp
        if metrics:
            step["metrics"] = metrics
            step["llm_call_count"] = 1
        if tool_name is not None:
            tool_count += 1
            call_id = f"tool-{tool_count:06d}"
            status = _event_status(event)
            step["tool_calls"] = [
                {"tool_call_id": call_id, "function_name": tool_name, "arguments": {}}
            ]
            step["observation"] = {
                "results": [{"source_call_id": call_id, "content": f"status:{status}"}]
            }
            step["extra"] = {"status": status}
        steps.append(step)

    if not seen_metrics:
        raise SanitizationError("runtime has no complete usage measurement")
    final_metrics: dict[str, int | float] = {"total_steps": len(steps)}
    for name in sorted(seen_metrics):
        final_name = "total_cost_usd" if name == "cost_usd" else f"total_{name}"
        final_metrics[final_name] = totals[name]
    if saw_reasoning_telemetry:
        final_metrics["extra"] = {
            "total_reasoning_tokens": totals["reasoning_tokens"],
        }
    safe_extra = {
        "api_model_id": API_MODEL_ID,
        "seed": seed,
        **metadata,
    }
    return {
        "schema_version": ATIF_VERSION,
        "agent": {
            "name": "seagym-evoagent-mimocode",
            "version": "0.1.0",
            "model_name": MODEL_NAME,
            "extra": safe_extra,
        },
        "steps": steps,
        "final_metrics": final_metrics,
        "extra": safe_extra,
    }


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _mimocode_exit_class(exit_code: Any) -> str:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return "unknown"
    if exit_code == 0:
        return "success"
    if exit_code == 124:
        return "timeout"
    if exit_code in {126, 127}:
        return "spawn_failed"
    if exit_code < 0 or 128 <= exit_code <= 255:
        return "signal"
    if 0 < exit_code < 128:
        return "nonzero"
    return "unknown"


def build_runtime_failure_receipt(
    *,
    mimocode_exit_code: int,
    sanitization_failed: bool,
    atif_present: bool,
    metadata: dict[str, Any],
    model: str,
    seed: int,
) -> dict[str, Any]:
    """Build a content-free, self-verifying runtime failure receipt."""

    try:
        normalized_metadata = _load_metadata(
            json.dumps(
                metadata,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise SanitizationError("failure receipt metadata is invalid") from exc
    if not isinstance(sanitization_failed, bool) or not isinstance(atif_present, bool):
        raise SanitizationError("failure receipt booleans are invalid")
    if model != MODEL_NAME:
        raise SanitizationError("failure receipt model is invalid")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**63 - 1:
        raise SanitizationError("failure receipt seed is invalid")
    exit_class = _mimocode_exit_class(mimocode_exit_code)
    if sanitization_failed:
        failure_class = (
            "runtime_sanitization_failed"
            if exit_class == "success"
            else "mimocode_and_sanitization_failed"
        )
        failure_stage = "sanitize"
    else:
        if exit_class == "success":
            raise SanitizationError("a successful runtime cannot emit a failure receipt")
        failure_class = "mimocode_process_failed"
        failure_stage = "mimocode"
    if failure_class not in _FAILURE_CLASSES or failure_stage not in _FAILURE_STAGES:
        raise SanitizationError("failure receipt classification is invalid")
    if exit_class not in _MIMOCODE_EXIT_CLASSES:
        raise SanitizationError("failure receipt exit classification is invalid")
    unsigned = {
        "schema_version": FAILURE_RECEIPT_SCHEMA,
        "failure_class": failure_class,
        "failure_stage": failure_stage,
        "mimocode_exit_class": exit_class,
        "snapshot_sha256": normalized_metadata["snapshot_hash"],
        "component_sha256": dict(normalized_metadata["component_hashes"]),
        "route_contract_sha256": normalized_metadata["route_contract_sha256"],
        "model": {"api_id": API_MODEL_ID, "harbor_id": MODEL_NAME},
        "seed": seed,
        "runtime": dict(normalized_metadata["runtime_identity"]),
        "atif_present": atif_present,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "reasoning_content_persisted": False,
    }
    receipt_sha256 = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
    return {**unsigned, "receipt_sha256": receipt_sha256}


def write_runtime_failure_receipt(
    path: str | os.PathLike[str],
    *,
    mimocode_exit_code: int,
    sanitization_failed: bool,
    atif_present: bool,
    metadata: dict[str, Any],
    model: str,
    seed: int,
) -> dict[str, Any]:
    receipt = build_runtime_failure_receipt(
        mimocode_exit_code=mimocode_exit_code,
        sanitization_failed=sanitization_failed,
        atif_present=atif_present,
        metadata=metadata,
        model=model,
        seed=seed,
    )
    _atomic_write_json(Path(path).absolute(), receipt)
    return receipt


def _scrub_regular_file(path: Path) -> None:
    try:
        size = path.stat(follow_symlinks=False).st_size
        with path.open("r+b", buffering=0) as stream:
            block = b"\x00" * min(65_536, max(1, size))
            remaining = size
            while remaining:
                amount = min(remaining, len(block))
                stream.write(block[:amount])
                remaining -= amount
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass


def _scrub_and_remove(path: Path | None) -> None:
    if path is None:
        return
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode):
        try:
            path.unlink()
        except OSError:
            pass
        return
    if stat.S_ISDIR(info.st_mode):
        try:
            children = list(path.iterdir())
        except OSError:
            children = []
        for child in children:
            _scrub_and_remove(child)
        try:
            path.rmdir()
        except OSError:
            shutil.rmtree(path, ignore_errors=True)
        return
    if stat.S_ISREG(info.st_mode):
        _scrub_regular_file(path)
    try:
        path.unlink()
    except OSError:
        pass


def sanitize_runtime_jsonl(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    model: str,
    seed: int,
    snapshot_metadata_json: str,
    prompt_path: str | os.PathLike[str] | None = None,
    session_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Sanitize one JSONL file and always destroy designated raw artifacts."""

    source = Path(input_path).absolute()
    destination = Path(output_path).absolute()
    prompt = Path(prompt_path).absolute() if prompt_path is not None else None
    session = Path(session_dir).absolute() if session_dir is not None else None
    try:
        if source == destination:
            raise SanitizationError("input and output paths must differ")
        if session is not None and (destination == session or session in destination.parents):
            raise SanitizationError("output cannot be inside the disposable session directory")
        if prompt is not None and destination == prompt:
            raise SanitizationError("output cannot be the disposable prompt file")
        if model != MODEL_NAME:
            raise SanitizationError(f"model must be exactly {MODEL_NAME}")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed > 2**63 - 1:
            raise SanitizationError("seed must be a non-negative signed 64-bit integer")
        metadata = _load_metadata(snapshot_metadata_json)
        # Materialize within the fixed file/event limits so the JSONL stream is
        # closed before validation can fail.  This is essential for reliable
        # scrubbing on Windows, where an open generator keeps the file locked.
        events = list(_read_events(source))
        trajectory = _build_trajectory(events, seed=seed, metadata=metadata)
        _atomic_write_json(destination, trajectory)
        return trajectory
    finally:
        _scrub_and_remove(source)
        if prompt != source and prompt != destination:
            _scrub_and_remove(prompt)
        if session != source and session != destination:
            _scrub_and_remove(session)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="MiMoCode JSONL file (destroyed after use)")
    parser.add_argument("--output", required=True, help="destination ATIF JSON file")
    parser.add_argument("--model", required=True, help=f"must be {MODEL_NAME}")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--snapshot-metadata-json", required=True, help="bounded snapshot metadata object")
    parser.add_argument("--prompt-file", help="optional disposable prompt file")
    parser.add_argument("--session-dir", help="optional disposable MiMoCode session directory")
    parser.add_argument("--mimocode-exit-code", required=True, type=int)
    parser.add_argument("--failure-receipt", required=True, help="content-free failure receipt destination")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    metadata: dict[str, Any] | None = None
    try:
        metadata = _load_metadata(args.snapshot_metadata_json)
        sanitize_runtime_jsonl(
            args.input,
            args.output,
            model=args.model,
            seed=args.seed,
            snapshot_metadata_json=args.snapshot_metadata_json,
            prompt_path=args.prompt_file,
            session_dir=args.session_dir,
        )
    except (OSError, SanitizationError) as exc:
        if metadata is not None:
            try:
                destination = Path(args.output).absolute()
                write_runtime_failure_receipt(
                    args.failure_receipt,
                    mimocode_exit_code=args.mimocode_exit_code,
                    sanitization_failed=True,
                    atif_present=(
                        destination.is_file() and not destination.is_symlink()
                    ),
                    metadata=metadata,
                    model=args.model,
                    seed=args.seed,
                )
            except (OSError, SanitizationError):
                pass
        raise SystemExit(f"sanitization failed: {type(exc).__name__}") from None
    if metadata is None:  # Defensive type narrowing; successful sanitization parsed it.
        raise SystemExit("sanitization failed: SanitizationError")
    if args.mimocode_exit_code != 0:
        destination = Path(args.output).absolute()
        try:
            write_runtime_failure_receipt(
                args.failure_receipt,
                mimocode_exit_code=args.mimocode_exit_code,
                sanitization_failed=False,
                atif_present=(destination.is_file() and not destination.is_symlink()),
                metadata=metadata,
                model=args.model,
                seed=args.seed,
            )
        except (OSError, SanitizationError) as exc:
            raise SystemExit(f"failure receipt failed: {type(exc).__name__}") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
