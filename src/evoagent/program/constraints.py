from __future__ import annotations

from typing import Any

from evoagent.domain.models import FailureLayer


BOUNDED_AUTOMATIC_INTERVENTION_LAYERS = frozenset(
    {
        FailureLayer.SKILL,
        FailureLayer.ROUTER,
        FailureLayer.TOOL,
        FailureLayer.CONTEXT,
        FailureLayer.VERIFIER,
    }
)

GOVERNED_ATTRIBUTION_LAYERS = frozenset(
    {
        *BOUNDED_AUTOMATIC_INTERVENTION_LAYERS,
        FailureLayer.MODEL,
        FailureLayer.ENVIRONMENT,
    }
)


def validate_bounded_automatic_layers(
    layers: tuple[FailureLayer, ...],
) -> tuple[FailureLayer, ...]:
    """Reject automatic Program authority outside the bounded repair layers."""

    if not layers or len(set(layers)) != len(layers):
        raise ValueError("Program automatic layers must be non-empty and unique.")
    unsupported = set(layers) - BOUNDED_AUTOMATIC_INTERVENTION_LAYERS
    if unsupported:
        names = ", ".join(sorted(item.value for item in unsupported))
        raise ValueError(
            "Program automatic layers are limited to Skill, Router, Tool, "
            f"Context and Verifier; unsupported: {names}."
        )
    return layers


def validate_governed_attribution_layer(layer: FailureLayer) -> FailureLayer:
    """Allow explicit Model/Environment evidence but reject non-causal labels."""

    if layer not in GOVERNED_ATTRIBUTION_LAYERS:
        raise ValueError(
            "Program attribution requires one governed causal layer; "
            f"unsupported: {layer.value}."
        )
    return layer


def validate_single_release_package_budget(budget: Any) -> Any:
    """The current GenerationOutcome contract binds exactly one release package."""

    if getattr(budget, "max_child_packages", None) != 1:
        raise ValueError(
            "Evolution Program generations currently require exactly one release "
            "evidence package."
        )
    return budget


def validate_hardened_program_policy(policy: Any) -> Any:
    """Reject disabling safeguards required by every high-risk Program."""

    validate_bounded_automatic_layers(policy.allowed_automatic_layers)
    disabled = []
    if not policy.require_independent_attributor:
        disabled.append("independent attribution")
    if not policy.require_single_supported_experiment:
        disabled.append("single supported causal experiment")
    if not policy.require_generation_approvals:
        disabled.append("independent Generation approvals")
    if not policy.safety_feedback_requires_attribution:
        disabled.append("safety-feedback attribution")
    if disabled:
        raise ValueError(
            "High-risk Evolution Program cannot disable: "
            + ", ".join(disabled)
            + "."
        )
    return policy


__all__ = [
    "BOUNDED_AUTOMATIC_INTERVENTION_LAYERS",
    "GOVERNED_ATTRIBUTION_LAYERS",
    "validate_bounded_automatic_layers",
    "validate_governed_attribution_layer",
    "validate_hardened_program_policy",
    "validate_single_release_package_budget",
]
