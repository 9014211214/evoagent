from __future__ import annotations

from datetime import timezone

import pytest

from evoagent.local_policy import (
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManager,
    LocalPolicyRegistryCheckpoint,
    SQLiteLocalPolicyRegistry,
)
from evoagent.model_registry.models import canonical_sha256
from tests.test_local_policy_promotion_lifecycle import (
    P1,
    _activate,
    _authorize_promotion,
    _authorize_rollback,
    _build_full_package,
    _promotion_context,
)


def _completed_package(tmp_path, monkeypatch):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    rollback_campaign = _authorize_rollback(context)
    context["service"].rollback(
        family_id=context["initial"].family_id,
        from_policy_id=P1,
        to_policy_id=context["initial"].policy_id,
        campaign_id=rollback_campaign.campaign_id,
        expected_active_revision=1,
        actor_id="independent-local-policy-rollback-executor",
    )
    return context, _build_full_package(context)


def _rehash_package(package):
    payload = package.model_dump(mode="json", exclude={"package_hash"})
    return package.model_copy(
        update={"package_hash": canonical_sha256(payload)}
    )


def test_coherently_rehashed_local_policy_audit_semantics_are_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    previous = "0" * 64
    forged_events = []
    for event in package.local_policy_events:
        reason = (
            "coherently forged authorization semantics"
            if event.event_type.value == "authorized"
            else event.reason
        )
        event_hash = SQLiteLocalPolicyRegistry._event_hash(
            sequence=event.sequence,
            event_id=event.event_id,
            event_type=event.event_type,
            family_id=event.family_id,
            policy_id=event.policy_id,
            from_policy_id=event.from_policy_id,
            to_policy_id=event.to_policy_id,
            reason=reason,
            metadata=event.metadata,
            actor_id=event.actor_id,
            created_at=event.created_at,
            previous_hash=previous,
        )
        forged = event.model_copy(
            update={
                "reason": reason,
                "previous_hash": previous,
                "event_hash": event_hash,
            }
        )
        forged_events.append(forged)
        previous = event_hash
    forged = package.model_copy(
        update={
            "local_policy_events": tuple(forged_events),
            "local_policy_checkpoint": LocalPolicyRegistryCheckpoint(
                event_count=len(forged_events),
                head_hash=previous,
            ),
        }
    )
    forged = _rehash_package(forged)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="audit reasons differ",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_rehashed_candidate_checkpoint_substitution_is_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    candidate = package.candidate_record.manifest
    forged_payload = candidate.model_dump(
        mode="json",
        exclude={"manifest_hash"},
    )
    forged_payload["selected_checkpoint_hash"] = "a" * 64
    forged_candidate = candidate.model_copy(
        update={
            "selected_checkpoint_hash": "a" * 64,
            "manifest_hash": canonical_sha256(forged_payload),
        }
    )
    forged_record = package.candidate_record.model_copy(
        update={"manifest": forged_candidate}
    )
    forged = package.model_copy(
        update={"candidate_record": forged_record}
    )
    forged = _rehash_package(forged)

    with pytest.raises(
        (LocalPolicyPromotionPackageError, ValueError),
        match="accepted evidence|candidate",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_rehashed_promotion_approval_role_substitution_is_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    evaluator = package.candidate_record.promotion_report.evaluator_id
    first = package.promotion_approvals[0].model_copy(
        update={"actor_id": evaluator}
    )
    forged = package.model_copy(
        update={
            "promotion_approvals": (
                first,
                package.promotion_approvals[1],
            )
        }
    )
    forged = _rehash_package(forged)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="approvals are incomplete|non-independent",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_package_cannot_claim_foundation_weight_update_or_production_activation(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    forged = package.model_copy(
        update={
            "foundation_model_weights_updated": True,
            "production_activation_performed": True,
        }
    )
    forged = _rehash_package(forged)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="widens its local evidence boundary",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)
