from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


_FINAL_METRIC_FIELDS = (
    "total_prompt_tokens",
    "total_completion_tokens",
    "total_cached_tokens",
    "total_cost_usd",
    "total_steps",
)
_FINAL_EXTRA_FIELDS = (
    "total_cache_creation_input_tokens",
    "total_cache_read_input_tokens",
)


def _nonnegative_number(value: Any, *, field: str) -> int | float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Harbor trajectory metric {field} must be numeric")
    if not math.isfinite(value) or value < 0:
        raise ValueError(
            f"Harbor trajectory metric {field} must be finite and non-negative"
        )
    return value


def _message_length(message: Any) -> int:
    if isinstance(message, str):
        return len(message)
    if not isinstance(message, list):
        return 0
    total = 0
    for part in message:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            total += len(part["text"])
    return total


def summarize_trajectory(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Harbor trajectory must be a JSON object")
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Harbor trajectory must contain a steps array")

    source_counts: Counter[str] = Counter()
    tool_name_counts: Counter[str] = Counter()
    observation_result_count = 0
    agent_message_char_count = 0
    model_names: set[str] = set()
    model_request_count = 0
    request_prompt_tokens: list[int | float] = []
    request_completion_tokens: list[int | float] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        source = step.get("source")
        if isinstance(source, str):
            source_counts[source] += 1
        if source == "agent":
            agent_message_char_count += _message_length(step.get("message"))
        model_name = step.get("model_name")
        if isinstance(model_name, str) and model_name:
            model_names.add(model_name)
        metrics = step.get("metrics")
        if isinstance(metrics, dict):
            model_request_count += 1
            prompt_tokens = _nonnegative_number(
                metrics.get("prompt_tokens"), field="step.prompt_tokens"
            )
            completion_tokens = _nonnegative_number(
                metrics.get("completion_tokens"),
                field="step.completion_tokens",
            )
            if prompt_tokens is not None:
                request_prompt_tokens.append(prompt_tokens)
            if completion_tokens is not None:
                request_completion_tokens.append(completion_tokens)
        tool_calls = step.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("function_name") or call.get("name") or "unknown"
                tool_name_counts[str(name)] += 1
        observation = step.get("observation")
        if isinstance(observation, dict) and isinstance(observation.get("results"), list):
            observation_result_count += len(observation["results"])

    usage: dict[str, int | float] = {}
    final_metrics = payload.get("final_metrics")
    if isinstance(final_metrics, dict):
        for field in _FINAL_METRIC_FIELDS:
            value = _nonnegative_number(
                final_metrics.get(field), field=f"final_metrics.{field}"
            )
            if value is not None:
                usage[field] = value
        extra = final_metrics.get("extra")
        if isinstance(extra, dict):
            for field in _FINAL_EXTRA_FIELDS:
                value = _nonnegative_number(
                    extra.get(field), field=f"final_metrics.extra.{field}"
                )
                if value is not None:
                    usage[field] = value

    return {
        "schema_version": "2",
        "source_trajectory_sha256": hashlib.sha256(raw).hexdigest(),
        "step_count": len(steps),
        "source_counts": dict(sorted(source_counts.items())),
        "tool_call_count": sum(tool_name_counts.values()),
        "tool_name_counts": dict(sorted(tool_name_counts.items())),
        "observation_result_count": observation_result_count,
        "agent_message_char_count": agent_message_char_count,
        "model_names": sorted(model_names),
        "model_request_count": model_request_count,
        "max_prompt_tokens_per_request": (
            max(request_prompt_tokens) if request_prompt_tokens else None
        ),
        "max_completion_tokens_per_request": (
            max(request_completion_tokens) if request_completion_tokens else None
        ),
        "usage": usage,
        "usage_source": "harbor_trajectory_final_metrics",
        "raw_messages_included": False,
        "raw_tool_arguments_included": False,
        "raw_observations_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a content-free structural summary of a Harbor trajectory."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary = summarize_trajectory(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
