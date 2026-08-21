from __future__ import annotations

from evoagent.local_policy import (
    LocalPolicyPromotionPackageManager,
    LocalPolicyVersionStatus,
)
from tests.test_local_policy_promotion_lifecycle import (
    FAMILY,
    P0,
    P1,
    _activate,
    _authorize_promotion,
    _build_full_package,
    _promotion_context,
)


def test_promotion_only_package_is_reproducible(tmp_path, monkeypatch):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)

    package = _build_full_package(context)

    assert LocalPolicyPromotionPackageManager.verify(package) is True
    assert package.rollback_campaign is None
    assert package.rollback_approvals == ()
    assert package.final_head.active_policy_id == P1
    assert package.final_head.revision == 1
    assert package.initial_record.policy_id == P0
    assert package.initial_record.status == LocalPolicyVersionStatus.SUPERSEDED
    assert package.candidate_record.policy_id == P1
    assert package.candidate_record.status == LocalPolicyVersionStatus.ACTIVE
    assert package.candidate_record.rollback_request is None
    assert package.candidate_record.rollback_report is None
    assert package.candidate_record.rollback_campaign_id is None
