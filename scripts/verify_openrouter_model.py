from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


MODELS_URL = "https://openrouter.ai/api/v1/models"
MODEL_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model_id}/endpoints"
RESPONSES_URL = "https://openrouter.ai/api/v1/responses"


def _sanitize_error_body(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[redacted]" if key == "user_id" else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return json.dumps(redact(payload), separators=(",", ":"), sort_keys=True)


def _request_json(
    request: urllib.request.Request,
    *,
    timeout: int,
    max_attempts: int = 1,
) -> Any:
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = _sanitize_error_body(
                exc.read(1000).decode("utf-8", errors="replace")
            )
            retryable = exc.code in {429, 503} and attempt < max_attempts
            if retryable:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after is not None else 0.0
                except ValueError:
                    delay = 0.0
                if delay <= 0:
                    delay = min(5 * (2 ** (attempt - 1)), 30)
                delay = min(delay, 30)
                print(
                    f"OpenRouter HTTP {exc.code}; retrying attempt "
                    f"{attempt + 1}/{max_attempts} after {delay:g}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"OpenRouter HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter request failed: {exc.reason}") from exc
    raise AssertionError("request retry loop exhausted without returning or raising")


def verify_model(model_id: str) -> dict[str, Any]:
    payload = _request_json(urllib.request.Request(MODELS_URL), timeout=30)
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("OpenRouter model catalogue has an unexpected schema")
    model = next(
        (item for item in models if isinstance(item, dict) and item.get("id") == model_id),
        None,
    )
    if model is None:
        raise RuntimeError(f"OpenRouter model is not currently listed: {model_id}")
    supported = model.get("supported_parameters") or []
    required = {"max_tokens", "tools"}
    missing = required - set(supported)
    if missing:
        raise RuntimeError(
            f"OpenRouter model lacks required agent parameters: {sorted(missing)}"
        )
    top_provider = model.get("top_provider") or {}
    return {
        "model_id": model["id"],
        "canonical_slug": model.get("canonical_slug"),
        "name": model.get("name"),
        "context_length": model.get("context_length"),
        "max_completion_tokens": top_provider.get("max_completion_tokens"),
        "pricing": model.get("pricing"),
        "reasoning": model.get("reasoning"),
        "supported_parameters": supported,
        "expiration_date": model.get("expiration_date"),
        "catalogue_url": MODELS_URL,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_preset(path: Path) -> dict[str, Any]:
    preset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(preset, dict):
        raise RuntimeError("OpenRouter preset root must be an object")
    required_keys = {
        "model_id",
        "canonical_model_id",
        "provider_slug",
        "provider_name",
        "prompt_cost_per_token_usd",
        "completion_cost_per_token_usd",
        "context_length",
        "max_completion_tokens",
        "supports_tools",
    }
    missing_keys = required_keys - set(preset)
    if missing_keys:
        raise RuntimeError(f"OpenRouter preset lacks fields: {sorted(missing_keys)}")
    tool_choice_mode = preset.get("tool_choice_mode")
    tool_choice_verified_at = preset.get("tool_choice_verified_at")
    endpoint_tag = preset.get("endpoint_tag")
    if tool_choice_mode not in {None, "named_function", "required_single_tool"}:
        raise RuntimeError("OpenRouter preset has an invalid Tool-choice mode")
    if (tool_choice_mode is None) != (tool_choice_verified_at is None):
        raise RuntimeError(
            "Explicit Tool-choice mode lacks its capability verification time"
        )
    if (tool_choice_mode is None) != (endpoint_tag is None):
        raise RuntimeError("Explicit Tool-choice mode lacks its exact endpoint tag")
    if endpoint_tag == "":
        raise RuntimeError("Explicit Tool-choice endpoint tag cannot be empty")
    selected_endpoint_tag = endpoint_tag or preset["provider_slug"]
    provider_slug = str(preset["provider_slug"])
    if selected_endpoint_tag != provider_slug and not str(
        selected_endpoint_tag
    ).startswith(f"{provider_slug}/"):
        raise RuntimeError("Pinned endpoint tag does not belong to the provider slug")

    catalogue = verify_model(str(preset["model_id"]))
    endpoint_url = MODEL_ENDPOINTS_URL.format(model_id=preset["model_id"])
    payload = _request_json(urllib.request.Request(endpoint_url), timeout=30)
    data = payload.get("data") if isinstance(payload, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list):
        raise RuntimeError("OpenRouter endpoint catalogue has an unexpected schema")
    endpoint = next(
        (
            item
            for item in endpoints
            if isinstance(item, dict)
            and item.get("tag") == selected_endpoint_tag
            and item.get("provider_name") == preset["provider_name"]
        ),
        None,
    )
    if endpoint is None:
        raise RuntimeError("Pinned OpenRouter provider endpoint is unavailable")
    if endpoint.get("status") != 0:
        raise RuntimeError("Pinned OpenRouter provider endpoint is not active")
    active_provider_endpoints = [
        item
        for item in endpoints
        if isinstance(item, dict)
        and item.get("provider_name") == preset["provider_name"]
        and item.get("status") == 0
    ]
    if endpoint_tag is not None and (
        len(active_provider_endpoints) != 1
        or active_provider_endpoints[0].get("tag") != selected_endpoint_tag
    ):
        raise RuntimeError(
            "Pinned provider slug does not resolve to one exact active endpoint"
        )
    supported = set(endpoint.get("supported_parameters") or [])
    required_parameters = {"max_tokens", "tools", "tool_choice"}
    if preset.get("reasoning_enabled") is not None:
        required_parameters.add("reasoning")
    missing_parameters = required_parameters - supported
    if missing_parameters:
        raise RuntimeError(
            "Pinned OpenRouter endpoint lacks required parameters: "
            f"{sorted(missing_parameters)}"
        )
    reasoning = catalogue.get("reasoning")
    if preset.get("reasoning_enabled") is False and (
        not isinstance(reasoning, dict) or reasoning.get("mandatory") is True
    ):
        raise RuntimeError("Pinned OpenRouter model cannot disable reasoning")

    checks = {
        "canonical_model_id": catalogue.get("canonical_slug"),
        "context_length": endpoint.get("context_length"),
        "max_completion_tokens": endpoint.get("max_completion_tokens"),
        "provider_name": endpoint.get("provider_name"),
        "endpoint_tag": endpoint.get("tag"),
    }
    for key, actual in checks.items():
        expected = selected_endpoint_tag if key == "endpoint_tag" else preset[key]
        if actual != expected:
            raise RuntimeError(
                f"OpenRouter preset drift for {key}: expected {expected!r}, "
                f"catalogue returned {actual!r}"
            )
    endpoint_pricing = endpoint.get("pricing") or {}
    pricing_checks = {
        "prompt_cost_per_token_usd": endpoint_pricing.get("prompt"),
        "completion_cost_per_token_usd": endpoint_pricing.get("completion"),
    }
    for key, actual in pricing_checks.items():
        if Decimal(str(actual)) != Decimal(str(preset[key])):
            raise RuntimeError(
                f"OpenRouter preset price drift for {key}: expected {preset[key]!r}, "
                f"catalogue returned {actual!r}"
            )
    if preset["supports_tools"] is not True:
        raise RuntimeError("Scientific OpenRouter preset must require Tool support")

    return {
        "catalogue": catalogue,
        "endpoint": {
            "provider_name": endpoint["provider_name"],
            "provider_slug": provider_slug,
            "endpoint_tag": endpoint["tag"],
            "model_id": endpoint.get("model_id"),
            "context_length": endpoint["context_length"],
            "max_completion_tokens": endpoint["max_completion_tokens"],
            "pricing": endpoint_pricing,
            "supported_parameters": sorted(supported),
            "status": endpoint.get("status"),
            "endpoint_url": endpoint_url,
        },
        "preset_id": preset.get("preset_id"),
        "reasoning_enabled": preset.get("reasoning_enabled"),
        "tool_choice_mode": tool_choice_mode,
        "tool_choice_verified_at": tool_choice_verified_at,
    }


def probe_model(model_id: str) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    request = urllib.request.Request(
        RESPONSES_URL,
        data=json.dumps(
            {
                "model": model_id,
                "input": "Reply with exactly: OK",
                "max_output_tokens": 8,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "EvoAgent SkillEvolBench preflight",
        },
        method="POST",
    )
    payload = _request_json(request, timeout=90, max_attempts=6)
    response_model = payload.get("model") if isinstance(payload, dict) else None
    return {
        "status": "success",
        "response_id_present": bool(
            isinstance(payload, dict) and payload.get("id")
        ),
        "response_model": response_model,
        "probed_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--model-id")
    selection.add_argument("--preset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)

    if args.preset is not None:
        record = verify_preset(args.preset)
        model_id = str(record["catalogue"]["model_id"])
    else:
        model_id = str(args.model_id)
        record = {"catalogue": verify_model(model_id)}
    if args.probe:
        record["authenticated_probe"] = probe_model(model_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"OpenRouter model verified: {model_id}")
    if args.probe:
        print("Authenticated OpenRouter probe succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
