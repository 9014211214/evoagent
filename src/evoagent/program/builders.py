from __future__ import annotations

from datetime import datetime

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.program.constraints import (
    validate_bounded_automatic_layers,
    validate_governed_attribution_layer,
    validate_single_release_package_budget,
)
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationBudget,
    GenerationPlan,
    ProgramBudget,
    ProgramLearningSignal,
)
from evoagent.release.package import ReleaseEvidencePackageManifest


def build_program_policy(
    *,
    policy_id: str = "multi-generation-program-v1",
    budget: ProgramBudget | None = None,
    minimum_attribution_confidence: float = 0.90,
    allowed_automatic_layers: tuple[FailureLayer, ...] = (
        FailureLayer.SKILL,
        FailureLayer.ROUTER,
        FailureLayer.TOOL,
        FailureLayer.CONTEXT,
        FailureLayer.VERIFIER,
    ),
    require_single_supported_experiment: bool = True,
    require_independent_attributor: bool = True,
    require_generation_approvals: bool = True,
    stop_on_ready: bool = True,
    safety_feedback_requires_attribution: bool = True,
    maximum_consecutive_non_improving: int = 2,
) -> EvolutionProgramPolicy:
    allowed_automatic_layers = validate_bounded_automatic_layers(
        allowed_automatic_layers
    )
    payload = {
        "policy_id": policy_id,
        "budget": budget or ProgramBudget(),
        "minimum_attribution_confidence": minimum_attribution_confidence,
        "allowed_automatic_layers": allowed_automatic_layers,
        "require_single_supported_experiment": require_single_supported_experiment,
        "require_independent_attributor": require_independent_attributor,
        "require_generation_approvals": require_generation_approvals,
        "stop_on_ready": stop_on_ready,
        "safety_feedback_requires_attribution": safety_feedback_requires_attribution,
        "maximum_consecutive_non_improving": maximum_consecutive_non_improving,
    }
    return EvolutionProgramPolicy(
        **payload,
        policy_hash=program_payload_hash(payload),
    )


def build_attribution_receipt(
    signal: ProgramLearningSignal,
    *,
    receipt_id: str,
    failure_layer: FailureLayer,
    action: EvolutionAction,
    confidence: float,
    supported_experiment_hashes: tuple[str, ...],
    attributor_id: str,
    created_at: datetime,
) -> AttributionReceipt:
    validate_governed_attribution_layer(failure_layer)
    if attributor_id == signal.evidence_producer_id:
        raise ValueError("Release evidence producer cannot attribute its own signal.")
    if created_at < signal.created_at:
        raise ValueError(
            "Attribution receipt time precedes its learning signal."
        )
    payload = {
        "receipt_id": receipt_id,
        "signal_id": signal.signal_id,
        "signal_hash": signal.signal_hash,
        "failure_layer": failure_layer,
        "action": action,
        "confidence": confidence,
        "supported_experiment_hashes": supported_experiment_hashes,
        "attributor_id": attributor_id,
        "created_at": created_at,
        "independent": True,
    }
    return AttributionReceipt(
        **payload,
        receipt_hash=program_payload_hash(payload),
    )


def build_generation_plan(
    *,
    program_id: str,
    generation_id: str,
    generation_index: int,
    parent_generation_id: str,
    signal: ProgramLearningSignal,
    attribution: AttributionReceipt,
    parent_agent_identity_hash: str,
    target_release_package: ReleaseEvidencePackageManifest,
    budget: GenerationBudget | None,
    created_by: str,
    created_at: datetime,
) -> GenerationPlan:
    validate_governed_attribution_layer(attribution.failure_layer)
    generation_budget = validate_single_release_package_budget(
        budget or GenerationBudget()
    )
    if program_id != signal.program_id:
        raise ValueError(
            "GenerationPlan Program differs from its learning signal."
        )
    if generation_index != signal.generation_index + 1:
        raise ValueError(
            "GenerationPlan must be the immediate successor of its signal."
        )
    if (
        attribution.signal_id != signal.signal_id
        or attribution.signal_hash != signal.signal_hash
    ):
        raise ValueError("Generation attribution differs from its learning signal.")
    if (
        not attribution.independent
        or attribution.attributor_id == signal.evidence_producer_id
    ):
        raise ValueError(
            "Generation attribution is not independent from evidence production."
        )
    if created_by in {signal.evidence_producer_id, attribution.attributor_id}:
        raise ValueError(
            "Generation planner must differ from evidence producer and attributor."
        )
    if created_at < max(
        signal.created_at,
        attribution.created_at,
        target_release_package.created_at,
    ):
        raise ValueError(
            "GenerationPlan time precedes its signal, attribution, or target "
            "release package."
        )
    target_agent_identity_hash = program_payload_hash(
        {
            "champion_package_hash": (
                target_release_package.champion_package.package_hash
            ),
            "snapshot_id": (
                target_release_package.plan.challenger_snapshot_id
            ),
            "runtime_config_sha256": (
                target_release_package.plan.runtime_config_sha256
            ),
            "tool_contract_sha256": (
                target_release_package.plan.tool_contract_sha256
            ),
        }
    )
    payload = {
        "plan_id": f"generation-plan:{program_id}:{generation_index}",
        "program_id": program_id,
        "generation_id": generation_id,
        "generation_index": generation_index,
        "parent_generation_id": parent_generation_id,
        "source_signal_id": signal.signal_id,
        "source_signal_hash": signal.signal_hash,
        "attribution_receipt_id": attribution.receipt_id,
        "attribution_receipt_hash": attribution.receipt_hash,
        "intervention_layer": attribution.failure_layer,
        "intervention_action": attribution.action,
        "parent_agent_identity_hash": parent_agent_identity_hash,
        "target_agent_identity_hash": target_agent_identity_hash,
        "target_runtime_config_sha256": (
            target_release_package.plan.runtime_config_sha256
        ),
        "target_tool_contract_sha256": (
            target_release_package.plan.tool_contract_sha256
        ),
        "expected_release_package_hash": target_release_package.package_hash,
        "expected_release_plan_hash": target_release_package.plan.plan_hash,
        "budget": generation_budget,
        "created_by": created_by,
        "created_at": created_at,
        "external_execution_authorized": False,
        "production_deployment_authorized": False,
    }
    return GenerationPlan(
        **payload,
        plan_hash=program_payload_hash(payload),
    )


__all__ = [
    "build_attribution_receipt",
    "build_generation_plan",
    "build_program_policy",
]
