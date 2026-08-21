from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.local_policy import (
    LocalPolicyPromotionLifecycleService,
    SQLiteLocalPolicyRegistry,
    build_initial_local_policy_manifest,
)
from tests.test_program_local_rl_full_lineage import _full_lineage


FAMILY = "local-policy-family:parent-provenance"
P0 = "local-policy:parent:p0"
P1 = "local-policy:parent:p1"


def _base(package):
    return (
        package.runtime_attested_package
        .schema_attested_package
        .attested_package
        .base_package
    )


@pytest.mark.parametrize(
    ("optimizer_config_hash", "source_commit"),
    (
        ("f" * 64, None),
        (None, "e" * 40),
    ),
)
def test_candidate_rejects_parent_optimizer_or_source_substitution(
    tmp_path,
    monkeypatch,
    optimizer_config_hash,
    source_commit,
):
    _, _, accepted, anchors, receipt = _full_lineage(
        tmp_path / "accepted-evidence",
        monkeypatch,
    )
    base = _base(accepted)
    registry = SQLiteLocalPolicyRegistry(tmp_path / "local-policy.db")
    campaigns = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    service = LocalPolicyPromotionLifecycleService(
        registry,
        CampaignGovernanceService(campaigns),
    )
    created_at = max(
        receipt.accepted_at + timedelta(seconds=1),
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    initial = build_initial_local_policy_manifest(
        family_id=FAMILY,
        policy_id=P0,
        checkpoint_hash=base.result.initial_checkpoint_hash,
        optimizer_config_hash=(
            optimizer_config_hash or base.intent.optimizer_config_hash
        ),
        source_commit=source_commit or base.source_commit,
        created_by="parent-provenance-bootstrap-owner",
        created_at=created_at,
    )
    registry.register_initial(
        initial,
        actor_id=initial.created_by,
        now=created_at,
    )

    with pytest.raises(
        ValueError,
        match="optimizer configuration or source commit differs",
    ):
        service.admit_candidate(
            accepted,
            anchors,
            receipt,
            family_id=FAMILY,
            candidate_id=P1,
            base_policy_id=P0,
            created_by="parent-provenance-candidate-controller",
            created_at=created_at + timedelta(seconds=1),
        )

    versions = registry.list_versions(FAMILY)
    assert len(versions) == 1
    assert versions[0].policy_id == P0
    assert len(registry.events()) == 1
