from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import TypeAdapter

from evoagent.benchmark_evidence.models import (
    BenchmarkComparisonMode,
    BenchmarkComparisonReport,
    BenchmarkEvidenceAuditEvent,
    BenchmarkEvidenceCheckpoint,
    BenchmarkEvidenceEventType,
    BenchmarkRunEvidence,
)
from evoagent.model_registry.models import canonical_sha256


_GENESIS_HASH = "0" * 64
_COMPARISON_ADAPTER = TypeAdapter(BenchmarkComparisonReport)


class BenchmarkEvidenceConflictError(RuntimeError):
    pass


class BenchmarkEvidenceAuditIntegrityError(RuntimeError):
    pass


class SQLiteBenchmarkEvidenceRepository:
    """Immutable Harbor evidence, comparison reports, and hash-chained audit."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    evidence_id TEXT PRIMARY KEY,
                    raw_file_sha256 TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS benchmark_comparisons (
                    comparison_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS benchmark_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def import_run(
        self,
        run: BenchmarkRunEvidence,
        *,
        actor_id: str = "benchmark-evidence-importer",
        now: datetime | None = None,
    ) -> tuple[BenchmarkRunEvidence, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT run_json FROM benchmark_runs WHERE evidence_id = ?",
                    (run.evidence_id,),
                ).fetchone()
                if row is not None:
                    existing = BenchmarkRunEvidence.model_validate(
                        json.loads(row["run_json"])
                    )
                    if existing != run:
                        raise BenchmarkEvidenceConflictError(
                            "Conflicting benchmark evidence under the same evidence ID."
                        )
                    connection.commit()
                    return existing, True
                connection.execute(
                    "INSERT INTO benchmark_runs (evidence_id, raw_file_sha256, "
                    "evidence_hash, run_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        run.evidence_id,
                        run.source_file_sha256,
                        run.evidence_hash,
                        self._json(run.model_dump(mode="json")),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=BenchmarkEvidenceEventType.RUN_IMPORTED,
                    subject_id=run.evidence_id,
                    payload={
                        "evidence_hash": run.evidence_hash,
                        "raw_file_sha256": run.source_file_sha256,
                        "contract_hash": run.contract.contract_hash,
                    },
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
                return run, False
            except Exception:
                connection.rollback()
                raise

    def store_comparison(
        self,
        report: BenchmarkComparisonReport,
        *,
        actor_id: str = "benchmark-comparator",
        now: datetime | None = None,
    ) -> tuple[BenchmarkComparisonReport, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT report_json FROM benchmark_comparisons "
                    "WHERE comparison_id = ?",
                    (report.comparison_id,),
                ).fetchone()
                if row is not None:
                    existing = _COMPARISON_ADAPTER.validate_python(
                        json.loads(row["report_json"])
                    )
                    if existing != report:
                        raise BenchmarkEvidenceConflictError(
                            "Conflicting benchmark comparison under the same ID."
                        )
                    connection.commit()
                    return existing, True
                event_type = (
                    BenchmarkEvidenceEventType.LONGITUDINAL_COMPARISON_STORED
                    if report.mode == BenchmarkComparisonMode.LONGITUDINAL
                    else BenchmarkEvidenceEventType.SAME_MODEL_COMPARISON_STORED
                )
                connection.execute(
                    "INSERT INTO benchmark_comparisons (comparison_id, mode, "
                    "report_hash, report_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        report.comparison_id,
                        report.mode.value,
                        report.report_hash,
                        self._json(report.model_dump(mode="json")),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=event_type,
                    subject_id=report.comparison_id,
                    payload={
                        "mode": report.mode.value,
                        "report_hash": report.report_hash,
                        "run_ids": list(report.run_ids),
                    },
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
                return report, False
            except Exception:
                connection.rollback()
                raise

    def get_run(self, evidence_id: str) -> BenchmarkRunEvidence:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_json FROM benchmark_runs WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown benchmark evidence: {evidence_id}")
            return BenchmarkRunEvidence.model_validate(json.loads(row["run_json"]))

    def list_runs(self) -> list[BenchmarkRunEvidence]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_json FROM benchmark_runs ORDER BY evidence_id"
            ).fetchall()
            return [
                BenchmarkRunEvidence.model_validate(json.loads(row["run_json"]))
                for row in rows
            ]

    def get_comparison(self, comparison_id: str) -> BenchmarkComparisonReport:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT report_json FROM benchmark_comparisons "
                "WHERE comparison_id = ?",
                (comparison_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown benchmark comparison: {comparison_id}")
            return _COMPARISON_ADAPTER.validate_python(
                json.loads(row["report_json"])
            )

    def list_comparisons(self) -> list[BenchmarkComparisonReport]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT report_json FROM benchmark_comparisons "
                "ORDER BY comparison_id"
            ).fetchall()
            return [
                _COMPARISON_ADAPTER.validate_python(
                    json.loads(row["report_json"])
                )
                for row in rows
            ]

    def events(self) -> list[BenchmarkEvidenceAuditEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM benchmark_audit_events ORDER BY sequence"
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> BenchmarkEvidenceCheckpoint:
        events = self.events()
        return BenchmarkEvidenceCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(
        self,
        checkpoint: BenchmarkEvidenceCheckpoint | None = None,
    ) -> bool:
        events = self.events()
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise BenchmarkEvidenceAuditIntegrityError(
                    "Benchmark evidence audit sequence or hash chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                subject_id=event.subject_id,
                payload=event.payload,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise BenchmarkEvidenceAuditIntegrityError(
                    "Benchmark evidence audit event content was modified."
                )
            previous_hash = event.event_hash
        current = BenchmarkEvidenceCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint is not None and current != checkpoint:
            raise BenchmarkEvidenceAuditIntegrityError(
                "Benchmark evidence audit does not match the external checkpoint."
            )
        return True

    def verify_state(self) -> bool:
        runs = {item.evidence_id: item for item in self.list_runs()}
        comparisons = self.list_comparisons()
        if len(runs) != len(self.list_runs()):
            raise BenchmarkEvidenceConflictError(
                "Benchmark Evidence Registry contains duplicate run IDs."
            )
        for report in comparisons:
            missing = set(report.run_ids) - set(runs)
            if missing:
                raise BenchmarkEvidenceConflictError(
                    "Benchmark comparison references missing run evidence."
                )
            if report.mode == BenchmarkComparisonMode.SAME_MODEL_CROSS_AGENT:
                if report.anchor_run_id not in runs:
                    raise BenchmarkEvidenceConflictError(
                        "Same-model comparison anchor evidence is missing."
                    )
        self.verify_audit()
        return True

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: BenchmarkEvidenceEventType,
        subject_id: str,
        payload: dict[str, Any],
        actor_id: str,
        created_at: datetime,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM benchmark_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"benchmark-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            subject_id=subject_id,
            payload=payload,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO benchmark_audit_events (sequence, event_id, event_type, "
            "subject_id, payload_json, actor_id, created_at, previous_hash, "
            "event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type.value,
                subject_id,
                self._json(payload),
                actor_id,
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
        event_type: BenchmarkEvidenceEventType,
        subject_id: str,
        payload: dict[str, Any],
        actor_id: str,
        created_at: datetime,
        previous_hash: str,
    ) -> str:
        return canonical_sha256(
            {
                "sequence": sequence,
                "event_id": event_id,
                "event_type": event_type.value,
                "subject_id": subject_id,
                "payload": payload,
                "actor_id": actor_id,
                "created_at": created_at,
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> BenchmarkEvidenceAuditEvent:
        return BenchmarkEvidenceAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            event_type=BenchmarkEvidenceEventType(row["event_type"]),
            subject_id=row["subject_id"],
            payload=json.loads(row["payload_json"]),
            actor_id=row["actor_id"],
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
    "BenchmarkEvidenceAuditIntegrityError",
    "BenchmarkEvidenceConflictError",
    "SQLiteBenchmarkEvidenceRepository",
]
