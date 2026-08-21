from __future__ import annotations

import pytest

from evoagent.benchmark_evidence import (
    BenchmarkComparator,
    BenchmarkComparisonError,
    BenchmarkComparisonPackageManager,
    BenchmarkEvidenceSource,
    BenchmarkRunRole,
    HarborResultImporter,
    assess_submission_eligibility,
    build_run_contract,
)
from evoagent.lab import AuthoritativeBenchmarkEvidenceLab


def _package(tmp_path):
    lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark-lab",
        source_commit="b" * 40,
    )
    result = lab.run()
    package = BenchmarkComparisonPackageManager().load_file(
        result.package_path
    )
    return lab, result, package


def test_longitudinal_report_tracks_a0_to_an_and_task_regression(tmp_path):
    _, result, package = _package(tmp_path)
    report = package.longitudinal
    assert [point.evolution_round for point in report.points] == [0, 1, 2]
    assert [point.score for point in report.points] == [0.25, 0.5, 0.75]
    assert [point.gain_from_baseline for point in report.points] == [
        0.0,
        0.25,
        0.5,
    ]
    assert report.best_round == 2
    assert report.final_round == 2
    assert report.final_gain == 0.5
    assert report.improved_tasks == 3
    assert report.regressed_tasks == 1
    assert report.tied_tasks == 0
    assert report.monotonic_score is True
    assert report.downward_round_count == 0
    assert report.error_rate_delta == -0.25
    assert result.a0_score == report.baseline_score
    assert result.a2_score == report.final_score


def test_same_model_cross_agent_report_verifies_model_and_pairwise_tasks(tmp_path):
    _, result, package = _package(tmp_path)
    report = package.same_model_cross_agent
    assert report.same_model_verified is True
    assert len(report.ranking) == 2
    assert report.ranking[0].run_id == report.anchor_run_id
    assert report.ranking[0].score == 0.75
    assert report.ranking[1].score == 0.75
    pairwise = report.pairwise[0]
    assert pairwise.wins == 1
    assert pairwise.losses == 1
    assert pairwise.ties == 2
    assert pairwise.score_delta == 0.0
    assert result.anchor_rank == 1
    assert result.mismatched_model_rejected is True


def test_same_model_comparison_rejects_exact_model_identity_mismatch(tmp_path):
    _, _, package = _package(tmp_path)
    by_id = {item.evidence_id: item for item in package.runs}
    with pytest.raises(BenchmarkComparisonError, match="Model identity mismatch"):
        BenchmarkComparator().same_model_cross_agent(
            (by_id["benchmark-run:a2"], by_id["benchmark-run:mismatch"]),
            anchor_run_id="benchmark-run:a2",
            comparison_id="comparison:model-mismatch",
        )


def test_longitudinal_comparison_rejects_frozen_reasoning_setting_drift(tmp_path):
    lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark-lab",
        source_commit="c" * 40,
    )
    suite = lab._suite()
    contracts = lab._contracts(suite)
    payloads = lab._fixture_payloads(contracts)
    hashes = lab._write_or_verify_fixtures(payloads)
    importer = HarborResultImporter(lab.fixtures_root)
    a0 = importer.import_file(
        "a0/result.json",
        expected_sha256=hashes["a0"],
        evidence_id="benchmark-run:a0",
        contract=contracts["a0"],
    )
    a1 = importer.import_file(
        "a1/result.json",
        expected_sha256=hashes["a1"],
        evidence_id="benchmark-run:a1",
        contract=contracts["a1"],
    )
    drifted_contract = build_run_contract(
        contract_id="benchmark-contract:a2-drifted",
        role=BenchmarkRunRole.EVOLVED,
        suite=suite,
        agent=contracts["a2"].agent,
        model=contracts["a2"].model,
        reasoning_effort="high",
        trials_per_task=1,
        max_wall_seconds=3600,
        max_cost_usd=1.0,
        source=BenchmarkEvidenceSource.SYNTHETIC_FIXTURE,
        default_execution_settings_attested=True,
    )
    a2_drifted = importer.import_file(
        "a2/result.json",
        expected_sha256=hashes["a2"],
        evidence_id="benchmark-run:a2-drifted",
        contract=drifted_contract,
    )
    with pytest.raises(BenchmarkComparisonError, match="frozen benchmark"):
        BenchmarkComparator().longitudinal(
            (a0, a1, a2_drifted),
            comparison_id="comparison:drifted-reasoning",
        )


def test_synthetic_fixture_never_meets_official_submission_prerequisites(tmp_path):
    _, result, package = _package(tmp_path)
    assessments = {
        item.evidence_id: item for item in package.eligibility
    }
    assert len(assessments) == 5
    assert result.submission_prerequisites_met_count == 0
    for run in package.runs:
        assessment = assess_submission_eligibility(run)
        assert assessment == assessments[run.evidence_id]
        assert assessment.submission_prerequisites_met is False
        assert assessment.synthetic_fixture is True
        assert "synthetic_fixture_is_not_submission_evidence" in assessment.reasons
        assert "canonical_task_manifest_not_attested" in assessment.reasons
        assert "fewer_than_five_trials_per_task" in assessment.reasons
        assert assessment.official_submission_performed is False
        assert assessment.official_submission_accepted is False
