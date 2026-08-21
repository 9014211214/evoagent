import json

import pytest

from evoagent.champion import ChampionDecisionPackageManager
from evoagent.lab import BenchmarkGatedChampionLab
from evoagent.model_registry.models import canonical_sha256


def _rehash_outer(payload: dict) -> None:
    payload["package_hash"] = canonical_sha256(
        {
            key: value
            for key, value in payload.items()
            if key != "package_hash"
        }
    )


def _rewrite(path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    _rehash_outer(payload)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _lab(tmp_path, commit: str):
    lab = BenchmarkGatedChampionLab(
        tmp_path / "champion-lab",
        source_commit=commit * 40,
    )
    lab.run()
    return lab


def _rehash_policy(policy: dict) -> None:
    policy["policy_hash"] = canonical_sha256(
        {key: value for key, value in policy.items() if key != "policy_hash"}
    )


def _rehash_decision(decision: dict) -> None:
    decision["decision_hash"] = canonical_sha256(
        {
            key: value
            for key, value in decision.items()
            if key != "decision_hash"
        }
    )


def test_package_rejects_coherently_rehashed_policy_threshold_change(tmp_path):
    lab = _lab(tmp_path, "d")

    def mutate(payload):
        payload["policy"]["maximum_regressed_tasks"] = 1
        _rehash_policy(payload["policy"])
        payload["decision"]["policy"] = dict(payload["policy"])
        _rehash_decision(payload["decision"])

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ChampionDecisionPackageManager().load_file(lab.package_path)


def test_package_rejects_active_pointer_rewrite(tmp_path):
    lab = _lab(tmp_path, "e")

    def mutate(payload):
        payload["active_snapshot_id"] = "evoagent-a0"

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError, match="Registry records"):
        ChampionDecisionPackageManager().load_file(lab.package_path)


def test_package_rejects_approval_identity_substitution(tmp_path):
    lab = _lab(tmp_path, "f")

    def mutate(payload):
        payload["approvals"][0]["actor_id"] = "substituted-reviewer"

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ChampionDecisionPackageManager().load_file(lab.package_path)


def test_package_rejects_champion_audit_tail_truncation_with_rewritten_checkpoint(tmp_path):
    lab = _lab(tmp_path, "1")

    def mutate(payload):
        payload["champion_events"] = payload["champion_events"][:-1]
        payload["champion_checkpoint"] = {
            "event_count": len(payload["champion_events"]),
            "head_hash": payload["champion_events"][-1]["event_hash"],
        }

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ChampionDecisionPackageManager().load_file(lab.package_path)


def test_package_rejects_selected_rejected_round_substitution(tmp_path):
    lab = _lab(tmp_path, "2")

    def mutate(payload):
        decision = payload["decision"]
        decision["selected_run_id"] = "benchmark-run:a2"
        decision["selected_snapshot_id"] = "evoagent-a2"
        decision["selected_round"] = 2
        _rehash_decision(decision)

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ChampionDecisionPackageManager().load_file(lab.package_path)


def test_package_rejects_rehashed_bootstrap_confidence_interval(tmp_path):
    lab = _lab(tmp_path, "3")

    def mutate(payload):
        assessment = payload["decision"]["assessments"][0]
        bootstrap = assessment["bootstrap"]
        bootstrap["lower_bound"] = 0.5
        bootstrap["evidence_hash"] = canonical_sha256(
            {
                key: value
                for key, value in bootstrap.items()
                if key != "evidence_hash"
            }
        )
        assessment["assessment_hash"] = canonical_sha256(
            {
                key: value
                for key, value in assessment.items()
                if key != "assessment_hash"
            }
        )
        _rehash_decision(payload["decision"])

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ChampionDecisionPackageManager().load_file(lab.package_path)
