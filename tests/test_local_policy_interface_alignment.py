from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from evoagent.local_policy import (
    LocalPolicyPromotionLifecycleService,
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManager,
)
from evoagent.local_policy.models import LocalPolicyCandidateManifest
from tests.test_local_policy_promotion_tamper import _completed_package


def _candidate():
    return LocalPolicyCandidateManifest.model_construct(
        family_id="local-policy-family:interface-alignment",
        candidate_id="local-policy:interface:p1",
        base_policy_id="local-policy:interface:p0",
        governed_actor_ids=("accepted-actor-a", "accepted-actor-b"),
        created_by="candidate-controller",
    )


def _record():
    return SimpleNamespace(
        family_id="local-policy-family:interface-alignment",
        policy_id="local-policy:interface:p1",
        manifest=_candidate(),
        promotion_report=SimpleNamespace(evaluator_id="promotion-evaluator"),
        promotion_decision=SimpleNamespace(decided_by="promotion-decider"),
        promotion_authorized_by="promotion-authorizer",
        activated_by="activation-executor",
        rollback_request=SimpleNamespace(requested_by="rollback-requester"),
        rollback_report=SimpleNamespace(evaluator_id="rollback-evaluator"),
        rollback_authorized_by="rollback-authorizer",
        rollback_campaign_id="campaign:rollback-interface",
    )


def test_role_guard_uses_persisted_candidate_actor_ids():
    service = object.__new__(LocalPolicyPromotionLifecycleService)
    record = _record()

    promotion_forbidden = service._forbidden(record, rollback=False)
    assert promotion_forbidden == {
        "accepted-actor-a",
        "accepted-actor-b",
        "candidate-controller",
        "promotion-evaluator",
        "promotion-decider",
        "promotion-authorizer",
    }

    rollback_forbidden = service._forbidden(record, rollback=True)
    assert rollback_forbidden == promotion_forbidden | {
        "activation-executor",
        "rollback-requester",
        "rollback-evaluator",
        "rollback-authorizer",
    }


def test_rollback_campaign_lookup_uses_from_policy_metadata():
    record = _record()

    class Registry:
        def get(self, family_id, policy_id):
            assert family_id == record.family_id
            assert policy_id == record.policy_id
            return record

    service = object.__new__(LocalPolicyPromotionLifecycleService)
    service.registry = Registry()
    campaign = SimpleNamespace(
        campaign_id=record.rollback_campaign_id,
        metadata={
            "family_id": record.family_id,
            "from_policy_id": record.policy_id,
        },
    )

    assert service._rollback_record(campaign) is record


def test_recovery_uses_safe_to_rollback_and_preserves_now_contract():
    rollback_source = inspect.getsource(
        LocalPolicyPromotionLifecycleService.submit_rollback
    )
    assert "report.safe_to_rollback" in rollback_source
    assert "report.rollback" not in rollback_source

    promotion_signature = inspect.signature(
        LocalPolicyPromotionLifecycleService.synchronize_promotion_authorization
    )
    rollback_signature = inspect.signature(
        LocalPolicyPromotionLifecycleService.synchronize_rollback_authorization
    )
    assert "now" in promotion_signature.parameters
    assert "now" in rollback_signature.parameters


def test_exact_package_reexport_is_read_only(tmp_path, monkeypatch):
    _, package = _completed_package(tmp_path, monkeypatch)
    manager = LocalPolicyPromotionPackageManager()
    path = tmp_path / "immutable-promotion-package.json"

    first = manager.export_file(package, path)
    before = path.read_bytes()
    second = manager.export_file(package, path)

    assert first == second == path
    assert path.read_bytes() == before


def test_conflicting_existing_package_is_not_overwritten(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    manager = LocalPolicyPromotionPackageManager()
    path = tmp_path / "conflicting-promotion-package.json"
    existing = b'{"foreign":"evidence"}\n'
    path.write_bytes(existing)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="differs from immutable evidence",
    ):
        manager.export_file(package, path)

    assert path.read_bytes() == existing
