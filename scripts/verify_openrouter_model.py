from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODELS_URL = "https://openrouter.ai/api/v1/models"
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
        "supported_parameters": supported,
        "expiration_date": model.get("expiration_date"),
        "catalogue_url": MODELS_URL,
        "verified_at": datetime.now(timezone.utc).isoformat(),
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
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)

    record = {"catalogue": verify_model(args.model_id)}
    if args.probe:
        record["authenticated_probe"] = probe_model(args.model_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"OpenRouter model verified: {args.model_id}")
    if args.probe:
        print("Authenticated OpenRouter probe succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
