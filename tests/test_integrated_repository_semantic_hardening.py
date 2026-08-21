from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from evoagent.integrated import (
    IntegratedAuditIntegrityError,
    IntegratedCaseStatus,
    IntegratedEventType,
    IntegratedRunStatus,
    IntegratedTrack,
)
from tests.test_integrated_repository import (
    RUN_ID,
    SKILL_EXECUTOR,
    _case,
    _repository,
    _skill_result,
)


_GENESIS_HASH = "0" * 64


def _coherently_rewrite_metadata(repository, updates):
    events = repository.events(RUN_ID)
    previous_hash = _GENESIS_HASH
    with sqlite3.connect(repository.path) as connection:
        for event in events:
            metadata = dict(event.metadata)
            metadata.update(updates.get(event.sequence, {}))
            event_hash = repository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                run_id=event.run_id,
                event_type=event.event_type,
                case_ids=event.case_ids,
                actor_id=event.actor_id,
                reason=event.reason,
                metadata=metadata,
                created_at=event.created_at,
                previous_hash=previous_hash,
            )
            connection.execute(
                "UPDATE integrated_audit_events "
                "SET metadata_json = ?, previous_hash = ?, event_hash = ? "
                "WHERE run_id = ? AND sequence = ?",
                (
                    json.dumps(
                        metadata,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    previous_hash,
                    event_hash,
                    RUN_ID,
                    event.sequence,
                ),
            )
            previous_hash = event_hash
        connection.commit()


def test_verify_state_accepts_persisted_running_claim_for_crash_recovery(
    tmp_path,
):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=3),
    )

    run = repository.get_run(RUN_ID)
    claimed = repository.get_case(RUN_ID, "case:skill")
    assert run.status == IntegratedRunStatus.RUNNING
    assert run.revision == 1
    assert claimed.status == IntegratedCaseStatus.CLAIMED
    assert repository.verify_state(RUN_ID) is True


def test_coherent_rehash_cannot_forge_claim_and_result_revisions(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    repository.claim_cases(
        RUN_ID,
        case_ids=("case:skill",),
        track=IntegratedTrack.SKILL,
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=0,
        now=start + timedelta(seconds=3),
    )
    repository.record_result(
        _skill_result(start + timedelta(seconds=3)),
        actor_id=SKILL_EXECUTOR,
        expected_run_revision=1,
        now=start + timedelta(seconds=5),
    )
    events = repository.events(RUN_ID)
    claim = next(
        item
        for item in events
        if item.event_type == IntegratedEventType.CASES_CLAIMED
    )
    result = next(
        item
        for item in events
        if item.event_type == IntegratedEventType.TRACK_RESULT_RECORDED
    )

    _coherently_rewrite_metadata(
        repository,
        {
            claim.sequence: {"active_revision_before": 40},
            result.sequence: {"active_revision_before": 41},
        },
    )

    assert repository.verify_audit(RUN_ID) is True
    with pytest.raises(
        IntegratedAuditIntegrityError,
        match="claim/result audit semantics differ",
    ):
        repository.verify_state(RUN_ID)


def test_audit_writer_rejects_backdated_lifecycle_transition(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:skill",
            track=IntegratedTrack.SKILL,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=4),
    )

    with pytest.raises(ValueError, match="precedes the prior lifecycle event"):
        repository.claim_cases(
            RUN_ID,
            case_ids=("case:skill",),
            track=IntegratedTrack.SKILL,
            actor_id=SKILL_EXECUTOR,
            expected_run_revision=0,
            now=start + timedelta(seconds=3),
        )

    assert repository.get_run(RUN_ID).revision == 0
    assert repository.get_case(RUN_ID, "case:skill").status == (
        IntegratedCaseStatus.PENDING
    )


def test_unexecuted_case_status_cannot_be_rewritten(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, policy = _repository(tmp_path, start)
    repository.admit_case(
        RUN_ID,
        _case(
            policy,
            case_id="case:escalation",
            track=IntegratedTrack.ESCALATION,
            created_at=start + timedelta(seconds=1),
        ),
        actor_id="integrated-case-admitter",
        now=start + timedelta(seconds=2),
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE integrated_cases SET status = ? "
            "WHERE run_id = ? AND case_id = ?",
            (
                IntegratedCaseStatus.FAILED.value,
                RUN_ID,
                "case:escalation",
            ),
        )
        connection.commit()

    assert repository.verify_audit(RUN_ID) is True
    with pytest.raises(
        IntegratedAuditIntegrityError,
        match="differs from admission state",
    ):
        repository.verify_state(RUN_ID)


def test_unmodeled_failed_run_state_is_rejected(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, _ = _repository(tmp_path, start)
    completed_at = start + timedelta(seconds=1)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE integrated_runs SET status = ?, "
            "terminal_decision_hash = ?, completed_by = ?, "
            "updated_at = ?, completed_at = ? WHERE run_id = ?",
            (
                IntegratedRunStatus.FAILED.value,
                "f" * 64,
                "forged-failure-completer",
                completed_at.isoformat(),
                completed_at.isoformat(),
                RUN_ID,
            ),
        )
        connection.commit()

    assert repository.verify_audit(RUN_ID) is True
    with pytest.raises(
        IntegratedAuditIntegrityError,
        match="lacks a governed failure lifecycle",
    ):
        repository.verify_state(RUN_ID)


def test_run_update_time_cannot_drift_from_lifecycle(tmp_path):
    start = datetime.now(timezone.utc) - timedelta(minutes=2)
    repository, _ = _repository(tmp_path, start)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE integrated_runs SET updated_at = ? WHERE run_id = ?",
            ((start + timedelta(seconds=1)).isoformat(), RUN_ID),
        )
        connection.commit()

    assert repository.verify_audit(RUN_ID) is True
    with pytest.raises(
        IntegratedAuditIntegrityError,
        match="update time differs from its lifecycle audit",
    ):
        repository.verify_state(RUN_ID)
