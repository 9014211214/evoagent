from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.supervisor import (
    SQLiteSupervisorRepository,
    StaleSupervisorRevision,
    SupervisorAuditIntegrityError,
    SupervisorBudget,
    SupervisorCaseStatus,
    SupervisorConflictError,
    SupervisorOutcome,
    SupervisorPolicy,
    SupervisorRunStatus,
    SupervisorTrack,
    build_supervisor_case,
    canonical_sha256,
)


NOW = datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc)


def _case(case_id: str = "case:skill"):
    return build_supervisor_case(
        case_id=case_id,
        trace_id=f"trace:{case_id}",
        task_id=f"task:{case_id}",
        failure_layer=FailureLayer.SKILL,
        action=EvolutionAction.UPDATE_SKILL,
        attribution_hash="a" * 64,
        evidence_hash="b" * 64,
        source="test",
        created_at=NOW,
    )


def _outcome(case_id: str = "case:skill"):
    payload = {
        "case_id": case_id,
        "track": SupervisorTrack.SKILL,
        "status": SupervisorCaseStatus.COMPLETED,
        "reason": "governed Skill lifecycle completed",
        "executor_id": "test-skill-executor",
        "child_run_id": "child:skill",
        "artifact_refs": ("skill:test@1.1.0",),
        "artifact_hashes": {"result": "c" * 64},
        "metrics": {"initial_score": 0.5, "final_score": 1.0},
        "completed_at": NOW,
        "skill_promoted": True,
        "model_candidate_evaluated": False,
        "model_candidate_activated": False,
        "model_rollback_verified": False,
        "training_executed_by_evoagent": False,
        "external_execution_performed": False,
    }
    return SupervisorOutcome(
        **payload,
        outcome_hash=canonical_sha256(payload),
    )


def _policy():
    return SupervisorPolicy(
        budget=SupervisorBudget(
            max_cases=3,
            max_rounds=3,
            max_skill_executions=1,
            max_model_executions=1,
            max_external_repair_tickets=1,
        )
    )


def test_repository_is_idempotent_and_rejects_conflicting_case(tmp_path):
    repository = SQLiteSupervisorRepository(tmp_path / "supervisor.db")
    run, reused = repository.create_or_get_run(
        "run:test",
        _policy(),
        actor_id="test",
        now=NOW,
    )
    assert reused is False
    same_run, reused = repository.create_or_get_run(
        "run:test",
        _policy(),
        actor_id="test",
        now=NOW,
    )
    assert reused is True
    assert same_run == run

    record, reused = repository.admit_case(
        run.run_id,
        _case(),
        SupervisorTrack.SKILL,
        actor_id="test",
        now=NOW,
    )
    assert reused is False
    event_count = repository.checkpoint().event_count
    same_record, reused = repository.admit_case(
        run.run_id,
        _case(),
        SupervisorTrack.SKILL,
        actor_id="test",
        now=NOW,
    )
    assert reused is True
    assert same_record == record
    assert repository.checkpoint().event_count == event_count

    conflicting = _case().model_copy(update={"evidence_hash": "d" * 64})
    conflicting_payload = conflicting.model_dump(mode="python", exclude={"case_hash"})
    conflicting = conflicting.model_copy(
        update={"case_hash": canonical_sha256(conflicting_payload)}
    )
    with pytest.raises(SupervisorConflictError, match="Conflicting Supervisor case"):
        repository.admit_case(
            run.run_id,
            conflicting,
            SupervisorTrack.SKILL,
            actor_id="test",
            now=NOW,
        )


def test_repository_claim_finalize_and_stale_revisions(tmp_path):
    repository = SQLiteSupervisorRepository(tmp_path / "supervisor.db")
    run, _ = repository.create_or_get_run(
        "run:test",
        _policy(),
        actor_id="test",
        now=NOW,
    )
    record, _ = repository.admit_case(
        run.run_id,
        _case(),
        SupervisorTrack.SKILL,
        actor_id="test",
        now=NOW,
    )
    with pytest.raises(StaleSupervisorRevision):
        repository.claim_case(
            run.run_id,
            record.case.case_id,
            expected_revision=99,
            actor_id="test",
            now=NOW,
        )
    claimed = repository.claim_case(
        run.run_id,
        record.case.case_id,
        expected_revision=record.revision,
        actor_id="test",
        now=NOW,
    )
    assert claimed.status == SupervisorCaseStatus.RUNNING
    with pytest.raises(StaleSupervisorRevision):
        repository.finalize_case(
            run.run_id,
            record.case.case_id,
            _outcome(),
            expected_revision=record.revision,
            actor_id="test",
            now=NOW,
        )
    completed = repository.finalize_case(
        run.run_id,
        record.case.case_id,
        _outcome(),
        expected_revision=claimed.revision,
        actor_id="test",
        now=NOW,
    )
    assert completed.status == SupervisorCaseStatus.COMPLETED
    assert completed.outcome == _outcome()

    running = repository.transition_run(
        run.run_id,
        to_status=SupervisorRunStatus.RUNNING,
        expected_revision=run.revision,
        actor_id="test",
        reason="start",
        now=NOW,
    )
    final = repository.transition_run(
        run.run_id,
        to_status=SupervisorRunStatus.COMPLETED,
        expected_revision=running.revision,
        actor_id="test",
        reason="done",
        now=NOW,
    )
    assert final.status == SupervisorRunStatus.COMPLETED
    assert repository.verify_state(run.run_id)


def test_repository_detects_audit_modification_and_tail_truncation(tmp_path):
    path = tmp_path / "supervisor.db"
    repository = SQLiteSupervisorRepository(path)
    run, _ = repository.create_or_get_run(
        "run:test",
        _policy(),
        actor_id="test",
        now=NOW,
    )
    record, _ = repository.admit_case(
        run.run_id,
        _case(),
        SupervisorTrack.SKILL,
        actor_id="test",
        now=NOW,
    )
    claimed = repository.claim_case(
        run.run_id,
        record.case.case_id,
        expected_revision=record.revision,
        actor_id="test",
        now=NOW,
    )
    repository.finalize_case(
        run.run_id,
        record.case.case_id,
        _outcome(),
        expected_revision=claimed.revision,
        actor_id="test",
        now=NOW,
    )
    checkpoint = repository.checkpoint()

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE supervisor_audit_events SET actor_id = ? WHERE sequence = 2",
            ("tampered",),
        )
        connection.commit()
    with pytest.raises(SupervisorAuditIntegrityError, match="content was modified"):
        SQLiteSupervisorRepository(path).verify_audit(checkpoint)

    clean_path = tmp_path / "clean.db"
    clean = SQLiteSupervisorRepository(clean_path)
    run, _ = clean.create_or_get_run(
        "run:clean",
        _policy(),
        actor_id="test",
        now=NOW,
    )
    clean.admit_case(
        run.run_id,
        _case(),
        SupervisorTrack.SKILL,
        actor_id="test",
        now=NOW,
    )
    checkpoint = clean.checkpoint()
    with sqlite3.connect(clean_path) as connection:
        connection.execute(
            "DELETE FROM supervisor_audit_events WHERE sequence = (SELECT MAX(sequence) FROM supervisor_audit_events)"
        )
        connection.commit()
    with pytest.raises(SupervisorAuditIntegrityError, match="external checkpoint"):
        SQLiteSupervisorRepository(clean_path).verify_audit(checkpoint)
