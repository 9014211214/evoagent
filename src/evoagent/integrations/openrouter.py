from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.runtime import AgentAction, AgentActionKind, AgentContext, ToolAgentPolicy


CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterIntegrationError(RuntimeError):
    pass


class OpenRouterModelPreset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
    canonical_model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
    provider_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    provider_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
    prompt_cost_per_token_usd: Decimal = Field(ge=0)
    completion_cost_per_token_usd: Decimal = Field(ge=0)
    context_length: int = Field(gt=0)
    max_completion_tokens: int = Field(gt=0)
    supports_tools: bool
    catalogue_verified_at: str

    @model_validator(mode="after")
    def validate_agent_support(self):
        if not self.supports_tools:
            raise ValueError("OpenRouter calibration preset must support Tool calls.")
        return self


class OpenRouterPolicyUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requests: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class OpenRouterUsageLedger:
    """One fail-closed usage ceiling shared by many short Agent episodes."""

    def __init__(
        self,
        *,
        preset: OpenRouterModelPreset,
        max_requests: int,
        max_prompt_bytes_per_request: int,
        max_output_tokens_per_request: int,
        max_cost_usd: float,
    ):
        if (
            max_requests <= 0
            or max_prompt_bytes_per_request <= 0
            or max_output_tokens_per_request <= 0
        ):
            raise ValueError("OpenRouter shared-ledger limits must be positive.")
        if not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise ValueError("OpenRouter shared cost limit must be finite and positive.")
        if max_output_tokens_per_request > preset.max_completion_tokens:
            raise ValueError("OpenRouter shared output cap exceeds the pinned endpoint.")
        worst = (
            Decimal(max_requests * max_prompt_bytes_per_request)
            * preset.prompt_cost_per_token_usd
            + Decimal(max_requests * max_output_tokens_per_request)
            * preset.completion_cost_per_token_usd
        )
        if worst > Decimal(str(max_cost_usd)):
            raise ValueError("OpenRouter shared mathematical ceiling exceeds its cost cap.")
        self.preset = preset
        self.max_requests = max_requests
        self.max_prompt_bytes_per_request = max_prompt_bytes_per_request
        self.max_output_tokens_per_request = max_output_tokens_per_request
        self.max_cost_usd = max_cost_usd
        self.mathematical_cost_ceiling_usd = float(worst)
        self._attempted_requests = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._cost_usd = 0.0

    def reserve(self, *, prompt_bytes: int, max_output_tokens: int) -> None:
        if prompt_bytes > self.max_prompt_bytes_per_request:
            raise OpenRouterIntegrationError(
                "OpenRouter request exceeds the shared prompt-byte cap."
            )
        if max_output_tokens > self.max_output_tokens_per_request:
            raise OpenRouterIntegrationError(
                "OpenRouter request exceeds the shared output-token cap."
            )
        if self._attempted_requests >= self.max_requests:
            raise OpenRouterIntegrationError("OpenRouter shared request-count cap reached.")
        # Reserve before network I/O: even a timed-out request may have reached
        # the provider and therefore must consume the one-run request allowance.
        self._attempted_requests += 1

    def record(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
    ) -> None:
        projected = self._cost_usd + cost_usd
        if projected > self.max_cost_usd:
            raise OpenRouterIntegrationError(
                "OpenRouter shared run exceeded its cost cap."
            )
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._total_tokens += total_tokens
        self._cost_usd = projected

    @property
    def usage(self) -> OpenRouterPolicyUsage:
        return OpenRouterPolicyUsage(
            requests=self._attempted_requests,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
            cost_usd=self._cost_usd,
        )


Transport = Callable[[dict[str, Any], str], dict[str, Any]]


class OpenRouterControlledToolPolicy(ToolAgentPolicy):
    """Require OpenRouter to reproduce one exact frozen-controller Tool call.

    The local Full-Agent controller remains responsible for routing, Skill,
    Memory and numeric-policy decisions. The external model receives only the
    exact observable action selected by that controller and must emit one
    matching typed Tool call. Raw request and response bodies are never exposed
    through metadata or usage records.
    """

    def __init__(
        self,
        *,
        controller: ToolAgentPolicy,
        preset: OpenRouterModelPreset,
        api_key: str,
        max_requests: int = 3,
        max_output_tokens: int = 256,
        max_prompt_bytes_per_request: int = 32_768,
        max_cost_usd: float = 2.0,
        shared_ledger: OpenRouterUsageLedger | None = None,
        transport: Transport | None = None,
    ):
        if not api_key or "\x00" in api_key:
            raise ValueError("OpenRouter API key must be present and NUL-free.")
        if max_requests <= 0 or max_output_tokens <= 0 or max_prompt_bytes_per_request <= 0:
            raise ValueError("OpenRouter calibration limits must be positive.")
        if not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise ValueError("OpenRouter calibration cost limit must be finite and positive.")
        if max_output_tokens > preset.max_completion_tokens:
            raise ValueError("OpenRouter output cap exceeds the pinned model endpoint.")
        worst = (
            Decimal(max_requests * max_prompt_bytes_per_request)
            * preset.prompt_cost_per_token_usd
            + Decimal(max_requests * max_output_tokens)
            * preset.completion_cost_per_token_usd
        )
        if worst > Decimal(str(max_cost_usd)):
            raise ValueError("OpenRouter mathematical request ceiling exceeds the cost cap.")
        self.controller = controller
        self.preset = preset
        self._api_key = api_key
        self.max_requests = max_requests
        self.max_output_tokens = max_output_tokens
        self.max_prompt_bytes_per_request = max_prompt_bytes_per_request
        self.max_cost_usd = max_cost_usd
        self.mathematical_cost_ceiling_usd = float(worst)
        if shared_ledger is not None and shared_ledger.preset != preset:
            raise ValueError("OpenRouter shared ledger uses another pinned model preset.")
        self._shared_ledger = shared_ledger
        self._transport = transport or self._post_json
        self._requests = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._cost_usd = 0.0
        self._metadata: dict[tuple[str, int], dict[str, object]] = {}

    def next_action(self, context: AgentContext) -> AgentAction:
        controlled = self.controller.next_action(context)
        controller_metadata = self.controller.observable_metadata(context)
        metadata = {
            **controller_metadata,
            "external_model_preset": self.preset.preset_id,
            "external_provider": self.preset.provider_name,
            "controller_action_kind": controlled.kind.value,
        }
        self._metadata[(context.task.task_id, context.step_index)] = metadata
        if controlled.kind == AgentActionKind.FINISH:
            return controlled
        if controlled.tool_call is None:
            raise OpenRouterIntegrationError("Frozen controller emitted an invalid Tool action.")
        if self._requests >= self.max_requests:
            raise OpenRouterIntegrationError("OpenRouter request-count cap reached.")

        tool = controlled.tool_call
        payload = {
            "model": self.preset.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a typed Tool-call transport. The frozen EvoAgent controller "
                        "has selected one exact observable action. Emit exactly one matching "
                        "function call and no prose. Do not change any argument."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_id": context.task.task_id,
                            "task_type": context.task.task_type,
                            "snapshot_hash": controller_metadata.get("snapshot_hash"),
                            "state_fingerprint": context.observation.state_fingerprint,
                            "required_tool": tool.tool_name,
                            "required_arguments": tool.arguments,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "tools": [self._tool_schema(tool.tool_name)],
            "tool_choice": {
                "type": "function",
                "function": {"name": tool.tool_name},
            },
            "max_tokens": self.max_output_tokens,
            "temperature": 0,
            "provider": {
                "only": [self.preset.provider_slug],
                "allow_fallbacks": False,
            },
        }
        encoded_size = len(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if encoded_size > self.max_prompt_bytes_per_request:
            raise OpenRouterIntegrationError("OpenRouter request exceeds the prompt-byte cap.")
        if self._shared_ledger is not None:
            self._shared_ledger.reserve(
                prompt_bytes=encoded_size,
                max_output_tokens=self.max_output_tokens,
            )
        response = self._transport(payload, self._api_key)
        self._requests += 1
        self._record_usage(response)
        self._verify_routing(response)
        model_name, arguments = self._one_tool_call(response)
        if model_name != tool.tool_name or arguments != tool.arguments:
            raise OpenRouterIntegrationError(
                "OpenRouter model changed the frozen controller Tool action."
            )
        return AgentAction.call(tool.call_id, tool.tool_name, **tool.arguments)

    def observable_metadata(self, context: AgentContext) -> dict[str, object]:
        return dict(self._metadata.get((context.task.task_id, context.step_index), {}))

    def observable_usage(self) -> dict[str, float]:
        return {
            "llm_requests": float(self._requests),
            "llm_prompt_tokens": float(self._prompt_tokens),
            "llm_completion_tokens": float(self._completion_tokens),
            "llm_tokens": float(self._total_tokens),
            "cost_usd": self._cost_usd,
        }

    @property
    def usage(self) -> OpenRouterPolicyUsage:
        return OpenRouterPolicyUsage(
            requests=self._requests,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
            cost_usd=self._cost_usd,
        )

    def _record_usage(self, response: dict[str, Any]) -> None:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            raise OpenRouterIntegrationError("OpenRouter response lacks usage accounting.")
        prompt = self._nonnegative_int(usage.get("prompt_tokens"), "prompt_tokens")
        completion = self._nonnegative_int(
            usage.get("completion_tokens"), "completion_tokens"
        )
        total = self._nonnegative_int(usage.get("total_tokens"), "total_tokens")
        cost = usage.get("cost")
        if (
            not isinstance(cost, (int, float))
            or isinstance(cost, bool)
            or not math.isfinite(float(cost))
            or float(cost) < 0
        ):
            raise OpenRouterIntegrationError("OpenRouter response has invalid cost accounting.")
        if total != prompt + completion:
            raise OpenRouterIntegrationError("OpenRouter token accounting is inconsistent.")
        self._prompt_tokens += prompt
        self._completion_tokens += completion
        self._total_tokens += total
        self._cost_usd += float(cost)
        if self._cost_usd > self.max_cost_usd:
            raise OpenRouterIntegrationError("OpenRouter calibration exceeded its cost cap.")
        if self._shared_ledger is not None:
            self._shared_ledger.record(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                cost_usd=float(cost),
            )

    def _verify_routing(self, response: dict[str, Any]) -> None:
        if response.get("model") not in {
            self.preset.model_id,
            self.preset.canonical_model_id,
        }:
            raise OpenRouterIntegrationError("OpenRouter returned another model.")
        provider = response.get("provider")
        if provider != self.preset.provider_name:
            raise OpenRouterIntegrationError("OpenRouter returned another provider.")

    @staticmethod
    def _one_tool_call(response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise OpenRouterIntegrationError("OpenRouter response must contain one choice.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if not isinstance(calls, list) or len(calls) != 1:
            raise OpenRouterIntegrationError("OpenRouter response must contain one Tool call.")
        function = calls[0].get("function") if isinstance(calls[0], dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise OpenRouterIntegrationError("OpenRouter returned an invalid Tool call.")
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str) or len(raw_arguments) > 16_384:
            raise OpenRouterIntegrationError("OpenRouter Tool arguments are invalid or oversized.")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise OpenRouterIntegrationError(
                "OpenRouter Tool arguments are not valid JSON."
            ) from exc
        if not isinstance(arguments, dict):
            raise OpenRouterIntegrationError("OpenRouter Tool arguments must be an object.")
        return function["name"], arguments

    @staticmethod
    def _tool_schema(tool_name: str) -> dict[str, Any]:
        if tool_name == "read_document":
            properties = {"path": {"type": "string"}}
            required = ["path"]
        elif tool_name == "write_document":
            properties = {
                "path": {"type": "string"},
                "content": {"type": "string"},
            }
            required = ["path", "content"]
        else:
            raise OpenRouterIntegrationError("Frozen controller selected an unallowlisted Tool.")
        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": "Execute the exact observable action selected by EvoAgent.",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _nonnegative_int(value: Any, label: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise OpenRouterIntegrationError(f"OpenRouter {label} is invalid.")
        return value

    @staticmethod
    def _post_json(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        request = urllib.request.Request(
            CHAT_COMPLETIONS_URL,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "EvoAgent Full-Agent integration calibration",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                parsed = json.load(response)
        except urllib.error.HTTPError as exc:
            raise OpenRouterIntegrationError(f"OpenRouter HTTP {exc.code}.") from None
        except urllib.error.URLError as exc:
            raise OpenRouterIntegrationError("OpenRouter transport failed.") from None
        if not isinstance(parsed, dict):
            raise OpenRouterIntegrationError("OpenRouter response root must be an object.")
        return parsed


__all__ = [
    "CHAT_COMPLETIONS_URL",
    "OpenRouterControlledToolPolicy",
    "OpenRouterIntegrationError",
    "OpenRouterModelPreset",
    "OpenRouterPolicyUsage",
    "OpenRouterUsageLedger",
]
