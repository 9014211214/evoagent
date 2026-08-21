from __future__ import annotations

from pathlib import Path

from evoagent.campaigns import SQLiteCampaignRepository
from evoagent.lab import AcceptedLocalPolicyPromotionLab
from evoagent.local_policy import (
    LocalPolicyPromotionPackageManager,
    SQLiteLocalPolicyRegistry,
)
from tests.test_program_local_rl_full_lineage import _full_lineage


def test_accepted_local_policy_promotion_lab_runs_and_resumes_read_only(
    tmp_path,
    monkeypatch,
):
    _, _, accepted, anchors, receipt = _full_lineage(
        tmp_path / "accepted-evidence",
        monkeypatch,
    )
    lab = AcceptedLocalPolicyPromotionLab(
        tmp_path / "local-policy-lab",
        accepted_program_package=accepted,
        trusted_anchors=anchors,
        acceptance_receipt=receipt,
        source_commit="f" * 40,
        perform_rollback=True,
    )

    first = lab.run()
    package_before = Path(first.package_path).read_bytes()
    manager = LocalPolicyPromotionPackageManager()
    packaged_before = manager.load_file(first.package_path)
    registry = SQLiteLocalPolicyRegistry(lab.registry_path)
    campaigns = SQLiteCampaignRepository(lab.campaign_path)
    registry_before = (
        registry.head(first.family_id),
        tuple(registry.list_versions(first.family_id)),
        tuple(registry.events()),
        registry.checkpoint(),
    )
    campaign_before = (
        tuple(campaigns.audit_events()),
        campaigns.checkpoint(),
    )

    second = lab.run()

    assert first.resumed is False
    assert second.resumed is True
    assert first.package_hash == second.package_hash
    assert first.package_path == second.package_path
    assert first.active_policy_id == lab.initial_policy_id
    assert first.active_revision == 2
    assert first.promotion_completed is True
    assert first.rollback_completed is True
    assert first.local_policy_event_count == 8
    assert first.campaign_event_count > 0
    assert first.local_policy_pointer_mutation_only is True
    assert first.foundation_model_weights_updated is False
    assert first.production_activation_performed is False
    assert first.production_deployment_performed is False
    assert Path(second.package_path).read_bytes() == package_before
    assert manager.load_file(second.package_path) == packaged_before
    assert (
        registry.head(first.family_id),
        tuple(registry.list_versions(first.family_id)),
        tuple(registry.events()),
        registry.checkpoint(),
    ) == registry_before
    assert (
        tuple(campaigns.audit_events()),
        campaigns.checkpoint(),
    ) == campaign_before


def test_accepted_local_policy_promotion_lab_supports_promotion_only(
    tmp_path,
    monkeypatch,
):
    _, _, accepted, anchors, receipt = _full_lineage(
        tmp_path / "promotion-only-evidence",
        monkeypatch,
    )
    lab = AcceptedLocalPolicyPromotionLab(
        tmp_path / "promotion-only-lab",
        accepted_program_package=accepted,
        trusted_anchors=anchors,
        acceptance_receipt=receipt,
        source_commit="e" * 40,
        perform_rollback=False,
    )

    result = lab.run()

    assert result.active_policy_id == lab.candidate_policy_id
    assert result.active_revision == 1
    assert result.promotion_completed is True
    assert result.rollback_completed is False
    assert result.local_policy_event_count == 5
