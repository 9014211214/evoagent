from __future__ import annotations

import json
import sqlite3

import pytest

from evoagent.lab import ModelCandidateAdmissionLab
from evoagent.model_registry import (
    ModelAdmissionPackageError,
    ModelAdmissionPackageManager,
    ModelAuditIntegrityError,
    SQLiteModelRegistry,
    canonical_sha256,
)


def _rewrite_package(path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    payload["package_hash"] = canonical_sha256(
        {
            key: value
            for key, value in payload.items()
            if key != "package_hash"
        }
    )
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_model_registry_detects_modified_audit_event(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="1" * 40,
    )
    result = lab.run()
    registry = SQLiteModelRegistry(lab.model_database)
    checkpoint = registry.checkpoint()

    with sqlite3.connect(lab.model_database) as connection:
        connection.execute(
            "UPDATE model_audit_events SET reason = ? WHERE sequence = 3",
            ("tampered evaluation reason",),
        )
        connection.commit()

    with pytest.raises(
        ModelAuditIntegrityError,
        match="content was modified",
    ):
        SQLiteModelRegistry(lab.model_database).verify_audit(checkpoint)

    assert result.model_event_count == 6


def test_model_registry_detects_tail_truncation_against_checkpoint(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="2" * 40,
    )
    result = lab.run()
    registry = SQLiteModelRegistry(lab.model_database)
    checkpoint = registry.checkpoint()

    with sqlite3.connect(lab.model_database) as connection:
        connection.execute(
            "DELETE FROM model_audit_events WHERE sequence = ?",
            (checkpoint.event_count,),
        )
        connection.commit()

    with pytest.raises(
        ModelAuditIntegrityError,
        match="external checkpoint",
    ):
        SQLiteModelRegistry(lab.model_database).verify_audit(checkpoint)

    assert result.model_event_count == checkpoint.event_count


def test_package_rejects_rehashed_activation_pointer_tamper(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="3" * 40,
    )
    result = lab.run()
    path = lab.package_path

    _rewrite_package(
        path,
        lambda payload: payload.__setitem__(
            "active_model_after_activation",
            payload["initial_manifest"]["model_id"],
        ),
    )

    with pytest.raises(
        ModelAdmissionPackageError,
        match="does not identify the candidate",
    ):
        ModelAdmissionPackageManager().load_file(path)

    assert result.active_model_after_activation == result.candidate_id


def test_package_rejects_rehashed_prohibited_approver(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="4" * 40,
    )
    lab.run()
    path = lab.package_path

    def mutate(payload):
        payload["approvals"][0]["actor_id"] = payload[
            "training_receipt"
        ]["trainer_id"]

    _rewrite_package(path, mutate)

    with pytest.raises(
        ModelAdmissionPackageError,
        match="cannot approve",
    ):
        ModelAdmissionPackageManager().load_file(path)


def test_package_rejects_rehashed_evaluation_aggregate_tamper(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="5" * 40,
    )
    lab.run()
    path = lab.package_path

    def mutate(payload):
        report = payload["evaluation_report"]
        report["held_out_candidate_score"] = 0.0
        report["report_hash"] = canonical_sha256(
            {
                key: value
                for key, value in report.items()
                if key != "report_hash"
            }
        )

    _rewrite_package(path, mutate)

    with pytest.raises(ModelAdmissionPackageError):
        ModelAdmissionPackageManager().load_file(path)


def test_package_rejects_rehashed_campaign_event_truncation(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="6" * 40,
    )
    lab.run()
    path = lab.package_path

    _rewrite_package(
        path,
        lambda payload: payload["campaign_events"].pop(),
    )

    with pytest.raises(
        ModelAdmissionPackageError,
        match="Campaign events do not match the checkpoint",
    ):
        ModelAdmissionPackageManager().load_file(path)


def test_package_rejects_candidate_artifact_rewrite_across_partial_copies(
    tmp_path,
):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="9" * 40,
    )
    lab.run()
    path = lab.package_path

    def mutate(payload):
        candidate = payload["candidate_manifest"]
        candidate["artifact_sha256"] = "f" * 64
        candidate["manifest_hash"] = canonical_sha256(
            {
                key: value
                for key, value in candidate.items()
                if key != "manifest_hash"
            }
        )

    _rewrite_package(path, mutate)

    with pytest.raises(
        ModelAdmissionPackageError,
        match="artifact hash differs",
    ):
        ModelAdmissionPackageManager().load_file(path)
