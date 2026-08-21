from __future__ import annotations

import json

import pytest

from evoagent.benchmark_evidence import (
    BenchmarkComparisonPackageManager,
)
from evoagent.lab import AuthoritativeBenchmarkEvidenceLab
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
    lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark-lab",
        source_commit=commit * 40,
    )
    lab.run()
    return lab


def test_package_rejects_rehashed_run_score_rewrite(tmp_path):
    lab = _lab(tmp_path, "1")

    def mutate(payload):
        run = next(
            item
            for item in payload["runs"]
            if item["evidence_id"] == "benchmark-run:a2"
        )
        run["score"] = 1.0

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        BenchmarkComparisonPackageManager().load_file(lab.package_path)


def test_package_rejects_coherently_rehashed_model_identity_drift(tmp_path):
    lab = _lab(tmp_path, "2")

    def mutate(payload):
        run = next(
            item
            for item in payload["runs"]
            if item["evidence_id"] == "benchmark-run:comparator"
        )
        model = run["contract"]["model"]
        model["name"] = "substituted-model"
        model["identity_hash"] = canonical_sha256(
            {
                key: value
                for key, value in model.items()
                if key != "identity_hash"
            }
        )
        for trial in run["trials"]:
            trial["model_name"] = "substituted-model"
            trial["evidence_hash"] = canonical_sha256(
                {
                    key: value
                    for key, value in trial.items()
                    if key != "evidence_hash"
                }
            )
        run["contract"]["contract_hash"] = canonical_sha256(
            {
                key: value
                for key, value in run["contract"].items()
                if key != "contract_hash"
            }
        )
        run["evidence_hash"] = canonical_sha256(
            {
                key: value
                for key, value in run.items()
                if key != "evidence_hash"
            }
        )

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        BenchmarkComparisonPackageManager().load_file(lab.package_path)


def test_package_rejects_eligibility_rewrite_after_inner_and_outer_rehash(tmp_path):
    lab = _lab(tmp_path, "3")

    def mutate(payload):
        assessment = next(
            item
            for item in payload["eligibility"]
            if item["evidence_id"] == "benchmark-run:a2"
        )
        assessment["reasons"] = [
            reason
            for reason in assessment["reasons"]
            if reason != "fewer_than_five_trials_per_task"
        ]
        assessment["assessment_hash"] = canonical_sha256(
            {
                key: value
                for key, value in assessment.items()
                if key != "assessment_hash"
            }
        )

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError, match="eligibility"):
        BenchmarkComparisonPackageManager().load_file(lab.package_path)


def test_package_rejects_audit_tail_truncation_with_rewritten_checkpoint(tmp_path):
    lab = _lab(tmp_path, "4")

    def mutate(payload):
        payload["audit_events"] = payload["audit_events"][:-1]
        payload["audit_checkpoint"] = {
            "event_count": len(payload["audit_events"]),
            "head_hash": payload["audit_events"][-1]["event_hash"],
        }

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        BenchmarkComparisonPackageManager().load_file(lab.package_path)


def test_package_rejects_comparison_mode_substitution(tmp_path):
    lab = _lab(tmp_path, "5")

    def mutate(payload):
        payload["same_model_cross_agent"]["mode"] = "longitudinal"

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError):
        BenchmarkComparisonPackageManager().load_file(lab.package_path)


def test_package_rejects_partial_raw_result_hash_substitution(tmp_path):
    lab = _lab(tmp_path, "6")

    def mutate(payload):
        run = next(
            item
            for item in payload["runs"]
            if item["evidence_id"] == "benchmark-run:a0"
        )
        run["source_file_sha256"] = "f" * 64
        run["evidence_hash"] = canonical_sha256(
            {
                key: value
                for key, value in run.items()
                if key != "evidence_hash"
            }
        )

    _rewrite(lab.package_path, mutate)
    with pytest.raises(ValueError, match="import event differs"):
        BenchmarkComparisonPackageManager().load_file(lab.package_path)
