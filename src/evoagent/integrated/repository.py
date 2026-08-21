from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.composite import CompositeStopAction, CompositeStopDecision
from evoagent.model_registry.models import canonical_sha256

from .models import (
    IntegratedAuditEvent,
    IntegratedCase,
    IntegratedCaseRecord,
    IntegratedCaseStatus,
    IntegratedCheckpoint,
    IntegratedEventType,
    IntegratedRunPolicy,
    IntegratedRunRecord,
    IntegratedRunStatus,
    IntegratedTrack,
    IntegratedTrackResult,
)


_GENESIS_HASH = "0" * 64


class IntegratedRepositoryConflictError(RuntimeError):
    pass


class StaleIntegratedRevision(RuntimeError):
    pass


class IntegratedAuditIntegrityError(RuntimeError):
    pass


class SQLiteIntegratedEvolutionRepository:
    """Persistent mixed-track queue with optimistic run revisions."""

    def __init__(self, path: str | Path):
        raw_path = Path(path).expanduser()
        if raw_path.is_symlink():
            raise ValueError(
                "Integrated evolution Repository path must not be a symlink."
            )
        self.path = raw_path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
        )
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
                CREATE TABLE IF NOT EXISTS integrated_runs (
                    run_id TEXT PRIMARY KEY,
                    lineage_id TEXT NOT NULL UNIQUE,
                    policy_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    round_index INTEGER NOT NULL,
                    skill_execution_count INTEGER NOT NULL,
                    policy_execution_count INTEGER NOT NULL,
                    terminal_decision_hash TEXT,
                    created_by TEXT NOT NULL,
                    completed_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS integrated_cases (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    case_json TEXT NOT NULL,
                    track TEXT NOT NULL,
                    status TEXT NOT NULL,
                    claimed_by TEXT,
                    result_id TEXT,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, case_id),
                    FOREIGN KEY(run_id) REFERENCES integrated_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS integrated_track_results (
                    result_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES integrated_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS integrated_audit_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    case_ids_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence)
                );
                """
            )

    def create_run(
        self,
        *,
        run_id: str,
        lineage_id: str,
        policy: IntegratedRunPolicy,
        actor_id: str,
        now: datetime | None = None,
    ) -> IntegratedRunRecord:
        effective = self._effective_now(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._run_row(connection, run_id)
                if row is not None:
                    existing = self._row_to_run(row)
                    if (
                        existing.lineage_id == lineage_id
                        and existing.policy == policy
                        and row["created_by"] == actor_id
                    ):
                        connection.commit()
                        return existing
                    raise IntegratedRepositoryConflictError(
                        "Integrated run ID contains another immutable policy."
                    )
                lineage = connection.execute(
                    "SELECT run_id FROM integrated_runs WHERE lineage_id = ?",
                    (lineage_id,),
                ).fetchone()
                if lineage is not None:
                    raise IntegratedRepositoryConflictError(
                        "Composite lineage is already bound to another integrated run."
                    )
                record = IntegratedRunRecord(
                    run_id=run_id,
                    lineage_id=lineage_id,
                    policy=policy,
                    status=IntegratedRunStatus.OPEN,
                    revision=0,
                    round_index=0,
                    skill_execution_count=0,
                    policy_execution_count=0,
                    terminal_decision_hash=None,
                    created_at=effective,
                    updated_at=effective,
                    completed_at=None,
                )
                connection.execute(
                    "INSERT INTO integrated_runs "
                    "(run_id, lineage_id, policy_json, status, revision, "
                    "round_index, skill_execution_count, policy_execution_count, "
                    "terminal_decision_hash, created_by, completed_by, created_at, "
                    "updated_at, completed_at) "
                    "VALUES (?, ?, ?, ?, 0, 0, 0, 0, NULL, ?, NULL, ?, ?, NULL)",
                    (
                        run_id,
                        lineage_id,
                        self._json(policy.model_dump(mode="json")),
                        IntegratedRunStatus.OPEN.value,
                        actor_id,
                        effective.isoformat(),
                        effective.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    event_type=IntegratedEventType.RUN_CREATED,
                    actor_id=actor_id,
                    reason="Integrated multi-track evolution run created.",
                    metadata={
                        "lineage_id": lineage_id,
                        "policy_hash": policy.policy_hash,
                    },
                    created_at=effective,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def admit_case(
        self,
        run_id: str,
        case: IntegratedCase,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> IntegratedCaseRecord:
        effective = self._effective_now(now)
        if case.created_at > effective:
            raise ValueError(
                "Integrated case postdates its Repository admission."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                run = self._require_run(connection, run_id)
                self._require_mutable_run(run)
                row = self._case_row(connection, run_id, case.case_id)
                if row is not None:
                    existing = self._row_to_case(row)
                    if existing.case == case:
                        connection.commit()
                        return existing
                    raise IntegratedRepositoryConflictError(
                        "Integrated case ID contains another immutable case."
                    )
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM integrated_cases "
                    "WHERE run_id = ?",
                    (run_id,),
                ).fetchone()["count"]
                if int(count) >= run.policy.max_cases:
                    raise IntegratedRepositoryConflictError(
                        "Integrated run has reached its frozen case budget."
                    )
                status = {
                    IntegratedTrack.SKILL: IntegratedCaseStatus.PENDING,
                    IntegratedTrack.LOCAL_POLICY: IntegratedCaseStatus.PENDING,
                    IntegratedTrack.ESCALATION: IntegratedCaseStatus.ESCALATED,
                    IntegratedTrack.QUARANTINE: IntegratedCaseStatus.QUARANTINED,
                }[case.track]
                record = IntegratedCaseRecord(
                    run_id=run_id,
                    case=case,
                    status=status,
                    claimed_by=None,
                    result_id=None,
                    revision=0,
                    created_at=effective,
                    updated_at=effective,
                )
                connection.execute(
                    "INSERT INTO integrated_cases "
                    "(run_id, case_id, case_json, track, status, claimed_by, "
                    "result_id, revision, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?)",
                    (
                        run_id,
                        case.case_id,
                        self._json(case.model_dump(mode="json")),
                        case.track.value,
                        status.value,
                        effective.isoformat(),
                        effective.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    event_type=IntegratedEventType.CASE_ADMITTED,
                    case_ids=(case.case_id,),
                    actor_id=actor_id,
                    reason="Attributed mixed-track case admitted.",
                    metadata={
                        "case_hash": case.case_hash,
                        "track": case.track.value,
                        "status": status.value,
                    },
                    created_at=effective,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def claim_cases(
        self,
        run_id: str,
        *,
        case_ids: tuple[str, ...],
        track: IntegratedTrack,
        actor_id: str,
        expected_run_revision: int,
        now: datetime | None = None,
    ) -> tuple[IntegratedRunRecord, tuple[IntegratedCaseRecord, ...]]:
        effective = self._effective_now(now)
        normalized = self._normalize_case_ids(case_ids)
        if track not in {IntegratedTrack.SKILL, IntegratedTrack.LOCAL_POLICY}:
            raise ValueError(
                "Only Skill or local-policy cases may enter automatic execution."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                run_row = self._require_run_row(connection, run_id)
                run = self._row_to_run(run_row)
                self._require_mutable_run(run)
                self._check_run_revision(run, expected_run_revision)
                if self._claimed_rows(connection, run_id):
                    raise IntegratedRepositoryConflictError(
                        "Integrated run already contains a claimed execution batch."
                    )
                records = tuple(
                    self._require_case(connection, run_id, case_id)
                    for case_id in normalized
                )
                if any(
                    record.status != IntegratedCaseStatus.PENDING
                    or record.case.track != track
                    for record in records
                ):
                    raise IntegratedRepositoryConflictError(
                        "Integrated claim contains a non-pending or wrong-track case."
                    )
                if track == IntegratedTrack.SKILL:
                    if len(records) != 1:
                        raise ValueError(
                            "Skill execution claims exactly one attributed case."
                        )
                    if (
                        run.skill_execution_count
                        >= run.policy.max_skill_executions
                    ):
                        raise IntegratedRepositoryConflictError(
                            "Integrated Skill execution budget is exhausted."
                        )
                else:
                    all_pending = tuple(
                        item.case.case_id
                        for item in self._pending_records(
                            connection,
                            run_id,
                            IntegratedTrack.LOCAL_POLICY,
                        )
                    )
                    if normalized != all_pending:
                        raise ValueError(
                            "Local-policy execution must claim the complete pending evidence batch."
                        )
                    if len(records) < run.policy.min_policy_cases:
                        raise ValueError(
                            "Local-policy execution lacks the required distinct cases."
                        )
                    if (
                        run.policy_execution_count
                        >= run.policy.max_policy_executions
                    ):
                        raise IntegratedRepositoryConflictError(
                            "Integrated local-policy execution budget is exhausted."
                        )
                for record in records:
                    connection.execute(
                        "UPDATE integrated_cases SET status = ?, claimed_by = ?, "
                        "revision = revision + 1, updated_at = ? "
                        "WHERE run_id = ? AND case_id = ? AND revision = ?",
                        (
                            IntegratedCaseStatus.CLAIMED.value,
                            actor_id,
                            effective.isoformat(),
                            run_id,
                            record.case.case_id,
                            record.revision,
                        ),
                    )
                connection.execute(
                    "UPDATE integrated_runs SET status = ?, revision = revision + 1, "
                    "updated_at = ? WHERE run_id = ? AND revision = ?",
                    (
                        IntegratedRunStatus.RUNNING.value,
                        effective.isoformat(),
                        run_id,
                        expected_run_revision,
                    ),
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    event_type=IntegratedEventType.CASES_CLAIMED,
                    case_ids=normalized,
                    actor_id=actor_id,
                    reason="Governed mixed-track execution batch claimed.",
                    metadata={
                        "track": track.value,
                        "active_revision_before": expected_run_revision,
                    },
                    created_at=effective,
                )
                connection.commit()
                return (
                    self.get_run(run_id),
                    tuple(self.get_case(run_id, item.case.case_id) for item in records),
                )
            except Exception:
                connection.rollback()
                raise

    def record_result(
        self,
        result: IntegratedTrackResult,
        *,
        actor_id: str,
        expected_run_revision: int,
        now: datetime | None = None,
    ) -> IntegratedRunRecord:
        effective = self._effective_now(now)
        if actor_id != result.executor_id:
            raise ValueError(
                "Integrated result recorder differs from the executor."
            )
        if result.completed_at > effective:
            raise ValueError(
                "Integrated result postdates its Repository write."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                run_row = self._require_run_row(connection, result.run_id)
                run = self._row_to_run(run_row)
                existing_row = self._result_row(connection, result.result_id)
                if existing_row is not None:
                    existing = self._row_to_result(existing_row)
                    if existing != result:
                        raise IntegratedRepositoryConflictError(
                            "Integrated result ID contains another immutable result."
                        )
                    if run.revision != expected_run_revision + 1:
                        raise StaleIntegratedRevision(
                            "Applied integrated result differs from the retried revision."
                        )
                    completed = tuple(
                        self._require_case(
                            connection,
                            result.run_id,
                            case_id,
                        )
                        for case_id in result.case_ids
                    )
                    if any(
                        item.status != IntegratedCaseStatus.COMPLETED
                        or item.result_id != result.result_id
                        or item.claimed_by != actor_id
                        for item in completed
                    ):
                        raise IntegratedRepositoryConflictError(
                            "Integrated result retry differs from completed cases."
                        )
                    connection.commit()
                    return run
                self._require_mutable_run(run)
                self._check_run_revision(run, expected_run_revision)
                if run.status != IntegratedRunStatus.RUNNING:
                    raise IntegratedRepositoryConflictError(
                        "Integrated result requires a RUNNING claimed batch."
                    )
                records = tuple(
                    self._require_case(
                        connection,
                        result.run_id,
                        case_id,
                    )
                    for case_id in result.case_ids
                )
                claimed_ids = tuple(
                    item.case.case_id
                    for item in self._claimed_records(connection, result.run_id)
                )
                if tuple(result.case_ids) != claimed_ids:
                    raise IntegratedRepositoryConflictError(
                        "Integrated result does not cover the exact claimed batch."
                    )
                if any(
                    item.status != IntegratedCaseStatus.CLAIMED
                    or item.claimed_by != actor_id
                    or item.case.track != result.track
                    for item in records
                ):
                    raise IntegratedRepositoryConflictError(
                        "Integrated result differs from claimed case evidence."
                    )
                if result.track == IntegratedTrack.SKILL:
                    if (
                        run.skill_execution_count + 1
                        > run.policy.max_skill_executions
                    ):
                        raise IntegratedRepositoryConflictError(
                            "Integrated Skill result exceeds its execution budget."
                        )
                    skill_increment, policy_increment = 1, 0
                elif result.track == IntegratedTrack.LOCAL_POLICY:
                    if (
                        run.policy_execution_count + 1
                        > run.policy.max_policy_executions
                    ):
                        raise IntegratedRepositoryConflictError(
                            "Integrated local-policy result exceeds its execution budget."
                        )
                    skill_increment, policy_increment = 0, 1
                else:
                    raise ValueError(
                        "Integrated automatic result belongs to another track."
                    )
                if run.round_index + 1 > run.policy.max_rounds:
                    raise IntegratedRepositoryConflictError(
                        "Integrated result exceeds the frozen round budget."
                    )
                connection.execute(
                    "INSERT INTO integrated_track_results "
                    "(result_id, run_id, result_json, recorded_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        result.result_id,
                        result.run_id,
                        self._json(result.model_dump(mode="json")),
                        effective.isoformat(),
                    ),
                )
                for record in records:
                    connection.execute(
                        "UPDATE integrated_cases SET status = ?, result_id = ?, "
                        "revision = revision + 1, updated_at = ? "
                        "WHERE run_id = ? AND case_id = ? AND revision = ?",
                        (
                            IntegratedCaseStatus.COMPLETED.value,
                            result.result_id,
                            effective.isoformat(),
                            result.run_id,
                            record.case.case_id,
                            record.revision,
                        ),
                    )
                connection.execute(
                    "UPDATE integrated_runs SET status = ?, "
                    "round_index = round_index + 1, "
                    "skill_execution_count = skill_execution_count + ?, "
                    "policy_execution_count = policy_execution_count + ?, "
                    "revision = revision + 1, updated_at = ? "
                    "WHERE run_id = ? AND revision = ?",
                    (
                        IntegratedRunStatus.OPEN.value,
                        skill_increment,
                        policy_increment,
                        effective.isoformat(),
                        result.run_id,
                        expected_run_revision,
                    ),
                )
                self._append_event(
                    connection,
                    run_id=result.run_id,
                    event_type=IntegratedEventType.TRACK_RESULT_RECORDED,
                    case_ids=result.case_ids,
                    actor_id=actor_id,
                    reason="Governed mixed-track execution result recorded.",
                    metadata={
                        "result_id": result.result_id,
                        "result_hash": result.result_hash,
                        "track": result.track.value,
                        "component_ref": result.component_ref,
                        "component_hash": result.component_hash,
                        "active_revision_before": expected_run_revision,
                    },
                    created_at=effective,
                )
                connection.commit()
                return self.get_run(result.run_id)
            except Exception:
                connection.rollback()
                raise

    def complete_run(
        self,
        run_id: str,
        decision: CompositeStopDecision,
        *,
        actor_id: str,
        expected_run_revision: int,
        now: datetime | None = None,
    ) -> IntegratedRunRecord:
        effective = self._effective_now(now)
        if decision.action == CompositeStopAction.CONTINUE:
            raise ValueError(
                "Integrated run cannot complete from a CONTINUE decision."
            )
        if decision.decided_at > effective:
            raise ValueError(
                "Integrated terminal decision postdates the run completion."
            )
        if actor_id == decision.decided_by:
            raise ValueError(
                "Integrated run completer must be independent from the stop decider."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._require_run_row(connection, run_id)
                run = self._row_to_run(row)
                if run.status in {
                    IntegratedRunStatus.STOPPED,
                    IntegratedRunStatus.ESCALATED,
                }:
                    if (
                        run.terminal_decision_hash != decision.decision_hash
                        or row["completed_by"] != actor_id
                    ):
                        raise IntegratedRepositoryConflictError(
                            "Integrated run completion retry differs from terminal evidence."
                        )
                    if run.revision != expected_run_revision + 1:
                        raise StaleIntegratedRevision(
                            "Applied integrated completion differs from the retried revision."
                        )
                    connection.commit()
                    return run
                self._require_mutable_run(run)
                self._check_run_revision(run, expected_run_revision)
                if decision.lineage_id != run.lineage_id:
                    raise ValueError(
                        "Integrated terminal decision belongs to another composite lineage."
                    )
                pending = self._pending_records(connection, run_id)
                claimed = self._claimed_records(connection, run_id)
                if claimed:
                    raise IntegratedRepositoryConflictError(
                        "Integrated run cannot complete with a claimed execution batch."
                    )
                if decision.action == CompositeStopAction.STOP and pending:
                    raise IntegratedRepositoryConflictError(
                        "Integrated STOP requires no pending automatic cases."
                    )
                status = (
                    IntegratedRunStatus.STOPPED
                    if decision.action == CompositeStopAction.STOP
                    else IntegratedRunStatus.ESCALATED
                )
                connection.execute(
                    "UPDATE integrated_runs SET status = ?, "
                    "terminal_decision_hash = ?, completed_by = ?, "
                    "revision = revision + 1, updated_at = ?, completed_at = ? "
                    "WHERE run_id = ? AND revision = ?",
                    (
                        status.value,
                        decision.decision_hash,
                        actor_id,
                        effective.isoformat(),
                        effective.isoformat(),
                        run_id,
                        expected_run_revision,
                    ),
                )
                self._append_event(
                    connection,
                    run_id=run_id,
                    event_type=IntegratedEventType.RUN_COMPLETED,
                    actor_id=actor_id,
                    reason="Integrated multi-track evolution run completed.",
                    metadata={
                        "decision_hash": decision.decision_hash,
                        "decision_action": decision.action.value,
                        "snapshot_id": decision.snapshot_id,
                        "pending_case_count": len(pending),
                        "active_revision_before": expected_run_revision,
                    },
                    created_at=effective,
                )
                connection.commit()
                return self.get_run(run_id)
            except Exception:
                connection.rollback()
                raise

    def get_run(self, run_id: str) -> IntegratedRunRecord:
        with self._connection() as connection:
            return self._require_run(connection, run_id)

    def get_case(self, run_id: str, case_id: str) -> IntegratedCaseRecord:
        with self._connection() as connection:
            return self._require_case(connection, run_id, case_id)

    def list_cases(
        self,
        run_id: str,
        *,
        status: IntegratedCaseStatus | None = None,
        track: IntegratedTrack | None = None,
    ) -> tuple[IntegratedCaseRecord, ...]:
        with self._connection() as connection:
            clauses = ["run_id = ?"]
            values: list[Any] = [run_id]
            if status is not None:
                clauses.append("status = ?")
                values.append(status.value)
            if track is not None:
                clauses.append("track = ?")
                values.append(track.value)
            rows = connection.execute(
                "SELECT * FROM integrated_cases WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at, case_id",
                tuple(values),
            ).fetchall()
            return tuple(self._row_to_case(row) for row in rows)

    def pending_cases(
        self,
        run_id: str,
        track: IntegratedTrack | None = None,
    ) -> tuple[IntegratedCaseRecord, ...]:
        return self.list_cases(
            run_id,
            status=IntegratedCaseStatus.PENDING,
            track=track,
        )

    def get_result(self, result_id: str) -> IntegratedTrackResult:
        with self._connection() as connection:
            row = self._result_row(connection, result_id)
            if row is None:
                raise KeyError(f"Unknown integrated result: {result_id}")
            return self._row_to_result(row)

    def list_results(self, run_id: str) -> tuple[IntegratedTrackResult, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM integrated_track_results WHERE run_id = ? "
                "ORDER BY recorded_at, result_id",
                (run_id,),
            ).fetchall()
            return tuple(self._row_to_result(row) for row in rows)

    def events(self, run_id: str) -> tuple[IntegratedAuditEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM integrated_audit_events WHERE run_id = ? "
                "ORDER BY sequence",
                (run_id,),
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)

    def checkpoint(self, run_id: str) -> IntegratedCheckpoint:
        events = self.events(run_id)
        return IntegratedCheckpoint(
            run_id=run_id,
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_state(self, run_id: str) -> bool:
        with self._connection() as connection:
            row = self._require_run_row(connection, run_id)
            run = self._row_to_run(row)
            cases = self.list_cases(run_id)
            results = self.list_results(run_id)
            skill_results = tuple(
                item for item in results if item.track == IntegratedTrack.SKILL
            )
            policy_results = tuple(
                item
                for item in results
                if item.track == IntegratedTrack.LOCAL_POLICY
            )
            if (
                run.round_index != len(results)
                or run.skill_execution_count != len(skill_results)
                or run.policy_execution_count != len(policy_results)
            ):
                raise IntegratedAuditIntegrityError(
                    "Integrated run counters differ from persisted results."
                )
            by_result = {item.result_id: item for item in results}
            for case in cases:
                if case.status == IntegratedCaseStatus.COMPLETED:
                    result = by_result.get(case.result_id)
                    if (
                        result is None
                        or case.case.case_id not in result.case_ids
                        or case.claimed_by != result.executor_id
                        or case.case.track != result.track
                    ):
                        raise IntegratedAuditIntegrityError(
                            "Completed integrated case differs from its result."
                        )
                elif case.result_id is not None:
                    raise IntegratedAuditIntegrityError(
                        "Non-completed integrated case contains a result."
                    )
            claimed = tuple(
                item
                for item in cases
                if item.status == IntegratedCaseStatus.CLAIMED
            )
            if (run.status == IntegratedRunStatus.RUNNING) != bool(claimed):
                raise IntegratedAuditIntegrityError(
                    "Integrated RUNNING state differs from claimed cases."
                )
            expected_revision = 2 * len(results) + (1 if claimed else 0)
            terminal = run.status in {
                IntegratedRunStatus.STOPPED,
                IntegratedRunStatus.ESCALATED,
            }
            if terminal:
                expected_revision += 1
                if row["completed_by"] is None:
                    raise IntegratedAuditIntegrityError(
                        "Integrated terminal run lacks a completion actor."
                    )
            if run.revision != expected_revision:
                raise IntegratedAuditIntegrityError(
                    "Integrated run revision differs from claim/result lifecycle."
                )
        self.verify_audit(run_id)
        self._verify_semantic_events(run_id)
        return True

    def verify_audit(
        self,
        run_id: str,
        checkpoint: IntegratedCheckpoint | None = None,
    ) -> bool:
        events = self.events(run_id)
        previous = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous
            ):
                raise IntegratedAuditIntegrityError(
                    "Integrated audit sequence or hash chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                run_id=event.run_id,
                event_type=event.event_type,
                case_ids=event.case_ids,
                actor_id=event.actor_id,
                reason=event.reason,
                metadata=event.metadata,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise IntegratedAuditIntegrityError(
                    "Integrated audit event content was modified."
                )
            previous = event.event_hash
        current = IntegratedCheckpoint(
            run_id=run_id,
            event_count=len(events),
            head_hash=previous,
        )
        if checkpoint is not None and current != checkpoint:
            raise IntegratedAuditIntegrityError(
                "Integrated audit differs from its external checkpoint."
            )
        return True

    def _verify_semantic_events(self, run_id: str) -> None:
        events = self.events(run_id)
        run = self.get_run(run_id)
        cases = self.list_cases(run_id)
        results = self.list_results(run_id)
        if not events or events[0].event_type != IntegratedEventType.RUN_CREATED:
            raise IntegratedAuditIntegrityError(
                "Integrated audit lacks the run creation event."
            )
        created = events[0]
        if (
            created.case_ids
            or created.reason
            != "Integrated multi-track evolution run created."
            or created.metadata
            != {
                "lineage_id": run.lineage_id,
                "policy_hash": run.policy.policy_hash,
            }
        ):
            raise IntegratedAuditIntegrityError(
                "Integrated run creation audit semantics differ."
            )
        admitted_events = tuple(
            item
            for item in events
            if item.event_type == IntegratedEventType.CASE_ADMITTED
        )
        if len(admitted_events) != len(cases):
            raise IntegratedAuditIntegrityError(
                "Integrated audit omits or duplicates case admission."
            )
        case_by_id = {item.case.case_id: item for item in cases}
        for event in admitted_events:
            if len(event.case_ids) != 1:
                raise IntegratedAuditIntegrityError(
                    "Integrated case admission audit contains another case set."
                )
            case = case_by_id.get(event.case_ids[0])
            if (
                case is None
                or event.reason != "Attributed mixed-track case admitted."
                or event.metadata
                != {
                    "case_hash": case.case.case_hash,
                    "track": case.case.track.value,
                    "status": (
                        IntegratedCaseStatus.PENDING.value
                        if case.case.track
                        in {IntegratedTrack.SKILL, IntegratedTrack.LOCAL_POLICY}
                        else (
                            IntegratedCaseStatus.ESCALATED.value
                            if case.case.track == IntegratedTrack.ESCALATION
                            else IntegratedCaseStatus.QUARANTINED.value
                        )
                    ),
                }
            ):
                raise IntegratedAuditIntegrityError(
                    "Integrated case admission audit semantics differ."
                )
        claim_events = tuple(
            item
            for item in events
            if item.event_type == IntegratedEventType.CASES_CLAIMED
        )
        result_events = tuple(
            item
            for item in events
            if item.event_type
            == IntegratedEventType.TRACK_RESULT_RECORDED
        )
        if len(claim_events) != len(results) or len(result_events) != len(results):
            raise IntegratedAuditIntegrityError(
                "Integrated audit lacks one claim and result event per execution."
            )
        result_by_id = {item.result_id: item for item in results}
        for claim, result_event in zip(
            claim_events,
            result_events,
            strict=True,
        ):
            if claim.reason != "Governed mixed-track execution batch claimed.":
                raise IntegratedAuditIntegrityError(
                    "Integrated claim audit reason differs."
                )
            result = result_by_id.get(result_event.metadata.get("result_id"))
            if (
                result is None
                or claim.case_ids != result.case_ids
                or result_event.case_ids != result.case_ids
                or claim.actor_id != result.executor_id
                or result_event.actor_id != result.executor_id
                or claim.metadata.get("track") != result.track.value
                or result_event.reason
                != "Governed mixed-track execution result recorded."
                or result_event.metadata
                != {
                    "result_id": result.result_id,
                    "result_hash": result.result_hash,
                    "track": result.track.value,
                    "component_ref": result.component_ref,
                    "component_hash": result.component_hash,
                    "active_revision_before": claim.metadata[
                        "active_revision_before"
                    ]
                    + 1,
                }
            ):
                raise IntegratedAuditIntegrityError(
                    "Integrated claim/result audit semantics differ."
                )
        completion_events = tuple(
            item
            for item in events
            if item.event_type == IntegratedEventType.RUN_COMPLETED
        )
        terminal = run.status in {
            IntegratedRunStatus.STOPPED,
            IntegratedRunStatus.ESCALATED,
        }
        if len(completion_events) != (1 if terminal else 0):
            raise IntegratedAuditIntegrityError(
                "Integrated terminal audit count differs from run state."
            )
        if terminal:
            completed = completion_events[0]
            if (
                completed.reason
                != "Integrated multi-track evolution run completed."
                or completed.metadata.get("decision_hash")
                != run.terminal_decision_hash
                or completed.metadata.get("decision_action")
                != (
                    CompositeStopAction.STOP.value
                    if run.status == IntegratedRunStatus.STOPPED
                    else CompositeStopAction.ESCALATE.value
                )
            ):
                raise IntegratedAuditIntegrityError(
                    "Integrated run completion audit semantics differ."
                )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        event_type: IntegratedEventType,
        actor_id: str,
        reason: str,
        metadata: dict[str, Any],
        created_at: datetime,
        case_ids: tuple[str, ...] = (),
    ) -> None:
        normalized = tuple(sorted(case_ids))
        last = connection.execute(
            "SELECT sequence, event_hash FROM integrated_audit_events "
            "WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"integrated-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            run_id=run_id,
            event_type=event_type,
            case_ids=normalized,
            actor_id=actor_id,
            reason=reason,
            metadata=metadata,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO integrated_audit_events "
            "(run_id, sequence, event_id, event_type, case_ids_json, "
            "actor_id, reason, metadata_json, created_at, previous_hash, "
            "event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                sequence,
                event_id,
                event_type.value,
                self._json(normalized),
                actor_id,
                reason,
                self._json(metadata),
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
        event_type: IntegratedEventType,
        case_ids: tuple[str, ...],
        actor_id: str,
        reason: str,
        metadata: dict[str, Any],
        created_at: datetime,
        previous_hash: str,
    ) -> str:
        return canonical_sha256(
            {
                "sequence": sequence,
                "event_id": event_id,
                "run_id": run_id,
                "event_type": event_type.value,
                "case_ids": case_ids,
                "actor_id": actor_id,
                "reason": reason,
                "metadata": metadata,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    def _require_run(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> IntegratedRunRecord:
        return self._row_to_run(self._require_run_row(connection, run_id))

    def _require_run_row(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ):
        row = self._run_row(connection, run_id)
        if row is None:
            raise KeyError(f"Unknown integrated run: {run_id}")
        return row

    @staticmethod
    def _run_row(connection: sqlite3.Connection, run_id: str):
        return connection.execute(
            "SELECT * FROM integrated_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    def _require_case(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
    ) -> IntegratedCaseRecord:
        row = self._case_row(connection, run_id, case_id)
        if row is None:
            raise KeyError(f"Unknown integrated case: {run_id}/{case_id}")
        return self._row_to_case(row)

    @staticmethod
    def _case_row(
        connection: sqlite3.Connection,
        run_id: str,
        case_id: str,
    ):
        return connection.execute(
            "SELECT * FROM integrated_cases WHERE run_id = ? AND case_id = ?",
            (run_id, case_id),
        ).fetchone()

    def _pending_records(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        track: IntegratedTrack | None = None,
    ) -> tuple[IntegratedCaseRecord, ...]:
        query = (
            "SELECT * FROM integrated_cases WHERE run_id = ? AND status = ?"
        )
        values: list[Any] = [run_id, IntegratedCaseStatus.PENDING.value]
        if track is not None:
            query += " AND track = ?"
            values.append(track.value)
        query += " ORDER BY created_at, case_id"
        rows = connection.execute(query, tuple(values)).fetchall()
        return tuple(self._row_to_case(row) for row in rows)

    def _claimed_records(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[IntegratedCaseRecord, ...]:
        rows = self._claimed_rows(connection, run_id)
        return tuple(self._row_to_case(row) for row in rows)

    @staticmethod
    def _claimed_rows(connection: sqlite3.Connection, run_id: str):
        return connection.execute(
            "SELECT * FROM integrated_cases WHERE run_id = ? AND status = ? "
            "ORDER BY created_at, case_id",
            (run_id, IntegratedCaseStatus.CLAIMED.value),
        ).fetchall()

    @staticmethod
    def _result_row(connection: sqlite3.Connection, result_id: str):
        return connection.execute(
            "SELECT * FROM integrated_track_results WHERE result_id = ?",
            (result_id,),
        ).fetchone()

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> IntegratedRunRecord:
        return IntegratedRunRecord(
            run_id=row["run_id"],
            lineage_id=row["lineage_id"],
            policy=IntegratedRunPolicy.model_validate_json(row["policy_json"]),
            status=IntegratedRunStatus(row["status"]),
            revision=int(row["revision"]),
            round_index=int(row["round_index"]),
            skill_execution_count=int(row["skill_execution_count"]),
            policy_execution_count=int(row["policy_execution_count"]),
            terminal_decision_hash=row["terminal_decision_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
        )

    @staticmethod
    def _row_to_case(row: sqlite3.Row) -> IntegratedCaseRecord:
        return IntegratedCaseRecord(
            run_id=row["run_id"],
            case=IntegratedCase.model_validate_json(row["case_json"]),
            status=IntegratedCaseStatus(row["status"]),
            claimed_by=row["claimed_by"],
            result_id=row["result_id"],
            revision=int(row["revision"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> IntegratedTrackResult:
        return IntegratedTrackResult.model_validate_json(row["result_json"])

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> IntegratedAuditEvent:
        return IntegratedAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            run_id=row["run_id"],
            event_type=IntegratedEventType(row["event_type"]),
            case_ids=tuple(json.loads(row["case_ids_json"])),
            actor_id=row["actor_id"],
            reason=row["reason"],
            metadata=json.loads(row["metadata_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    @staticmethod
    def _require_mutable_run(run: IntegratedRunRecord) -> None:
        if run.status in {
            IntegratedRunStatus.STOPPED,
            IntegratedRunStatus.ESCALATED,
            IntegratedRunStatus.FAILED,
        }:
            raise IntegratedRepositoryConflictError(
                "Integrated run is terminal and immutable."
            )

    @staticmethod
    def _check_run_revision(
        run: IntegratedRunRecord,
        expected_revision: int,
    ) -> None:
        if run.revision != expected_revision:
            raise StaleIntegratedRevision(
                f"Expected integrated revision {expected_revision}, found {run.revision}."
            )

    @staticmethod
    def _normalize_case_ids(case_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not case_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError(
                "Integrated claim requires unique non-empty case IDs."
            )
        return tuple(sorted(case_ids))

    @staticmethod
    def _effective_now(value: datetime | None) -> datetime:
        effective = value or datetime.now(timezone.utc)
        if effective.tzinfo is None or effective.utcoffset() is None:
            raise ValueError(
                "Integrated Repository time must include a timezone."
            )
        if effective > datetime.now(timezone.utc):
            raise ValueError(
                "Integrated Repository time must not be in the future."
            )
        return effective

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


__all__ = [
    "IntegratedAuditIntegrityError",
    "IntegratedRepositoryConflictError",
    "SQLiteIntegratedEvolutionRepository",
    "StaleIntegratedRevision",
]
