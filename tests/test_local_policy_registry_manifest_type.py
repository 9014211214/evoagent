from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evoagent.local_policy import (
    SQLiteLocalPolicyRegistry,
    build_initial_local_policy_manifest,
)


FAMILY = "local-policy-family:manifest-type"
P0 = "local-policy:manifest-type:p0"


def test_initial_manifest_cannot_enter_candidate_admission(tmp_path):
    registry = SQLiteLocalPolicyRegistry(tmp_path / "local-policy.db")
    now = datetime.now(timezone.utc)
    initial = build_initial_local_policy_manifest(
        family_id=FAMILY,
        policy_id=P0,
        checkpoint_hash="1" * 64,
        optimizer_config_hash="2" * 64,
        source_commit="3" * 40,
        created_by="manifest-type-bootstrap-owner",
        created_at=now,
    )
    registry.register_initial(
        initial,
        actor_id=initial.created_by,
        now=now,
    )
    before = registry.events()

    with pytest.raises(TypeError, match="requires a candidate manifest"):
        registry.admit_candidate(
            initial,
            actor_id="manifest-type-invalid-admitter",
            now=now,
        )

    assert registry.list_versions(FAMILY)[0].policy_id == P0
    assert registry.events() == before
