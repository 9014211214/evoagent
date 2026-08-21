from __future__ import annotations

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.models import (
    ProgramLocalRLAuthorization,
    ProgramLocalRLBindingPackage,
    ProgramLocalRLIntent,
    ProgramLocalRLResultBinding,
)
from evoagent.program_rl.package import ProgramLocalRLPackageError


def _verify_intent(intent: ProgramLocalRLIntent) -> None:
    expected_hash = program_payload_hash(
        intent.model_dump(mode="json", exclude={"intent_hash"})
    )
    if intent.intent_hash != expected_hash:
        raise ProgramLocalRLPackageError(
            "Program local-RL intent hash mismatch."
        )
    governed = set(intent.governed_actor_ids)
    if len(governed) != len(intent.governed_actor_ids) or len(governed) < 5:
        raise ProgramLocalRLPackageError(
            "Program local-RL intent governed actors are not unique."
        )
    if intent.created_by in governed:
        raise ProgramLocalRLPackageError(
            "Program local-RL intent author overlaps a governed Program role."
        )
    if intent.training_task_set_hash == intent.heldout_task_set_hash:
        raise ProgramLocalRLPackageError(
            "Program local-RL training and held-out task sets overlap."
        )
    if intent.parent_agent_identity_hash == intent.target_agent_identity_hash:
        raise ProgramLocalRLPackageError(
            "Program local-RL target identity equals its parent."
        )
    if (
        intent.optimizer_execution_authorized
        or intent.checkpoint_promotion_authorized
        or intent.production_activation_authorized
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL intent widens execution or activation authority."
        )


def _verify_authorization(
    intent: ProgramLocalRLIntent,
    authorization: ProgramLocalRLAuthorization,
) -> None:
    expected_hash = program_payload_hash(
        authorization.model_dump(
            mode="json",
            exclude={"authorization_hash"},
        )
    )
    if authorization.authorization_hash != expected_hash:
        raise ProgramLocalRLPackageError(
            "Program local-RL authorization hash mismatch."
        )
    if (
        authorization.intent_id != intent.intent_id
        or authorization.intent_hash != intent.intent_hash
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL authorization differs from its immutable intent."
        )
    if authorization.authorized_by in {
        *intent.governed_actor_ids,
        intent.created_by,
    }:
        raise ProgramLocalRLPackageError(
            "Program local-RL authorizer overlaps Program or intent actors."
        )
    if authorization.authorized_at < intent.created_at:
        raise ProgramLocalRLPackageError(
            "Program local-RL authorization predates its intent."
        )
    if (
        authorization.expires_at is not None
        and authorization.expires_at <= authorization.authorized_at
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL authorization expiry is invalid."
        )
    if (
        authorization.optimizer_execution_authorized is not True
        or authorization.checkpoint_promotion_authorized
        or authorization.production_activation_authorized
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL authorization widens promotion or activation rights."
        )


def _verify_result(
    intent: ProgramLocalRLIntent,
    authorization: ProgramLocalRLAuthorization,
    result: ProgramLocalRLResultBinding,
) -> None:
    expected_hash = program_payload_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )
    if result.result_hash != expected_hash:
        raise ProgramLocalRLPackageError(
            "Program local-RL result hash mismatch."
        )
    if (
        result.intent_id != intent.intent_id
        or result.intent_hash != intent.intent_hash
        or result.authorization_id != authorization.authorization_id
        or result.authorization_hash != authorization.authorization_hash
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL result lineage differs from intent or authorization."
        )
    if result.executed_by in {
        *intent.governed_actor_ids,
        intent.created_by,
        authorization.authorized_by,
    }:
        raise ProgramLocalRLPackageError(
            "Program local-RL executor overlaps a governed role."
        )
    if result.started_at < authorization.authorized_at:
        raise ProgramLocalRLPackageError(
            "Program local-RL execution predates optimizer authorization."
        )
    if result.completed_at < result.started_at:
        raise ProgramLocalRLPackageError(
            "Program local-RL completion predates execution start."
        )
    if (
        authorization.expires_at is not None
        and result.completed_at > authorization.expires_at
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL execution exceeded authorization expiry."
        )
    usage = result.usage
    budget = authorization.budget
    if (
        usage.iterations > budget.max_iterations
        or usage.rollouts > budget.max_rollouts
        or usage.tokens > budget.max_tokens
        or usage.cost_usd > budget.max_cost_usd + 1e-12
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL result exceeds its execution budget."
        )
    if result.initial_checkpoint_hash == result.selected_checkpoint_hash:
        raise ProgramLocalRLPackageError(
            "Program local-RL result contains no checkpoint change."
        )
    if (
        result.heldout_reward_delta <= 0.0
        or result.heldout_success_delta <= 0.0
        or result.unsafe_action_count != 0
        or result.regression_count != 0
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL result lacks strict safe held-out improvement."
        )
    if (
        result.checkpoint_promotion_authorized
        or result.production_activation_authorized
    ):
        raise ProgramLocalRLPackageError(
            "Program local-RL result widens promotion or activation authority."
        )


class ProgramLocalRLPackageManager:
    """Final base-package verifier with explicit nested recomputation."""

    @staticmethod
    def verify(package: ProgramLocalRLBindingPackage) -> bool:
        _verify_intent(package.intent)
        _verify_authorization(package.intent, package.authorization)
        _verify_result(
            package.intent,
            package.authorization,
            package.result,
        )
        if package.created_at < package.result.completed_at:
            raise ProgramLocalRLPackageError(
                "Program local-RL package predates its result."
            )
        if (
            package.external_model_call_performed_by_evoagent
            or package.foundation_model_weights_updated
            or package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
            or package.official_benchmark_claimed
        ):
            raise ProgramLocalRLPackageError(
                "Program local-RL package widens its offline evidence boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise ProgramLocalRLPackageError(
                "Program local-RL binding package hash mismatch."
            )
        return True


__all__ = ["ProgramLocalRLPackageManager"]
