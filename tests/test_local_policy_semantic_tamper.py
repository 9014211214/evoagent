from __future__ import annotations

from datetime import timedelta

import pytest

from evoagent.local_policy import (
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManager,
    LocalPolicyRegistryCheckpoint,
    SQLiteLocalPolicyRegistry,
)
from evoagent.model_registry.models import canonical_sha256
from tests.test_local_policy_promotion_tamper import (
    _completed_package,
    _rehash_package,
)


def _rewrite_events(package, mutate):
    previous = "0" * 64
    events = []
    for event in package.local_policy_events:
        updates = mutate(event)
        candidate = event.model_copy(update=updates)
        event_hash = SQLiteLocalPolicyRegistry._event_hash(
            sequence=candidate.sequence,
            event_id=candidate.event_id,
            event_type=candidate.event_type,
            family_id=candidate.family_id,
            policy_id=candidate.policy_id,
            from_policy_id=candidate.from_policy_id,
            to_policy_id=candidate.to_policy_id,
            reason=candidate.reason,
            metadata=candidate.metadata,
            actor_id=candidate.actor_id,
            created_at=candidate.created_at,
            previous_hash=previous,
        )
        candidate = candidate.model_copy(
            update={
                "previous_hash": previous,
                "event_hash": event_hash,
            }
        )
        events.append(candidate)
        previous = event_hash
    forged = package.model_copy(
        update={
            "local_policy_events": tuple(events),
            "local_policy_checkpoint": LocalPolicyRegistryCheckpoint(
                event_count=len(events),
                head_hash=previous,
            ),
        }
    )
    return _rehash_package(forged)


def test_coherently_rehashed_activation_revision_metadata_is_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)

    forged = _rewrite_events(
        package,
        lambda event: (
            {
                "metadata": {
                    **event.metadata,
                    "active_revision_before": 99,
                }
            }
            if event.event_type.value == "activated"
            else {}
        ),
    )

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="audit metadata differs",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_coherently_rehashed_nonmonotonic_local_audit_time_is_rejected(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    first_time = package.local_policy_events[0].created_at

    forged = _rewrite_events(
        package,
        lambda event: (
            {"created_at": first_time - timedelta(seconds=1)}
            if event.event_type.value == "activated"
            else {}
        ),
    )

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="timestamps are not monotonic",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)
