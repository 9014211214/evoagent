from __future__ import annotations

from evoagent.benchmark_evidence import (
    BenchmarkComparisonPackageManager,
    SQLiteBenchmarkEvidenceRepository,
)
from evoagent.lab import AuthoritativeBenchmarkEvidenceLab


def test_complete_benchmark_evidence_lab_and_read_only_resume(tmp_path):
    lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark-lab",
        source_commit="e" * 40,
    )
    first = lab.run()
    repository = SQLiteBenchmarkEvidenceRepository(
        lab.registry_database
    )
    checkpoint = repository.checkpoint()
    second = lab.run()

    assert first.resumed is False
    assert second.resumed is True
    assert first.package_hash == second.package_hash
    assert second.a0_score == 0.25
    assert second.a1_score == 0.5
    assert second.a2_score == 0.75
    assert second.final_gain == 0.5
    assert second.best_round == 2
    assert second.monotonic_score is True
    assert second.improved_tasks == 3
    assert second.regressed_tasks == 1
    assert second.tied_tasks == 0
    assert second.comparator_score == 0.75
    assert second.anchor_rank == 1
    assert second.anchor_wins == 1
    assert second.anchor_losses == 1
    assert second.anchor_ties == 2
    assert second.mismatched_model_rejected is True
    assert second.submission_prerequisites_met_count == 0
    assert len(second.evidence_ids) == 5
    assert len(second.comparison_ids) == 2
    assert second.registry_event_count == 7
    assert repository.checkpoint() == checkpoint
    assert second.harbor_execution_performed_by_evoagent is False
    assert second.external_model_call_performed_by_evoagent is False
    assert second.checkpoint_downloaded_or_loaded is False
    assert second.upload_performed is False
    assert second.official_submission_performed is False
    assert second.official_submission_accepted is False
    assert second.production_deployment_performed is False

    package = BenchmarkComparisonPackageManager().load_file(
        lab.package_path
    )
    assert package.package_hash == second.package_hash
    assert package.synthetic_fixture is True
    assert package.audit_checkpoint.model_dump(mode="json") == (
        second.registry_checkpoint
    )
    assert len(package.runs) == 5
    assert len(package.audit_events) == 7
    assert all(
        not item.submission_prerequisites_met
        for item in package.eligibility
    )


def test_safe_package_contains_no_raw_harbor_diagnostics_or_secrets(tmp_path):
    result = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark-lab",
        source_commit="f" * 40,
    ).run()
    text = open(result.package_path, encoding="utf-8").read().lower()
    for forbidden in (
        "exception_message",
        "exception_traceback",
        "traceback (synthetic fixture only)",
        "chain_of_thought",
        "hidden_reasoning",
        "reasoning_content",
        "scratchpad",
        "private key",
        "api_key=",
        "password=",
    ):
        assert forbidden not in text
