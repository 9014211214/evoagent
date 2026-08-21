from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.local_rl.models import (
    LocalRLAuditEvent,
    LocalRLEvaluationReport,
    LocalRLEventType,
    LocalRLRegistryCheckpoint,
    LocalRLRunManifest,
    LocalRLSelectionDecision,
    LocalRLTrainingResult,
)
from evoagent.model_registry.models import canonical_sha256


_GENESIS_HASH = "0" * 64


class LocalRLRepositoryConflictError(RuntimeError):
    pass


class LocalRLAuditIntegrityError(RuntimeError):
    pass


class SQLiteLocalRLRepository:
    """Immutable restart-safe storage for one or more local optimization runs."""

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
                CREATE TABLE IF NOT EXISTS local_rl_runs (
                    run_id TEXT PRIMARY KEY,
                    manifest_hash TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    training_result_hash TEXT,
                    training_result_json TEXT,
                    baseline_report_hash TEXT,
                    baseline_report_json TEXT,
                    candidate_reports_json TEXT,
                    decision_hash TEXT,
                    decision_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_rl_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def register_manifest(
        self,
        manifest: LocalRLRunManifest,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._run_row(connection, manifest.run_id)
                if row is not None:
                    stored = LocalRLRunManifest.model_validate_json(
                        row["manifest_json"]
                    )
                    if stored != manifest:
                        raise LocalRLRepositoryConflictError(
                            "Local RL run ID already contains another manifest."
                        )
                    connection.commit()
                    return True
                connection.execute(
                    "INSERT INTO local_rl_runs "
                    "(run_id, manifest_hash, manifest_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        manifest.run_id,
                        manifest.manifest_hash,
                        manifest.model_dump_json(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=LocalRLEventType.RUN_REGISTERED,
                    run_id=manifest.run_id,
                    actor_id=actor_id,
                    reason="Frozen local RL run manifest registered.",
                    payload={"manifest_hash": manifest.manifest_hash},
                    created_at=now,
                )
                connection.commit()
                return False
            except Exception:
                connection.rollback()
                raise

    def store_training(
        self,
        result: LocalRLTrainingResult,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._require_run(connection, result.run_id)
                if row["manifest_hash"] != result.manifest_hash:
                    raise LocalRLRepositoryConflictError(
                        "Local RL training result belongs to another manifest."
                    )
                if row["training_result_json"] is not None:
                    stored = LocalRLTrainingResult.model_validate_json(
                        row["training_result_json"]
                    )
                    if stored != result:
                        raise LocalRLRepositoryConflictError(
                            "Local RL run already contains another training result."
                        )
                    connection.commit()
                    return True
                connection.execute(
                    "UPDATE local_rl_runs SET training_result_hash = ?, "
                    "training_result_json = ?, updated_at = ? WHERE run_id = ?",
                    (
                        result.result_hash,
                        result.model_dump_json(),
                        now.isoformat(),
                        result.run_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=LocalRLEventType.TRAINING_COMPLETED,
                    run_id=result.run_id,
                    actor_id=actor_id,
                    reason="Bounded local rollout optimization completed.",
                    payload={
                        "training_result_hash": result.result_hash,
                        "initial_checkpoint_hash": (
                            result.initial_checkpoint.checkpoint_hash
                        ),
                        "final_checkpoint_hash": (
                            result.retained_checkpoints[-1].checkpoint_hash
                        ),
                        "iterations": result.usage.iterations,
                        "rollouts": result.usage.rollouts,
                        "episode_steps": result.usage.episode_steps,
                        "parameter_updates": result.usage.parameter_updates,
                    },
                    created_at=now,
                )
                connection.commit()
                return False
            except Exception:
                connection.rollback()
                raise

    def store_evaluations(
        self,
        run_id: str,
        *,
        baseline: LocalRLEvaluationReport,
        candidates: tuple[LocalRLEvaluationReport, ...],
        actor_id: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        if baseline.run_id != run_id or any(
            item.run_id != run_id for item in candidates
        ):
            raise LocalRLRepositoryConflictError(
                "Local RL evaluations belong to another run."
            )
        candidate_hashes = [item.checkpoint_hash for item in candidates]
        if len(set(candidate_hashes)) != len(candidate_hashes):
            raise LocalRLRepositoryConflictError(
                "Local RL candidate evaluation checkpoints must be unique."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._require_run(connection, run_id)
                if row["training_result_json"] is None:
                    raise LocalRLRepositoryConflictError(
                        "Local RL evaluations require a stored training result."
                    )
                if row["baseline_report_json"] is not None:
                    stored_baseline = LocalRLEvaluationReport.model_validate_json(
                        row["baseline_report_json"]
                    )
                    stored_candidates = tuple(
                        LocalRLEvaluationReport.model_validate(item)
                        for item in json.loads(row["candidate_reports_json"])
                    )
                    if stored_baseline != baseline or stored_candidates != candidates:
                        raise LocalRLRepositoryConflictError(
                            "Local RL run already contains different evaluations."
                        )
                    connection.commit()
                    return True
                connection.execute(
                    "UPDATE local_rl_runs SET baseline_report_hash = ?, "
                    "baseline_report_json = ?, candidate_reports_json = ?, "
                    "updated_at = ? WHERE run_id = ?",
                    (
                        baseline.report_hash,
                        baseline.model_dump_json(),
                        self._json(
                            [item.model_dump(mode="json") for item in candidates]
                        ),
                        now.isoformat(),
                        run_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=LocalRLEventType.EVALUATION_STORED,
                    run_id=run_id,
                    actor_id=actor_id,
                    reason="Independent frozen held-out evaluations stored.",
                    payload={
                        "baseline_report_hash": baseline.report_hash,
                        "candidate_report_hashes": [
                            item.report_hash for item in candidates
                        ],
                        "task_manifest_hash": baseline.task_manifest_hash,
                    },
                    created_at=now,
                )
                connection.commit()
                return False
            except Exception:
                connection.rollback()
                raise

    def store_decision(
        self,
        decision: LocalRLSelectionDecision,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._require_run(connection, decision.run_id)
                if row["baseline_report_json"] is None:
                    raise LocalRLRepositoryConflictError(
                        "Local RL decision requires stored evaluations."
                    )
                if row["manifest_hash"] != decision.manifest_hash:
                    raise LocalRLRepositoryConflictError(
                        "Local RL decision belongs to another manifest."
                    )
                if row["decision_json"] is not None:
                    stored = LocalRLSelectionDecision.model_validate_json(
                        row["decision_json"]
                    )
                    if stored != decision:
                        raise LocalRLRepositoryConflictError(
                            "Local RL run already contains another decision."
                        )
                    connection.commit()
                    return True
                connection.execute(
                    "UPDATE local_rl_runs SET decision_hash = ?, decision_json = ?, "
                    "updated_at = ? WHERE run_id = ?",
                    (
                        decision.decision_hash,
                        decision.model_dump_json(),
                        now.isoformat(),
                        decision.run_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=LocalRLEventType.SELECTION_STORED,
                    run_id=decision.run_id,
                    actor_id=actor_id,
                    reason="Best safe improving local policy checkpoint selected.",
                    payload={
                        "decision_hash": decision.decision_hash,
                        "selected_checkpoint_hash": (
                            decision.selected_checkpoint_hash
                        ),
                        "selected_iteration": decision.selected_iteration,
                    },
                    created_at=now,
                )
                connection.commit()
                return False
            except Exception:
                connection.rollback()
                raise

    def load_manifest(self, run_id: str) -> LocalRLRunManifest:
        with self._connection() as connection:
            row = self._require_run(connection, run_id)
            return LocalRLRunManifest.model_validate_json(row["manifest_json"])

    def load_training(self, run_id: str) -> LocalRLTrainingResult:
        with self._connection() as connection:
            row = self._require_run(connection, run_id)
            if row["training_result_json"] is None:
                raise KeyError(f"Local RL training result is absent: {run_id}")
            return LocalRLTrainingResult.model_validate_json(
                row["training_result_json"]
            )

    def load_evaluations(
        self,
        run_id: str,
    ) -> tuple[LocalRLEvaluationReport, tuple[LocalRLEvaluationReport, ...]]:
        with self._connection() as connection:
            row = self._require_run(connection, run_id)
            if row["baseline_report_json"] is None:
                raise KeyError(f"Local RL evaluations are absent: {run_id}")
            baseline = LocalRLEvaluationReport.model_validate_json(
                row["baseline_report_json"]
            )
            candidates = tuple(
                LocalRLEvaluationReport.model_validate(item)
                for item in json.loads(row["candidate_reports_json"])
            )
            return baseline, candidates

    def load_decision(self, run_id: str) -> LocalRLSelectionDecision:
        with self._connection() as connection:
            row = self._require_run(connection, run_id)
            if row["decision_json"] is None:
                raise KeyError(f"Local RL decision is absent: {run_id}")
            return LocalRLSelectionDecision.model_validate_json(
                row["decision_json"]
            )

    def is_complete(self, run_id: str) -> bool:
        with self._connection() as connection:
            row = self._run_row(connection, run_id)
            return bool(
                row is not None
                and row["training_result_json"] is not None
                and row["baseline_report_json"] is not None
                and row["decision_json"] is not None
            )

    def events(self) -> tuple[LocalRLAuditEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM local_rl_audit_events ORDER BY sequence"
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)

    def checkpoint(self) -> LocalRLRegistryCheckpoint:
        events = self.events()
        return LocalRLRegistryCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(
        self,
        checkpoint: LocalRLRegistryCheckpoint | None = None,
    ) -> bool:
        events = self.events()
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise LocalRLAuditIntegrityError(
                    "Local RL audit sequence or hash chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                run_id=event.run_id,
                actor_id=event.actor_id,
                reason=event.reason,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise LocalRLAuditIntegrityError(
                    "Local RL audit event content was modified."
                )
            previous_hash = event.event_hash
        current = LocalRLRegistryCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint is not None and current != checkpoint:
            raise LocalRLAuditIntegrityError(
                "Local RL audit does not match the external checkpoint."
            )
        return True

    def verify_state(self, run_id: str) -> bool:
        manifest = self.load_manifest(run_id)
        training = self.load_training(run_id)
        baseline, candidates = self.load_evaluations(run_id)
        decision = self.load_decision(run_id)
        if training.manifest_hash != manifest.manifest_hash:
            raise LocalRLRepositoryConflictError(
                "Stored Local RL training differs from its manifest."
            )
        if baseline.checkpoint_hash != training.initial_checkpoint.checkpoint_hash:
            raise LocalRLRepositoryConflictError(
                "Stored Local RL baseline differs from P0."
            )
        retained_hashes = {
            item.checkpoint_hash for item in training.retained_checkpoints
        }
        if {item.checkpoint_hash for item in candidates} != retained_hashes:
            raise LocalRLRepositoryConflictError(
                "Stored Local RL evaluations differ from retained checkpoints."
            )
        if decision.selected_checkpoint_hash not in retained_hashes:
            raise LocalRLRepositoryConflictError(
                "Stored Local RL decision selected an unknown checkpoint."
            )
        self.verify_audit()
        return True

    @staticmethod
    def _run_row(connection: sqlite3.Connection, run_id: str):
        return connection.execute(
            "SELECT * FROM local_rl_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    def _require_run(self, connection: sqlite3.Connection, run_id: str):
        row = self._run_row(connection, run_id)
        if row is None:
            raise KeyError(f"Unknown Local RL run: {run_id}")
        return row

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: LocalRLEventType,
        run_id: str,
        actor_id: str,
        reason: str,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM local_rl_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"local-rl-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            run_id=run_id,
            actor_id=actor_id,
            reason=reason,
            payload=payload,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO local_rl_audit_events "
            "(sequence, event_id, event_type, run_id, actor_id, reason, "
            "payload_json, created_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type.value,
                run_id,
                actor_id,
                reason,
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
        event_type: LocalRLEventType,
        run_id: str,
        actor_id: str,
        reason: str,
        payload: dict[str, Any],
        created_at: datetime,
        previous_hash: str,
    ) -> str:
        return canonical_sha256(
            {
                "sequence": sequence,
                "event_id": event_id,
                "event_type": event_type.value,
                "run_id": run_id,
                "actor_id": actor_id,
                "reason": reason,
                "payload": payload,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LocalRLAuditEvent:
        return LocalRLAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            event_type=LocalRLEventType(row["event_type"]),
            run_id=row["run_id"],
            actor_id=row["actor_id"],
            reason=row["reason"],
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
    "LocalRLAuditIntegrityError",
    "LocalRLRepositoryConflictError",
    "SQLiteLocalRLRepository",
]
