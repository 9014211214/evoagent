from __future__ import annotations

from datetime import datetime

from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import (
    AttributionReceipt,
    GenerationPlan,
    GenerationRecord,
    GenerationStatus,
    ProgramCheckpoint,
    ProgramHead,
    ProgramLearningSignal,
    ProgramState,
)
from evoagent.program_rl.models import (
    LocalRLExecutionBudget,
    LocalRLExecutionUsage,
    ProgramLocalRLAuthorization,
    ProgramLocalRLIntent,
    ProgramLocalRLResultBinding,
)


class ProgramLocalRLAdapter:
    """Create immutable offline optimizer bindings without promotion authority."""

    def build_intent(
        self,
        *,
        generation: GenerationRecord,
        head: ProgramHead,
        checkpoint: ProgramCheckpoint,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        governed_actor_ids: tuple[str, ...],
        local_rl_run_id: str,
        optimizer_config_hash: str,
        training_task_set_hash: str,
        heldout_task_set_hash: str,
        created_by: str,
        created_at: datetime,
        intent_id: str | None = None,
    ) -> ProgramLocalRLIntent:
        if (
            generation.status != GenerationStatus.RUNNING
            or generation.plan is None
            or generation.campaign_id is None
            or generation.outcome is not None
        ):
            raise ValueError(
                "Local-RL intent requires one explicitly running Program generation."
            )
        plan = generation.plan
        if (
            head.program_id != generation.program_id
            or head.state != ProgramState.GENERATION_RUNNING
            or head.active_generation_id != generation.generation_id
            or head.current_generation_index != generation.generation_index
        ):
            raise ValueError(
                "Program head does not identify the exact running generation."
            )
        if (
            signal.program_id != generation.program_id
            or signal.generation_index != generation.generation_index - 1
            or plan.source_signal_id != signal.signal_id
            or plan.source_signal_hash != signal.signal_hash
            or attribution.signal_id != signal.signal_id
            or attribution.signal_hash != signal.signal_hash
            or plan.attribution_receipt_id != attribution.receipt_id
            or plan.attribution_receipt_hash != attribution.receipt_hash
            or plan.intervention_layer != attribution.failure_layer
            or plan.intervention_action != attribution.action
        ):
            raise ValueError(
                "Running GenerationPlan, signal and Attribution lineage differ."
            )
        governed = set(governed_actor_ids)
        required_governed = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
        }
        if not required_governed.issubset(governed):
            raise ValueError(
                "Local-RL intent omits required governed Program actors."
            )
        if created_by in governed:
            raise ValueError(
                "Local-RL intent author must be independent from governed Program actors."
            )
        if created_at < max(
            generation.updated_at,
            head.updated_at,
            plan.created_at,
            signal.created_at,
            attribution.created_at,
        ):
            raise ValueError(
                "Local-RL intent time precedes its Program authorization evidence."
            )
        payload = {
            "intent_id": intent_id
            or f"program-local-rl-intent:{generation.program_id}:{generation.generation_index}",
            "program_id": generation.program_id,
            "generation_id": generation.generation_id,
            "generation_index": generation.generation_index,
            "program_head_revision": head.revision,
            "program_checkpoint": checkpoint,
            "campaign_id": generation.campaign_id,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "source_signal_id": signal.signal_id,
            "source_signal_hash": signal.signal_hash,
            "attribution_receipt_id": attribution.receipt_id,
            "attribution_receipt_hash": attribution.receipt_hash,
            "intervention_layer": plan.intervention_layer,
            "intervention_action": plan.intervention_action,
            "parent_agent_identity_hash": plan.parent_agent_identity_hash,
            "target_agent_identity_hash": plan.target_agent_identity_hash,
            "expected_release_package_hash": plan.expected_release_package_hash,
            "expected_release_plan_hash": plan.expected_release_plan_hash,
            "local_rl_run_id": local_rl_run_id,
            "optimizer_config_hash": optimizer_config_hash,
            "training_task_set_hash": training_task_set_hash,
            "heldout_task_set_hash": heldout_task_set_hash,
            "governed_actor_ids": tuple(sorted(governed_actor_ids)),
            "created_by": created_by,
            "created_at": created_at,
            "optimizer_execution_authorized": False,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return ProgramLocalRLIntent(
            **payload,
            intent_hash=program_payload_hash(payload),
        )

    def authorize(
        self,
        intent: ProgramLocalRLIntent,
        *,
        generation_plan: GenerationPlan,
        budget: LocalRLExecutionBudget,
        authorized_by: str,
        authorized_at: datetime,
        expires_at: datetime | None = None,
        authorization_id: str | None = None,
    ) -> ProgramLocalRLAuthorization:
        if (
            generation_plan.plan_id != intent.plan_id
            or generation_plan.plan_hash != intent.plan_hash
        ):
            raise ValueError(
                "Local-RL authorization GenerationPlan differs from its intent."
            )
        if authorized_by in {
            *intent.governed_actor_ids,
            intent.created_by,
        }:
            raise ValueError(
                "Local-RL execution authorizer must be independent from Program and intent actors."
            )
        if authorized_at < intent.created_at:
            raise ValueError(
                "Local-RL execution authorization precedes its immutable intent."
            )
        if (
            budget.max_tokens > generation_plan.budget.max_tokens
            or budget.max_cost_usd
            > generation_plan.budget.max_cost_usd + 1e-12
        ):
            raise ValueError(
                "Local-RL execution authorization exceeds the GenerationPlan budget."
            )
        payload = {
            "authorization_id": authorization_id
            or f"program-local-rl-authorization:{intent.intent_id}",
            "intent_id": intent.intent_id,
            "intent_hash": intent.intent_hash,
            "budget": budget,
            "authorized_by": authorized_by,
            "authorized_at": authorized_at,
            "expires_at": expires_at,
            "optimizer_execution_authorized": True,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return ProgramLocalRLAuthorization(
            **payload,
            authorization_hash=program_payload_hash(payload),
        )

    def bind_result(
        self,
        intent: ProgramLocalRLIntent,
        authorization: ProgramLocalRLAuthorization,
        *,
        local_rl_package_id: str,
        local_rl_package_hash: str,
        initial_checkpoint_hash: str,
        selected_checkpoint_hash: str,
        optimizer_evidence_hash: str,
        heldout_evaluation_hash: str,
        usage: LocalRLExecutionUsage,
        heldout_reward_delta: float,
        heldout_success_delta: float,
        unsafe_action_count: int,
        regression_count: int,
        executed_by: str,
        started_at: datetime,
        completed_at: datetime,
        result_id: str | None = None,
    ) -> ProgramLocalRLResultBinding:
        if (
            authorization.intent_id != intent.intent_id
            or authorization.intent_hash != intent.intent_hash
        ):
            raise ValueError(
                "Local-RL execution authorization differs from its Program intent."
            )
        forbidden = {
            *intent.governed_actor_ids,
            intent.created_by,
            authorization.authorized_by,
        }
        if executed_by in forbidden:
            raise ValueError(
                "Local-RL executor must be independent from Program, intent and authorization actors."
            )
        if started_at < authorization.authorized_at:
            raise ValueError(
                "Local-RL execution started before explicit optimizer authorization."
            )
        if (
            authorization.expires_at is not None
            and completed_at > authorization.expires_at
        ):
            raise ValueError("Local-RL execution completed after authorization expiry.")
        budget = authorization.budget
        if (
            usage.iterations > budget.max_iterations
            or usage.rollouts > budget.max_rollouts
            or usage.tokens > budget.max_tokens
            or usage.cost_usd > budget.max_cost_usd + 1e-12
        ):
            raise ValueError("Local-RL result exceeds its execution authorization budget.")
        if heldout_reward_delta <= 0.0 or heldout_success_delta <= 0.0:
            raise ValueError(
                "Local-RL result requires strict held-out reward and success improvement."
            )
        if unsafe_action_count != 0 or regression_count != 0:
            raise ValueError(
                "Local-RL result contains an unsafe action or held-out regression."
            )
        payload = {
            "result_id": result_id
            or f"program-local-rl-result:{intent.intent_id}",
            "intent_id": intent.intent_id,
            "intent_hash": intent.intent_hash,
            "authorization_id": authorization.authorization_id,
            "authorization_hash": authorization.authorization_hash,
            "local_rl_package_id": local_rl_package_id,
            "local_rl_package_hash": local_rl_package_hash,
            "initial_checkpoint_hash": initial_checkpoint_hash,
            "selected_checkpoint_hash": selected_checkpoint_hash,
            "optimizer_evidence_hash": optimizer_evidence_hash,
            "heldout_evaluation_hash": heldout_evaluation_hash,
            "usage": usage,
            "heldout_reward_delta": heldout_reward_delta,
            "heldout_success_delta": heldout_success_delta,
            "unsafe_action_count": unsafe_action_count,
            "regression_count": regression_count,
            "executed_by": executed_by,
            "started_at": started_at,
            "completed_at": completed_at,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return ProgramLocalRLResultBinding(
            **payload,
            result_hash=program_payload_hash(payload),
        )


__all__ = ["ProgramLocalRLAdapter"]
