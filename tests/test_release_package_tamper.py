import json

import pytest

from evoagent.lab import ShadowCanaryReleaseLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.release import ReleaseEvidencePackageManager


def _package(tmp_path, *, scenario="drift"):
    result = ShadowCanaryReleaseLab(
        tmp_path / "release-lab", source_commit="f" * 40
    ).run()
    return (
        result.drift.package_path
        if scenario == "drift"
        else result.passing.package_path
    )


def _rewrite(path, mutate):
    payload = json.loads(open(path, encoding="utf-8").read())
    mutate(payload)
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def test_package_rejects_policy_rewrite_with_outer_rehash(tmp_path):
    path = _package(tmp_path)

    def mutate(payload):
        policy = payload["plan"]["policy"]
        policy["maximum_safety_violations"] = 99
        policy["policy_hash"] = canonical_sha256(
            {key: value for key, value in policy.items() if key != "policy_hash"}
        )
        plan = payload["plan"]
        plan["plan_hash"] = canonical_sha256(
            {key: value for key, value in plan.items() if key != "plan_hash"}
        )

    _rewrite(path, mutate)
    with pytest.raises(ValueError):
        ReleaseEvidencePackageManager().load_file(path)


def test_package_rejects_release_approval_identity_substitution(tmp_path):
    path = _package(tmp_path)

    def mutate(payload):
        payload["release_approvals"][0]["actor_id"] = "substituted-reviewer"

    _rewrite(path, mutate)
    with pytest.raises(ValueError, match="approval identity"):
        ReleaseEvidencePackageManager().load_file(path)


def test_package_rejects_primary_pointer_rewrite(tmp_path):
    path = _package(tmp_path, scenario="passing")

    def mutate(payload):
        payload["final_head"]["primary_snapshot_id"] = "evoagent-a1"

    _rewrite(path, mutate)
    with pytest.raises(ValueError):
        ReleaseEvidencePackageManager().load_file(path)


def test_package_rejects_release_audit_content_and_tail_changes(tmp_path):
    path = _package(tmp_path)

    def mutate_event(payload):
        payload["release_events"][0]["reason"] = "modified"

    _rewrite(path, mutate_event)
    with pytest.raises(ValueError):
        ReleaseEvidencePackageManager().load_file(path)

    path = _package(tmp_path / "tail")

    def truncate(payload):
        payload["release_events"] = payload["release_events"][:-1]

    _rewrite(path, truncate)
    with pytest.raises(ValueError, match="checkpoint"):
        ReleaseEvidencePackageManager().load_file(path)


def test_package_rejects_coherent_release_audit_tail_truncation(tmp_path):
    path = _package(tmp_path)

    def truncate_and_reanchor(payload):
        payload["release_events"] = payload["release_events"][:-1]
        payload["release_checkpoint"] = {
            "event_count": len(payload["release_events"]),
            "head_hash": payload["release_events"][-1]["event_hash"],
        }

    _rewrite(path, truncate_and_reanchor)
    with pytest.raises(ValueError, match="lifecycle event sequence"):
        ReleaseEvidencePackageManager().load_file(path)


def test_package_rejects_coherent_campaign_audit_tail_truncation(tmp_path):
    path = _package(tmp_path)

    def truncate_and_reanchor(payload):
        payload["campaign_events"] = payload["campaign_events"][:-1]
        payload["campaign_checkpoint"] = {
            "event_count": len(payload["campaign_events"]),
            "head_hash": payload["campaign_events"][-1]["event_hash"],
        }

    _rewrite(path, truncate_and_reanchor)
    with pytest.raises(ValueError, match="Campaign lifecycle event sequence"):
        ReleaseEvidencePackageManager().load_file(path)


def test_package_rejects_campaign_fingerprint_rewrite(tmp_path):
    path = _package(tmp_path)

    def mutate(payload):
        payload["rollback_campaign"]["fingerprint"] = "0" * 64

    _rewrite(path, mutate)
    with pytest.raises(ValueError, match="Rollback Campaign"):
        ReleaseEvidencePackageManager().load_file(path)