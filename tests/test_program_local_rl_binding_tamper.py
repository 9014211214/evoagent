from __future__ import annotations

import pytest

from evoagent.local_rl import (
    LocalRLPackageManager,
    LocalRLRegistryCheckpoint,
    LocalRLEventType,
    ProgramLocalRLBindingError,
    SQLiteLocalRLRepository,
)
from evoagent.model_registry.models import canonical_sha256
from tests.test_program_local_rl_binding import bound_context


def _rehash_ticket(ticket):
    payload = ticket.model_dump(mode="json", exclude={"ticket_hash"})
    return ticket.model_copy(
        update={"ticket_hash": canonical_sha256(payload)}
    )


def _rehash_bound_package(package):
    payload = package.model_dump(mode="json", exclude={"package_hash"})
    return package.model_copy(
        update={"package_hash": canonical_sha256(payload)}
    )


def _budget_snapshot_hash(policy, head):
    budget = policy.budget
    return canonical_sha256(
        {
            "policy_budget": budget.model_dump(mode="json"),
            "head": {
                "current_generation_index": head.current_generation_index,
                "rollback_count": head.rollback_count,
                "hold_count": head.hold_count,
                "generation_campaign_count": (
                    head.generation_campaign_count
                ),
                "total_pairs": head.total_pairs,
                "total_tokens": head.total_tokens,
                "total_cost_usd": head.total_cost_usd,
            },
            "remaining": {
                "generations": budget.max_generations
                - (head.current_generation_index + 1),
                "rollbacks": budget.max_rollbacks - head.rollback_count,
                "holds": budget.max_holds - head.hold_count,
                "generation_campaigns": budget.max_generation_campaigns
                - head.generation_campaign_count,
                "pairs": budget.max_total_pairs - head.total_pairs,
                "tokens": budget.max_total_tokens - head.total_tokens,
                "cost_usd": budget.max_total_cost_usd
                - head.total_cost_usd,
            },
        }
    )


def _rehash_local_audit(package, *, replacement_trainer):
    previous_hash = "0" * 64
    events = []
    for event in package.audit_events:
        actor_id = (
            replacement_trainer
            if event.event_type == LocalRLEventType.TRAINING_COMPLETED
            else event.actor_id
        )
        event_hash = SQLiteLocalRLRepository._event_hash(
            sequence=event.sequence,
            event_id=event.event_id,
            event_type=event.event_type,
            run_id=event.run_id,
            actor_id=actor_id,
            reason=event.reason,
            payload=event.payload,
            created_at=event.created_at,
            previous_hash=previous_hash,
        )
        events.append(
            event.model_copy(
                update={
                    "actor_id": actor_id,
                    "previous_hash": previous_hash,
                    "event_hash": event_hash,
                }
            )
        )
        previous_hash = event_hash
    checkpoint = LocalRLRegistryCheckpoint(
        event_count=len(events),
        head_hash=previous_hash,
    )
    forged = package.model_copy(
        update={
            "audit_events": tuple(events),
            "audit_checkpoint": checkpoint,
        }
    )
    payload = forged.model_dump(mode="json", exclude={"package_hash"})
    return forged.model_copy(
        update={"package_hash": canonical_sha256(payload)}
    )


def test_rehashed_ticket_cannot_authorize_checkpoint_activation(bound_context):
    ticket = bound_context["ticket"].model_copy(
        update={"checkpoint_activation_authorized": True}
    )
    ticket = _rehash_ticket(ticket)

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="widens immutable authority",
    ):
        bound_context["manager"].verify_ticket(ticket)


def test_rehashed_outer_package_cannot_authorize_release(bound_context):
    package = bound_context["bound_package"].model_copy(
        update={"release_authorized": True}
    )
    package = _rehash_bound_package(package)

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="widens immutable authority",
    ):
        bound_context["manager"].verify(package)


def test_rehashed_local_package_cannot_claim_foundation_training(bound_context):
    local_package = bound_context["local_package"].model_copy(
        update={"foundation_model_training_performed": True}
    )
    local_payload = local_package.model_dump(
        mode="json",
        exclude={"package_hash"},
    )
    local_package = local_package.model_copy(
        update={"package_hash": canonical_sha256(local_payload)}
    )
    outer = bound_context["bound_package"].model_copy(
        update={"local_rl_package": local_package}
    )
    outer = _rehash_bound_package(outer)

    assert LocalRLPackageManager().verify(local_package) is True
    with pytest.raises(
        ProgramLocalRLBindingError,
        match="widens tiny-policy or execution authority",
    ):
        bound_context["manager"].verify(outer)


def test_rehashed_local_audit_cannot_use_program_approver_as_trainer(
    bound_context,
):
    approver = bound_context["ticket"].approvals[0].actor_id
    local_package = _rehash_local_audit(
        bound_context["local_package"],
        replacement_trainer=approver,
    )
    outer = bound_context["bound_package"].model_copy(
        update={"local_rl_package": local_package}
    )
    outer = _rehash_bound_package(outer)

    assert LocalRLPackageManager().verify(local_package) is True
    with pytest.raises(
        ProgramLocalRLBindingError,
        match="audit actors differ|not independent",
    ):
        bound_context["manager"].verify(outer)


def test_rehashed_ticket_budget_snapshot_cannot_hide_exhausted_capacity(
    bound_context,
):
    ticket = bound_context["ticket"]
    budget = ticket.policy.budget
    exhausted_head = ticket.head.model_copy(
        update={
            "total_pairs": budget.max_total_pairs,
            "total_tokens": budget.max_total_tokens,
            "total_cost_usd": budget.max_total_cost_usd,
        }
    )
    forged = ticket.model_copy(
        update={
            "head": exhausted_head,
            "cumulative_program_budget_snapshot_hash": (
                _budget_snapshot_hash(ticket.policy, exhausted_head)
            ),
        }
    )
    forged = _rehash_ticket(forged)

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="remaining Program budget",
    ):
        bound_context["manager"].verify_ticket(forged)
