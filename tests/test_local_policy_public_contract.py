from evoagent.campaigns import CampaignType
from evoagent.lab import AcceptedLocalPolicyPromotionLab
from evoagent.local_policy import (
    LocalPolicyPromotionLifecycleService,
    LocalPolicyPromotionPackageManager,
    LocalPolicyPromotionPackageManifest,
    SQLiteLocalPolicyRegistry,
    StaleLocalPolicyRevision,
    build_candidate_from_accepted_evidence,
    build_initial_local_policy_manifest,
)


def test_local_policy_public_api_exposes_final_governed_lifecycle():
    assert SQLiteLocalPolicyRegistry.__module__ == (
        "evoagent.local_policy.repository_chronology_final"
    )
    assert LocalPolicyPromotionLifecycleService.__module__ == (
        "evoagent.local_policy.lifecycle_recovery_final"
    )
    assert LocalPolicyPromotionPackageManager.__module__ == (
        "evoagent.local_policy.package_semantic_final"
    )
    assert LocalPolicyPromotionPackageManifest.__module__ == (
        "evoagent.local_policy.package"
    )
    assert StaleLocalPolicyRevision.__module__ == (
        "evoagent.local_policy.repository"
    )
    assert AcceptedLocalPolicyPromotionLab.__module__ == (
        "evoagent.lab.local_policy_promotion_final"
    )
    assert build_candidate_from_accepted_evidence.__module__ == (
        "evoagent.local_policy.builders"
    )
    assert build_initial_local_policy_manifest.__module__ == (
        "evoagent.local_policy.builders"
    )


def test_campaign_contract_has_separate_promotion_and_rollback_types():
    assert CampaignType.LOCAL_POLICY_PROMOTION.value == (
        "local_policy_promotion"
    )
    assert CampaignType.LOCAL_POLICY_ROLLBACK.value == (
        "local_policy_rollback"
    )
    assert CampaignType.LOCAL_POLICY_PROMOTION != (
        CampaignType.LOCAL_POLICY_ROLLBACK
    )
