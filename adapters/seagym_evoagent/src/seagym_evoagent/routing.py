"""Single source of truth for the frozen OpenRouter route contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_ROUTE_CONTRACT: dict[str, Any] = {
    "provider": {
        "only": ["xiaomi/fp8"],
        "allow_fallbacks": False,
        "require_parameters": True,
    },
    "reasoning": {"enabled": False},
    "accepted_response_models": ["xiaomi/mimo-v2.5", "xiaomi/mimo-v2.5-20260422"],
    "response_provider": "Xiaomi",
}


def expected_route_contract() -> dict[str, Any]:
    return deepcopy(_ROUTE_CONTRACT)


def validate_route_contract(raw: Any) -> dict[str, Any]:
    if raw != _ROUTE_CONTRACT:
        raise ValueError("route_contract does not match the frozen Xiaomi fp8/no-fallback/no-reasoning route")
    return deepcopy(_ROUTE_CONTRACT)
