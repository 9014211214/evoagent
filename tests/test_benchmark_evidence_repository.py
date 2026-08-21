from __future__ import annotations

import sqlite3

import pytest

from evoagent.benchmark_evidence import (
    BenchmarkEvidenceAuditIntegrityError,
    BenchmarkEvidenceConflictError,
    BenchmarkRunEvidence,
    SameModelCrossAgentReport,
    SQLiteBenchmarkEvidenceRepository,
)
from evoagent.lab import AuthoritativeBenchmarkEvidenceLab
from evoagent.model_registry.models import canonical_sha256


def _lab(tmp_path, suffix: str = "main"):
    lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / suffix,
        source_commit="d" * 40,
    )
    result = lab.run()
    repository = SQLiteBenchmarkEvidenceRepository(
        lab.registry_database
    )
    return lab, result, repository


def test_registry_reuses_identical_runs_and_comparisons_without_new_events(tmp_path):
    _, _, repository = _lab(tmp_path)
    runs = repository.list_runs()
    reports = repository.list_comparisons()
    checkpoint = repository.checkpoint()

    for run in runs:
        stored, reused = repository.import_run(run)
        assert reused is True
        assert stored == run
    for report in reports:
        stored, reused = repository.store_comparison(report)
        assert reused is True
        assert stored == report

    assert repository.checkpoint() == checkpoint
    assert repository.verify_state()


def test_registry_rejects_conflicting_run_under_same_evidence_id(tmp_path):
    _, _, repository = _lab(tmp_path)
    run = repository.get_run("benchmark-run:a0")
    payload = run.model_dump(mode="python", exclude={"evidence_hash"})
    payload["source_file_sha256"] = "f" * 64
    conflicting = BenchmarkRunEvidence(
        **payload,
        evidence_hash=canonical_sha256(payload),
    )
    with pytest.raises(BenchmarkEvidenceConflictError, match="Conflicting benchmark evidence"):
        repository.import_run(conflicting)


def test_registry_rejects_conflicting_comparison_under_same_id(tmp_path):
    _, _, repository = _lab(tmp_path)
    same_model = repository.get_comparison(
        "comparison:same-model-a2-vs-comparator"
    )
    payload = same_model.model_dump(mode="python", exclude={"report_hash"})
    payload["comparison_id"] = "comparison:longitudinal-a0-a2"
    conflicting = SameModelCrossAgentReport(
        **payload,
        report_hash=canonical_sha256(payload),
    )
    with pytest.raises(BenchmarkEvidenceConflictError, match="Conflicting benchmark comparison"):
        repository.store_comparison(conflicting)


def test_registry_detects_modified_audit_event(tmp_path):
    lab, _, repository = _lab(tmp_path)
    checkpoint = repository.checkpoint()
    with sqlite3.connect(lab.registry_database) as connection:
        connection.execute(
            "UPDATE benchmark_audit_events SET actor_id = ? WHERE sequence = 2",
            ("tampered-actor",),
        )
        connection.commit()
    with pytest.raises(
        BenchmarkEvidenceAuditIntegrityError,
        match="content was modified",
    ):
        SQLiteBenchmarkEvidenceRepository(
            lab.registry_database
        ).verify_audit(checkpoint)


def test_registry_detects_audit_tail_truncation_against_checkpoint(tmp_path):
    lab, _, repository = _lab(tmp_path, suffix="tail")
    checkpoint = repository.checkpoint()
    with sqlite3.connect(lab.registry_database) as connection:
        connection.execute(
            "DELETE FROM benchmark_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM benchmark_audit_events)"
        )
        connection.commit()
    with pytest.raises(
        BenchmarkEvidenceAuditIntegrityError,
        match="external checkpoint",
    ):
        SQLiteBenchmarkEvidenceRepository(
            lab.registry_database
        ).verify_audit(checkpoint)
