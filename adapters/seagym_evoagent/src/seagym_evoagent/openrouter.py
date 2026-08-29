"""Minimal OpenRouter structured-output client with a fail-closed response guard."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
import socket
from typing import Any, Callable
import urllib.error
import urllib.request

from .canonical import canonical_bytes, strict_json_loads
from .models import CANONICAL_MODEL_ID, UPDATE_MODEL_ID, candidate_json_schema
from .routing import expected_route_contract, validate_route_contract


OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
ROUTE_PROVIDER = "xiaomi/fp8"
CANDIDATE_TOOL_NAME = "evoagent_harness_components"
Transport = Callable[[str, dict[str, str], bytes, float], bytes]


def safe_probe_failure_code(exc: BaseException) -> str:
    """Return a bounded diagnostic code without serializing provider data."""

    if isinstance(exc, RuntimeError):
        prefix = "OpenRouter returned HTTP "
        message = str(exc)
        if message.startswith(prefix):
            status = message[len(prefix) :]
            if len(status) == 3 and status.isascii() and status.isdigit() and 400 <= int(status) <= 599:
                return f"openrouter_http_{status}"
        if message.startswith("OpenRouter request failed ("):
            return "openrouter_transport_failure"
        return "openrouter_runtime_failure"
    if isinstance(exc, ValueError):
        return "openrouter_response_validation_failed"
    return "unexpected_probe_failure"


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class StructuredCompletion:
    candidate: dict[str, Any]
    usage: ModelUsage
    request_sha256: str
    response_sha256: str
    served_model_id: str
    provider: str


class OpenRouterStructuredClient:
    def __init__(
        self,
        *,
        api_key_env: str = "OPENROUTER_API_KEY",
        endpoint: str = OPENROUTER_ENDPOINT,
        model_id: str = UPDATE_MODEL_ID,
        timeout_seconds: float = 180.0,
        transport: Transport | None = None,
        route_contract: dict[str, Any] | None = None,
    ) -> None:
        if api_key_env != "OPENROUTER_API_KEY":
            raise ValueError("the adapter permits only OPENROUTER_API_KEY")
        if endpoint != OPENROUTER_ENDPOINT or model_id != UPDATE_MODEL_ID:
            raise ValueError("OpenRouter endpoint and model are fixed by the experiment protocol")
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be in [1, 600]")
        self.api_key_env = api_key_env
        self.endpoint = endpoint
        self.model_id = model_id
        self.timeout_seconds = float(timeout_seconds)
        self.transport = transport or _urllib_transport
        self.route_contract = validate_route_contract(route_contract or expected_route_contract())

    def complete(
        self,
        *,
        evidence: dict[str, Any],
        current_components: dict[str, Any],
        seed: int,
    ) -> StructuredCompletion:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**63 - 1:
            raise ValueError("seed must be a non-negative signed 64-bit integer")
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        user_payload = {
            "observable_train_evidence": evidence,
            "current_harness": current_components,
            "required_output": "Return a general four-component evaluation candidate only.",
            "claim_boundary": {
                "causal_attribution": False,
                "promotion": False,
                "activation": False,
            },
        }
        schema = candidate_json_schema()
        if schema.get("name") != CANDIDATE_TOOL_NAME or not isinstance(schema.get("schema"), dict):
            raise ValueError("candidate Tool schema identity is invalid")
        request_payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Produce strict JSON for a general tool-agent harness. Use only the supplied "
                        "aggregate observable evidence. Do not emit identifiers, task-specific facts, "
                        "paths, URLs, secrets, canaries, transcripts, model text, or hidden reasoning. "
                        "Do not claim causality, promotion, or activation."
                    ),
                },
                {"role": "user", "content": canonical_bytes(user_payload).decode("utf-8")},
            ],
            # The frozen Xiaomi endpoint was live-verified with one exposed
            # function and tool_choice="required".  Do not silently switch to
            # an unverified response_format/json_schema transport here.
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": CANDIDATE_TOOL_NAME,
                        "description": "Return the complete general EvoAgent evaluation harness candidate.",
                        "parameters": schema["schema"],
                    },
                }
            ],
            "tool_choice": "required",
            "provider": self.route_contract["provider"],
            "reasoning": self.route_contract["reasoning"],
            "temperature": 0,
            # The frozen Xiaomi endpoint does not advertise the OpenRouter
            # ``seed`` parameter.  With require_parameters=True, forwarding it
            # removes the only permitted endpoint before inference.  The host
            # seed still binds split/order and host-side attempt/evidence
            # records; provider sampling determinism is deliberately not
            # claimed.
            "max_tokens": 3000,
            "usage": {"include": True},
        }
        body = canonical_bytes(request_payload)
        request_hash = hashlib.sha256(body).hexdigest()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "evoagent-seagym/0.1.0",
            "X-OpenRouter-Cache": "false",
            "X-OpenRouter-Metadata": "enabled",
        }
        try:
            raw_response = self.transport(self.endpoint, headers, body, self.timeout_seconds)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise RuntimeError(f"OpenRouter request failed ({type(exc).__name__})") from None
        response_hash = hashlib.sha256(raw_response).hexdigest()
        response = strict_json_loads(raw_response)
        candidate, usage, served_model_id, provider = _parse_response(response, self.route_contract)
        return StructuredCompletion(
            candidate,
            usage,
            request_hash,
            response_hash,
            served_model_id,
            provider,
        )


def _urllib_transport(endpoint: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is fixed
            if response.status != 200:
                raise RuntimeError(f"unexpected OpenRouter status {response.status}")
            return response.read(256 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OpenRouter returned HTTP {exc.code}") from None


def _parse_response(
    response: Any,
    route_contract: dict[str, Any],
) -> tuple[dict[str, Any], ModelUsage, str, str]:
    if not isinstance(response, dict):
        raise ValueError("OpenRouter response must be an object")
    _reject_reasoning(response)
    served_model_id = response.get("model")
    if served_model_id not in route_contract["accepted_response_models"]:
        raise ValueError("OpenRouter response model identity drifted from the frozen alias")
    provider = response.get("provider")
    if provider != route_contract["response_provider"]:
        raise ValueError("OpenRouter response provider drifted from Xiaomi")
    _validate_router_metadata(response.get("openrouter_metadata"), route_contract)
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ValueError("OpenRouter response must contain exactly one choice")
    choice = choices[0]
    if choice.get("finish_reason") != "tool_calls":
        raise ValueError("OpenRouter structured response did not finish with a Tool call")
    message = choice.get("message")
    if not isinstance(message, dict) or set(message) - {
        "role",
        "content",
        "refusal",
        "reasoning",
        "reasoning_content",
        "reasoning_details",
        "tool_calls",
    }:
        raise ValueError("OpenRouter response message has an unexpected shape")
    if message.get("role") != "assistant" or message.get("refusal") not in (None, ""):
        raise ValueError("OpenRouter response was not an unrefused assistant result")
    if message.get("content") not in (None, ""):
        raise ValueError("OpenRouter required-Tool response contains unexpected prose")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        raise ValueError("OpenRouter response must contain exactly one Tool call")
    call = calls[0]
    if set(call) - {"id", "type", "function", "index"}:
        raise ValueError("OpenRouter Tool call has an unexpected shape")
    if not isinstance(call.get("id"), str) or not call["id"].strip() or call.get("type") != "function":
        raise ValueError("OpenRouter Tool call identity is invalid")
    function = call.get("function")
    if not isinstance(function, dict) or set(function) != {"name", "arguments"}:
        raise ValueError("OpenRouter Tool function has an unexpected shape")
    if function.get("name") != CANDIDATE_TOOL_NAME:
        raise ValueError("OpenRouter returned the wrong candidate Tool")
    arguments = function.get("arguments")
    if not isinstance(arguments, str) or not arguments or len(arguments.encode("utf-8")) > 64 * 1024:
        raise ValueError("OpenRouter Tool arguments are invalid or oversized")
    candidate = strict_json_loads(arguments)
    if not isinstance(candidate, dict):
        raise ValueError("structured candidate must be an object")
    usage = _parse_usage(response.get("usage"))
    return candidate, usage, served_model_id, provider


def _validate_router_metadata(raw: Any, route_contract: dict[str, Any]) -> None:
    if not isinstance(raw, dict):
        raise ValueError("OpenRouter router metadata is required on the cache-disabled route")
    if raw.get("requested") != UPDATE_MODEL_ID:
        raise ValueError("OpenRouter router metadata requested model drifted")
    if raw.get("strategy") not in {"alias", "direct"} or raw.get("attempt") != 1:
        raise ValueError("OpenRouter router metadata strategy or attempt drifted")
    if raw.get("pipeline", []) != []:
        raise ValueError("OpenRouter materially altered the request or response")
    endpoints = raw.get("endpoints")
    available = endpoints.get("available") if isinstance(endpoints, dict) else None
    if not isinstance(available, list) or not available:
        raise ValueError("OpenRouter router metadata lacks endpoint evidence")
    selected = [item for item in available if isinstance(item, dict) and item.get("selected") is True]
    if len(selected) != 1:
        raise ValueError("OpenRouter router metadata must select exactly one endpoint")
    endpoint = selected[0]
    if (
        endpoint.get("provider") != route_contract["response_provider"]
        or endpoint.get("model") not in route_contract["accepted_response_models"]
    ):
        raise ValueError("OpenRouter selected endpoint drifted from Xiaomi")
    attempts = raw.get("attempts")
    if attempts is not None:
        if not isinstance(attempts, list) or len(attempts) != 1 or not isinstance(attempts[0], dict):
            raise ValueError("OpenRouter router metadata attempt history drifted")
        attempt = attempts[0]
        if (
            attempt.get("provider") != route_contract["response_provider"]
            or attempt.get("model") not in route_contract["accepted_response_models"]
            or attempt.get("status") != 200
        ):
            raise ValueError("OpenRouter router metadata records an unexpected attempt")


def _reject_reasoning(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"reasoning", "reasoning_content", "reasoning_details"} and item not in (None, "", [], {}):
                raise ValueError("unexpected reasoning content in OpenRouter response")
            _reject_reasoning(item)
    elif isinstance(value, list):
        for item in value:
            _reject_reasoning(item)


def _parse_usage(raw: Any) -> ModelUsage:
    if not isinstance(raw, dict):
        raise ValueError("OpenRouter usage accounting is required")
    prompt = _bounded_int(raw.get("prompt_tokens"), "prompt_tokens")
    completion = _bounded_int(raw.get("completion_tokens"), "completion_tokens")
    total = _bounded_int(raw.get("total_tokens"), "total_tokens")
    if total != prompt + completion:
        raise ValueError("OpenRouter token totals are inconsistent")
    cost = raw.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        raise ValueError("OpenRouter usage cost is required")
    cost_float = float(cost)
    if not math.isfinite(cost_float) or not 0 <= cost_float <= 100_000:
        raise ValueError("OpenRouter usage cost is outside the permitted range")
    return ModelUsage(prompt, completion, total, cost_float)


def _bounded_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000_000_000:
        raise ValueError(f"OpenRouter {label} must be a bounded integer")
    return value
