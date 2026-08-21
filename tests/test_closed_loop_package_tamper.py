from __future__ import annotations

import json

import pytest

from evoagent.lab import ClosedLoopEvolutionSupervisorLab
from evoagent.supervisor import (
    ClosedLoopEvolutionPackageManager,
    canonical_sha256,
)


def _rewrite(path, mutate):
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_package_rejects_rehashed_run_status_rewrite(tmp_path):
    lab = ClosedLoopEvolutionSupervisorLab(
        tmp_path / "closed-loop",
        source_commit="8" * 40,
    )
    lab.run()

    def mutate(payload):
        payload["run"]["status"] = "completed"

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ClosedLoopEvolutionPackageManager().load_file(lab.package_path)


def test_package_rejects_rehashed_score_rewrite(tmp_path):
    lab = ClosedLoopEvolutionSupervisorLab(
        tmp_path / "closed-loop",
        source_commit="9" * 40,
    )
    lab.run()

    def mutate(payload):
        payload["score_summary"]["model_final_score"] = 0.5
        payload["score_summary"]["composite_final_score"] = 0.75
        payload["score_summary"]["composite_gain"] = 0.5

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ClosedLoopEvolutionPackageManager().load_file(lab.package_path)


def test_package_rejects_outcome_artifact_substitution_even_after_rehash(tmp_path):
    lab = ClosedLoopEvolutionSupervisorLab(
        tmp_path / "closed-loop",
        source_commit="a" * 40,
    )
    lab.run()

    def mutate(payload):
        skill = next(item for item in payload["cases"] if item["track"] == "skill")
        skill["outcome"]["artifact_hashes"]["skill_result"] = "f" * 64
        outcome_payload = {
            key: value
            for key, value in skill["outcome"].items()
            if key != "outcome_hash"
        }
        skill["outcome"]["outcome_hash"] = canonical_sha256(outcome_payload)

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ClosedLoopEvolutionPackageManager().load_file(lab.package_path)


def test_package_rejects_audit_tail_truncation_with_rewritten_checkpoint(tmp_path):
    lab = ClosedLoopEvolutionSupervisorLab(
        tmp_path / "closed-loop",
        source_commit="b" * 40,
    )
    lab.run()

    def mutate(payload):
        payload["events"] = payload["events"][:-1]
        payload["checkpoint"] = {
            "event_count": len(payload["events"]),
            "head_hash": payload["events"][-1]["event_hash"],
        }

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ClosedLoopEvolutionPackageManager().load_file(lab.package_path)


def test_package_rejects_budget_rewrite_below_persisted_tracks(tmp_path):
    lab = ClosedLoopEvolutionSupervisorLab(
        tmp_path / "closed-loop",
        source_commit="c" * 40,
    )
    lab.run()

    def mutate(payload):
        payload["policy"]["budget"]["max_skill_executions"] = 0
        payload["run"]["policy"]["budget"]["max_skill_executions"] = 0

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        ClosedLoopEvolutionPackageManager().load_file(lab.package_path)
