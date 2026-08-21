from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.supervisor.models import (
    SupervisorAuditEvent,
    SupervisorCase,
    SupervisorCaseRecord,
    SupervisorCaseStatus,
    SupervisorCheckpoint,
    SupervisorEventType,
    SupervisorOutcome,
    SupervisorPolicy,
    SupervisorRunRecord,
    SupervisorRunStatus,
    SupervisorTrack,
    canonical_sha256,
)


_GENESIS_HASH = "0" * 64


class SupervisorConflictError(RuntimeError):
    pass


class StaleSupervisorRevision(RuntimeError):
    pass


class SupervisorAuditIntegrityError(RuntimeError):
    pass


class SQLiteSupervisorRepository:
    """Persistent, optimistic and tamper-evident closed-loop run state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS supervisor_runs (
                    run_id TEXT PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS supervisor_cases (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    case_json TEXT NOT NULL,
                    track TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outcome_json TEXT,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, case_id),
                    FOREIGN KEY(run_id) REFERENCES supervisor_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS supervisor_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    case_id TEXT,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def create_or_get_run(
        self,
        run_id: str,
        policy: SupervisorPolicy,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> tuple[SupervisorRunRecord, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._run_row(connection, run_id)
                if row is not None:
                    record = self._row_to_run(row)
                    if record.policy != policy:
                        raise SupervisorConflictError(
                            "Existing Supervisor run uses another immutable policy."
                        )
                    connection.commit()
                    return record, True
                record = SupervisorRunRecord(
                    run_id=run_id,
                    policy=policy,
                    status=SupervisorRunStatus.OPEN,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
                connection.execute(
                    "INSERT INTO supervisor_runs (run_id, policy_json, status, revision, "
                    "created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (
                        run_id,
                        self._json(policy.model_dump(mode="json")),
                        record.status.value,
                        record.revision,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    event_type=SupervisorEventType.RUN_CREATED,
                    actor_id=actor_id,
                    to_status=record.status.value,
                    payload={"policy_hash": canonical_sha256(policy)},
                    created_at=now,
                )
                connection.commit()
                return record, False
            except Exception:
                connection.rollback()
                raise

    def transition_run(
        self,
        run_id: str,
        *,
        to_status: SupervisorRunStatus,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> SupervisorRunRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._require_run(connection, run_id)
                if current.status == to_status:
                    connection.commit()
                    return current
                if current.revision != expected_revision:
                    raise StaleSupervisorRevision(
                        f"Expected Supervisor run revision {expected_revision}, found {current.revision}."
                    )
                self._validate_run_transition(current.status, to_status)
                completed_at = (
                    now.isoformat()
                    if to_status not in {SupervisorRunStatus.OPEN, SupervisorRunStatus.RUNNING}
                    else None
                )
                changed = connection.execute(
                    "UPDATE supervisor_runs SET status = ?, revision = revision + 1, "
                    "updated_at = ?, completed_at = ? WHERE run_id = ? AND revision = ?",
                    (
                        to_status.value,
                        now.isoformat(),
                        completed_at,
                        run_id,
                        expected_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleSupervisorRevision(
                        "Supervisor run revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    run_id=run_id,
                    event_type=SupervisorEventType.RUN_STATUS_CHANGED,
                    actor_id=actor_id,
                    from_status=current.status.value,
                    to_status=to_status.value,
                    payload={"reason": reason},
                    created_at=now,
                )
                connection.commit()
                return self.get_run(run_id)
            except Exception:
                connection.rollback()
                raise

    def admit_case(
        self,
        run_id: str,
        case: SupervisorCase,
        track: SupervisorTrack,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> tuple[SupervisorCaseRecord, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                run = self._require_run(connection, run_id)
                if run.status not in {SupervisorRunStatus.OPEN, SupervisorRunStatus.RUNNING}:
                    raise SupervisorConflictError(
                        f"Cannot admit a case into terminal run state {run.status.value}."
                    )
                row = self._case_row(connection, run_id, case.case_id)
                if row is not None:
                    record = self._row_to_case(row)
                    if record.case != case or record.track != track:
                        raise SupervisorConflictError(
                            "Conflicting Supervisor case payload under the same case ID."
                        )
                    connection.commit()
                    return record, True
                count = connection.execute(
                    "SELECT COUNT(*) AS value FROM supervisor_cases WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["value"]
                if int(count) >= run.policy.budget.max_cases:
                    raise SupervisorConflictError("Supervisor total-case budget is exhausted.")
                record = SupervisorCaseRecord(
                    run_id=run_id,
                    case=case,
                    track=track,
                    status=SupervisorCaseStatus.PENDING,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
                connection.execute(
                    "INSERT INTO supervisor_cases (run_id, case_id, case_json, track, status, "
                    "outcome_json, revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, 0, ?, ?)",
                    (
                        run_id,
                        case.case_id,
                        self._json(case.model_dump(mode="json")),
                        track.value,
                        record.status.value,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    case_id=case.case_id,
                    event_type=SupervisorEventType.CASE_ADMITTED,
                    actor_id=actor_id,
                    to_status=record.status.value,
                    payload={
                        "case_hash": case.case_hash,
                        "action": case.action.value,
                        "failure_layer": case.failure_layer.value,
                    },
                    created_at=now,
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    case_id=case.case_id,
                    event_type=SupervisorEventType.CASE_ROUTED,
                    actor_id=actor_id,
                    from_status=record.status.value,
                    to_status=record.status.value,
                    payload={"track": track.value},
                    created_at=now,
                )
                connection.commit()
                return record, False
            except Exception:
                connection.rollback()
                raise

    def claim_case(
        self,
        run_id: str,
        case_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> SupervisorCaseRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_case(connection, run_id, case_id)
                if record.status != SupervisorCaseStatus.PENDING:
                    raise SupervisorConflictError(
                        f"Only PENDING cases may be claimed, found {record.status.value}."
                    )
                if record.revision != expected_revision:
                    raise StaleSupervisorRevision(
                        f"Expected case revision {expected_revision}, found {record.revision}."
                    )
                changed = connection.execute(
                    "UPDATE supervisor_cases SET status = ?, revision = revision + 1, "
                    "updated_at = ? WHERE run_id = ? AND case_id = ? AND revision = ?",
                    (
                        SupervisorCaseStatus.RUNNING.value,
                        now.isoformat(),
                        run_id,
                        case_id,
                        expected_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleSupervisorRevision("Supervisor case changed concurrently.")
                self._append_event(
                    connection,
                    run_id=run_id,
                    case_id=case_id,
                    event_type=SupervisorEventType.CASE_CLAIMED,
                    actor_id=actor_id,
                    from_status=record.status.value,
                    to_status=SupervisorCaseStatus.RUNNING.value,
                    payload={"track": record.track.value},
                    created_at=now,
                )
                connection.commit()
                return self.get_case(run_id, case_id)
            except Exception:
                connection.rollback()
                raise

    def finalize_case(
        self,
        run_id: str,
        case_id: str,
        outcome: SupervisorOutcome,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> SupervisorCaseRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_case(connection, run_id, case_id)
                if record.status != SupervisorCaseStatus.RUNNING:
                    raise SupervisorConflictError(
                        f"Only RUNNING cases may be finalized, found {record.status.value}."
                    )
                if record.revision != expected_revision:
                    raise StaleSupervisorRevision(
                        f"Expected case revision {expected_revision}, found {record.revision}."
                    )
                if outcome.case_id != case_id or outcome.track != record.track:
                    raise SupervisorConflictError("Supervisor outcome target differs from the claimed case.")
                event_type = {
                    SupervisorCaseStatus.COMPLETED: SupervisorEventType.CASE_COMPLETED,
                    SupervisorCaseStatus.BLOCKED: SupervisorEventType.CASE_BLOCKED,
                    SupervisorCaseStatus.ESCALATED: SupervisorEventType.CASE_ESCALATED,
                    SupervisorCaseStatus.QUARANTINED: SupervisorEventType.CASE_QUARANTINED,
                    SupervisorCaseStatus.FAILED: SupervisorEventType.CASE_FAILED,
                }.get(outcome.status)
                if event_type is None:
                    raise SupervisorConflictError("Supervisor outcome is not terminal.")
                changed = connection.execute(
                    "UPDATE supervisor_cases SET status = ?, outcome_json = ?, "
                    "revision = revision + 1, updated_at = ? WHERE run_id = ? "
                    "AND case_id = ? AND revision = ?",
                    (
                        outcome.status.value,
                        self._json(outcome.model_dump(mode="json")),
                        now.isoformat(),
                        run_id,
                        case_id,
                        expected_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleSupervisorRevision("Supervisor case changed concurrently.")
                self._append_event(
                    connection,
                    run_id=run_id,
                    case_id=case_id,
                    event_type=event_type,
                    actor_id=actor_id,
                    from_status=record.status.value,
                    to_status=outcome.status.value,
                    payload={
                        "track": record.track.value,
                        "outcome_hash": outcome.outcome_hash,
                        "executor_id": outcome.executor_id,
                    },
                    created_at=now,
                )
                connection.commit()
                return self.get_case(run_id, case_id)
            except Exception:
                connection.rollback()
                raise

    def get_run(self, run_id: str) -> SupervisorRunRecord:
        with self._connection() as connection:
            return self._require_run(connection, run_id)

    def get_case(self, run_id: str, case_id: str) -> SupervisorCaseRecord:
        with self._connection() as connection:
            return self._require_case(connection, run_id, case_id)

    def list_cases(self, run_id: str) -> list[SupervisorCaseRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM supervisor_cases WHERE run_id = ? ORDER BY created_at, case_id",
                (run_id,),
            ).fetchall()
            return [self._row_to_case(row) for row in rows]

    def events(self, run_id: str | None = None) -> list[SupervisorAuditEvent]:
        with self._connection() as connection:
            if run_id is None:
                rows = connection.execute(
                    "SELECT * FROM supervisor_audit_events ORDER BY sequence"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM supervisor_audit_events WHERE run_id = ? ORDER BY sequence",
                    (run_id,),
                ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> SupervisorCheckpoint:
        events = self.events()
        return SupervisorCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(self, checkpoint: SupervisorCheckpoint | None = None) -> bool:
        events = self.events()
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise SupervisorAuditIntegrityError(
                    "Supervisor audit sequence or hash chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                run_id=event.run_id,
                case_id=event.case_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                from_status=event.from_status,
                to_status=event.to_status,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise SupervisorAuditIntegrityError(
                    "Supervisor audit event content was modified."
                )
            previous_hash = event.event_hash
        current = SupervisorCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint is not None and current != checkpoint:
            raise SupervisorAuditIntegrityError(
                "Supervisor audit does not match the external checkpoint."
            )
        return True

    def verify_state(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        cases = self.list_cases(run_id)
        if len(cases) > run.policy.budget.max_cases:
            raise SupervisorConflictError("Persisted Supervisor cases exceed the run budget.")
        if run.status not in {SupervisorRunStatus.OPEN, SupervisorRunStatus.RUNNING}:
            if any(
                item.status in {SupervisorCaseStatus.PENDING, SupervisorCaseStatus.RUNNING}
                for item in cases
            ):
                raise SupervisorConflictError(
                    "Terminal Supervisor run contains unfinished cases."
                )
        if run.status == SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS and not any(
            item.status == SupervisorCaseStatus.ESCALATED for item in cases
        ):
            raise SupervisorConflictError(
                "COMPLETED_WITH_ESCALATIONS run has no escalated case."
            )
        if run.status == SupervisorRunStatus.QUARANTINED and not any(
            item.status == SupervisorCaseStatus.QUARANTINED for item in cases
        ):
            raise SupervisorConflictError("QUARANTINED run has no quarantined case.")
        if sum(item.track == SupervisorTrack.SKILL for item in cases) > run.policy.budget.max_skill_executions:
            raise SupervisorConflictError("Skill-track case count exceeds the Supervisor budget.")
        if sum(item.track == SupervisorTrack.MODEL for item in cases) > run.policy.budget.max_model_executions:
            raise SupervisorConflictError("Model-track case count exceeds the Supervisor budget.")
        self.verify_audit()
        return True

    @staticmethod
    def _validate_run_transition(
        current: SupervisorRunStatus,
        target: SupervisorRunStatus,
    ) -> None:
        allowed = {
            SupervisorRunStatus.OPEN: {
                SupervisorRunStatus.RUNNING,
                SupervisorRunStatus.COMPLETED,
                SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS,
                SupervisorRunStatus.BLOCKED,
                SupervisorRunStatus.QUARANTINED,
                SupervisorRunStatus.BUDGET_EXHAUSTED,
                SupervisorRunStatus.FAILED,
            },
            SupervisorRunStatus.RUNNING: {
                SupervisorRunStatus.COMPLETED,
                SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS,
                SupervisorRunStatus.BLOCKED,
                SupervisorRunStatus.QUARANTINED,
                SupervisorRunStatus.BUDGET_EXHAUSTED,
                SupervisorRunStatus.FAILED,
            },
        }
        if target not in allowed.get(current, set()):
            raise SupervisorConflictError(
                f"Illegal Supervisor run transition {current.value} -> {target.value}."
            )

    @staticmethod
    def _run_row(connection: sqlite3.Connection, run_id: str):
        return connection.execute(
            "SELECT * FROM supervisor_runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def _require_run(self, connection: sqlite3.Connection, run_id: str) -> SupervisorRunRecord:
        row = self._run_row(connection, run_id)
        if row is None:
            raise KeyError(f"Unknown Supervisor run: {run_id}")
        return self._row_to_run(row)

    @staticmethod
    def _case_row(connection: sqlite3.Connection, run_id: str, case_id: str):
        return connection.execute(
            "SELECT * FROM supervisor_cases WHERE run_id = ? AND case_id = ?",
            (run_id, case_id),
        ).fetchone()

    def _require_case(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
    ) -> SupervisorCaseRecord:
        row = self._case_row(connection, run_id, case_id)
        if row is None:
            raise KeyError(f"Unknown Supervisor case: {run_id}/{case_id}")
        return self._row_to_case(row)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> SupervisorRunRecord:
        return SupervisorRunRecord(
            run_id=row["run_id"],
            policy=SupervisorPolicy.model_validate(json.loads(row["policy_json"])),
            status=SupervisorRunStatus(row["status"]),
            revision=int(row["revision"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
        )

    @staticmethod
    def _row_to_case(row: sqlite3.Row) -> SupervisorCaseRecord:
        outcome = (
            SupervisorOutcome.model_validate(json.loads(row["outcome_json"]))
            if row["outcome_json"]
            else None
        )
        return SupervisorCaseRecord(
            run_id=row["run_id"],
            case=SupervisorCase.model_validate(json.loads(row["case_json"])),
            track=SupervisorTrack(row["track"]),
            status=SupervisorCaseStatus(row["status"]),
            outcome=outcome,
            revision=int(row["revision"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        event_type: SupervisorEventType,
        actor_id: str,
        created_at: datetime,
        case_id: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM supervisor_audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"supervisor-event:{uuid.uuid4()}"
        payload = payload or {}
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            run_id=run_id,
            case_id=case_id,
            event_type=event_type,
            actor_id=actor_id,
            from_status=from_status,
            to_status=to_status,
            payload=payload,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO supervisor_audit_events (sequence, event_id, run_id, case_id, "
            "event_type, actor_id, from_status, to_status, payload_json, created_at, "
            "previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                run_id,
                case_id,
                event_type.value,
                actor_id,
                from_status,
                to_status,
                self._json(payload),
                created_at.isoformat(),
                previous_hash,
                event_hash,
            ),
        )

    @staticmethod
    def _event_hash(
        *,
        sequence: int,
        event_id: str,
        run_id: str,
        case_id: str | None,
        event_type: SupervisorEventType,
        actor_id: str,
        from_status: str | None,
        to_status: str | None,
        payload: dict[str, Any],
        created_at: datetime,
        previous_hash: str,
    ) -> str:
        return canonical_sha256(
            {
                "sequence": sequence,
                "event_id": event_id,
                "run_id": run_id,
                "case_id": case_id,
                "event_type": event_type.value,
                "actor_id": actor_id,
                "from_status": from_status,
                "to_status": to_status,
                "payload": payload,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> SupervisorAuditEvent:
        return SupervisorAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            run_id=row["run_id"],
            case_id=row["case_id"],
            event_type=SupervisorEventType(row["event_type"]),
            actor_id=row["actor_id"],
            from_status=row["from_status"],
            to_status=row["to_status"],
            payload=json.loads(row["payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


__all__ = [
    "SQLiteSupervisorRepository",
    "StaleSupervisorRevision",
    "SupervisorAuditIntegrityError",
    "SupervisorConflictError",
]
