from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.skills.models import (
    SkillEvaluationDecision,
    SkillEventType,
    SkillSpec,
    SkillVersionRecord,
    SkillVersionStatus,
)
from evoagent.skills.persistent_models import PersistentSkillEvent, SkillRegistryCheckpoint


_GENESIS_HASH = "0" * 64


class SkillRegistryConflictError(RuntimeError):
    pass


class StaleSkillRevision(RuntimeError):
    pass


class SkillAuditIntegrityError(RuntimeError):
    pass


def skill_content_hash(spec: SkillSpec) -> str:
    payload = json.dumps(
        spec.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SQLiteSkillRegistry:
    """Transactional, restart-safe implementation of the immutable Skill lifecycle."""

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
                CREATE TABLE IF NOT EXISTS skill_versions (
                    skill_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    parent_version TEXT,
                    status TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    evaluation_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(skill_id, version)
                );

                CREATE TABLE IF NOT EXISTS skill_heads (
                    skill_id TEXT PRIMARY KEY,
                    active_version TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(skill_id, active_version)
                        REFERENCES skill_versions(skill_id, version)
                );

                CREATE TABLE IF NOT EXISTS skill_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    from_version TEXT,
                    to_version TEXT,
                    reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def register_initial(
        self,
        spec: SkillSpec,
        *,
        reason: str = "initial registration",
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT 1 FROM skill_heads WHERE skill_id = ?", (spec.skill_id,)
                ).fetchone()
                if existing:
                    raise SkillRegistryConflictError(f"Skill already registered: {spec.skill_id}")
                self._insert_version(
                    connection,
                    SkillVersionRecord(
                        spec=spec,
                        parent_version=None,
                        status=SkillVersionStatus.ACTIVE,
                        content_hash=skill_content_hash(spec),
                    ),
                    created_at=now,
                )
                connection.execute(
                    "INSERT INTO skill_heads (skill_id, active_version, revision, updated_at) "
                    "VALUES (?, ?, 0, ?)",
                    (spec.skill_id, spec.version, now.isoformat()),
                )
                self._append_event(
                    connection,
                    event_type=SkillEventType.REGISTERED.value,
                    spec=spec,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def add_candidate(
        self,
        spec: SkillSpec,
        *,
        parent_version: str,
        reason: str,
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if self._version_row(connection, spec.skill_id, spec.version):
                    raise SkillRegistryConflictError(
                        f"Duplicate Skill version: {spec.skill_id}@{spec.version}"
                    )
                if not self._version_row(connection, spec.skill_id, parent_version):
                    raise ValueError(f"Unknown parent version: {spec.skill_id}@{parent_version}")
                if not self._head_row(connection, spec.skill_id):
                    raise KeyError(f"Unknown Skill: {spec.skill_id}")
                self._insert_version(
                    connection,
                    SkillVersionRecord(
                        spec=spec,
                        parent_version=parent_version,
                        status=SkillVersionStatus.CANDIDATE,
                        content_hash=skill_content_hash(spec),
                    ),
                    created_at=now,
                )
                self._append_event(
                    connection,
                    event_type=SkillEventType.CANDIDATE_CREATED.value,
                    spec=spec,
                    from_version=parent_version,
                    to_version=spec.version,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def promote(
        self,
        skill_id: str,
        version: str,
        decision: SkillEvaluationDecision,
        *,
        expected_active_revision: int | None = None,
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> None:
        if not decision.promote:
            raise ValueError("A rejected evaluation decision cannot promote a candidate.")
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, skill_id)
                self._check_revision(head, expected_active_revision)
                candidate = self._require_record(connection, skill_id, version)
                if candidate.status != SkillVersionStatus.CANDIDATE:
                    raise ValueError("Only candidate versions can be promoted.")
                if (
                    decision.skill_id != skill_id
                    or decision.candidate_version != version
                    or decision.base_version != head["active_version"]
                    or candidate.parent_version != head["active_version"]
                ):
                    raise ValueError("Evaluation decision or parent does not match the active Skill.")

                previous_version = head["active_version"]
                connection.execute(
                    "UPDATE skill_versions SET status = ? WHERE skill_id = ? AND version = ?",
                    (SkillVersionStatus.SUPERSEDED.value, skill_id, previous_version),
                )
                connection.execute(
                    "UPDATE skill_versions SET status = ?, evaluation_json = ? "
                    "WHERE skill_id = ? AND version = ?",
                    (
                        SkillVersionStatus.ACTIVE.value,
                        self._json(decision.model_dump(mode="json")),
                        skill_id,
                        version,
                    ),
                )
                changed = connection.execute(
                    "UPDATE skill_heads SET active_version = ?, revision = revision + 1, "
                    "updated_at = ? WHERE skill_id = ? AND revision = ?",
                    (version, now.isoformat(), skill_id, head["revision"]),
                ).rowcount
                if changed != 1:
                    raise StaleSkillRevision("Active Skill revision changed concurrently.")
                self._append_event(
                    connection,
                    event_type=SkillEventType.PROMOTED.value,
                    spec=candidate.spec,
                    from_version=previous_version,
                    to_version=version,
                    reason=decision.reason,
                    metadata={"regression_count": decision.regression_count},
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def reject(
        self,
        skill_id: str,
        version: str,
        decision: SkillEvaluationDecision,
        *,
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> None:
        if decision.promote:
            raise ValueError("A passing evaluation decision cannot reject a candidate.")
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                candidate = self._require_record(connection, skill_id, version)
                if candidate.status != SkillVersionStatus.CANDIDATE:
                    raise ValueError("Only candidate versions can be rejected.")
                if decision.skill_id != skill_id or decision.candidate_version != version:
                    raise ValueError("Evaluation decision does not match the candidate.")
                connection.execute(
                    "UPDATE skill_versions SET status = ?, evaluation_json = ? "
                    "WHERE skill_id = ? AND version = ?",
                    (
                        SkillVersionStatus.REJECTED.value,
                        self._json(decision.model_dump(mode="json")),
                        skill_id,
                        version,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=SkillEventType.REJECTED.value,
                    spec=candidate.spec,
                    reason=decision.reason,
                    metadata={"regression_count": decision.regression_count},
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def rollback(
        self,
        skill_id: str,
        to_version: str,
        *,
        reason: str,
        expected_active_revision: int | None = None,
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, skill_id)
                self._check_revision(head, expected_active_revision)
                current_version = head["active_version"]
                if current_version == to_version:
                    raise ValueError("Requested rollback target is already active.")
                target = self._require_record(connection, skill_id, to_version)
                if target.status not in {
                    SkillVersionStatus.ACTIVE,
                    SkillVersionStatus.SUPERSEDED,
                }:
                    raise ValueError("Rollback target must be a previously active stable version.")
                connection.execute(
                    "UPDATE skill_versions SET status = ? WHERE skill_id = ? AND version = ?",
                    (SkillVersionStatus.SUPERSEDED.value, skill_id, current_version),
                )
                connection.execute(
                    "UPDATE skill_versions SET status = ? WHERE skill_id = ? AND version = ?",
                    (SkillVersionStatus.ACTIVE.value, skill_id, to_version),
                )
                changed = connection.execute(
                    "UPDATE skill_heads SET active_version = ?, revision = revision + 1, "
                    "updated_at = ? WHERE skill_id = ? AND revision = ?",
                    (to_version, now.isoformat(), skill_id, head["revision"]),
                ).rowcount
                if changed != 1:
                    raise StaleSkillRevision("Active Skill revision changed concurrently.")
                self._append_event(
                    connection,
                    event_type=SkillEventType.ROLLED_BACK.value,
                    spec=target.spec,
                    from_version=current_version,
                    to_version=to_version,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def active(self, skill_id: str) -> SkillVersionRecord:
        with self._connection() as connection:
            head = self._require_head(connection, skill_id)
            return self._require_record(connection, skill_id, head["active_version"])

    def active_revision(self, skill_id: str) -> int:
        with self._connection() as connection:
            return int(self._require_head(connection, skill_id)["revision"])

    def get(self, skill_id: str, version: str) -> SkillVersionRecord:
        with self._connection() as connection:
            return self._require_record(connection, skill_id, version)

    def list_versions(self, skill_id: str) -> list[SkillVersionRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY created_at, version",
                (skill_id,),
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

    def list_skill_ids(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute("SELECT skill_id FROM skill_heads ORDER BY skill_id").fetchall()
            return [row["skill_id"] for row in rows]

    def active_versions(self) -> dict[str, str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT skill_id, active_version FROM skill_heads ORDER BY skill_id"
            ).fetchall()
            return {row["skill_id"]: row["active_version"] for row in rows}

    def active_revisions(self) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT skill_id, revision FROM skill_heads ORDER BY skill_id"
            ).fetchall()
            return {row["skill_id"]: int(row["revision"]) for row in rows}

    def events(self, skill_id: str | None = None) -> list[PersistentSkillEvent]:
        with self._connection() as connection:
            if skill_id is None:
                rows = connection.execute(
                    "SELECT * FROM skill_audit_events ORDER BY sequence"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM skill_audit_events WHERE skill_id = ? ORDER BY sequence",
                    (skill_id,),
                ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> SkillRegistryCheckpoint:
        events = self.events()
        return SkillRegistryCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(self, checkpoint: SkillRegistryCheckpoint | None = None) -> bool:
        events = self.events()
        previous = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous:
                raise SkillAuditIntegrityError("Skill audit sequence or hash chain is broken.")
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                skill_id=event.skill_id,
                version=event.version,
                from_version=event.from_version,
                to_version=event.to_version,
                reason=event.reason,
                metadata=event.metadata,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise SkillAuditIntegrityError("Skill audit event content was modified.")
            previous = event.event_hash
        if checkpoint is not None:
            current = SkillRegistryCheckpoint(event_count=len(events), head_hash=previous)
            if current != checkpoint:
                raise SkillAuditIntegrityError(
                    "Skill audit does not match the externally anchored checkpoint."
                )
        return True

    def is_empty(self) -> bool:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM skill_versions").fetchone()
            return row["count"] == 0

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        record: SkillVersionRecord,
        *,
        created_at: datetime,
    ) -> None:
        connection.execute(
            "INSERT INTO skill_versions (skill_id, version, spec_json, parent_version, status, "
            "content_hash, evaluation_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.spec.skill_id,
                record.spec.version,
                self._json(record.spec.model_dump(mode="json")),
                record.parent_version,
                record.status.value,
                record.content_hash,
                (
                    self._json(record.evaluation.model_dump(mode="json"))
                    if record.evaluation
                    else None
                ),
                created_at.isoformat(),
            ),
        )

    @staticmethod
    def _check_revision(head: sqlite3.Row, expected: int | None) -> None:
        if expected is not None and head["revision"] != expected:
            raise StaleSkillRevision(
                f"Expected active revision {expected}, found {head['revision']}."
            )

    @staticmethod
    def _head_row(connection: sqlite3.Connection, skill_id: str):
        return connection.execute(
            "SELECT * FROM skill_heads WHERE skill_id = ?", (skill_id,)
        ).fetchone()

    def _require_head(self, connection: sqlite3.Connection, skill_id: str):
        row = self._head_row(connection, skill_id)
        if row is None:
            raise KeyError(f"Unknown Skill: {skill_id}")
        return row

    @staticmethod
    def _version_row(connection: sqlite3.Connection, skill_id: str, version: str):
        return connection.execute(
            "SELECT * FROM skill_versions WHERE skill_id = ? AND version = ?",
            (skill_id, version),
        ).fetchone()

    def _require_record(
        self, connection: sqlite3.Connection, skill_id: str, version: str
    ) -> SkillVersionRecord:
        row = self._version_row(connection, skill_id, version)
        if row is None:
            raise KeyError(f"Unknown Skill version: {skill_id}@{version}")
        return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SkillVersionRecord:
        evaluation = (
            SkillEvaluationDecision.model_validate(json.loads(row["evaluation_json"]))
            if row["evaluation_json"]
            else None
        )
        return SkillVersionRecord(
            spec=SkillSpec.model_validate(json.loads(row["spec_json"])),
            parent_version=row["parent_version"],
            status=SkillVersionStatus(row["status"]),
            content_hash=row["content_hash"],
            evaluation=evaluation,
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        spec: SkillSpec,
        reason: str,
        actor_id: str,
        created_at: datetime,
        from_version: str | None = None,
        to_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM skill_audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = last["sequence"] + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"skill-event:{uuid.uuid4()}"
        metadata = metadata or {}
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            skill_id=spec.skill_id,
            version=spec.version,
            from_version=from_version,
            to_version=to_version,
            reason=reason,
            metadata=metadata,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO skill_audit_events (sequence, event_id, event_type, skill_id, version, "
            "from_version, to_version, reason, metadata_json, actor_id, created_at, "
            "previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type,
                spec.skill_id,
                spec.version,
                from_version,
                to_version,
                reason,
                self._json(metadata),
                actor_id,
                created_at.isoformat(),
                previous_hash,
                event_hash,
            ),
        )

    @classmethod
    def _event_hash(
        cls,
        *,
        sequence: int,
        event_id: str,
        event_type: str,
        skill_id: str,
        version: str,
        from_version: str | None,
        to_version: str | None,
        reason: str,
        metadata: dict[str, Any],
        actor_id: str,
        created_at: datetime,
        previous_hash: str,
    ) -> str:
        canonical = cls._json(
            {
                "sequence": sequence,
                "event_id": event_id,
                "event_type": event_type,
                "skill_id": skill_id,
                "version": version,
                "from_version": from_version,
                "to_version": to_version,
                "reason": reason,
                "metadata": metadata,
                "actor_id": actor_id,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> PersistentSkillEvent:
        return PersistentSkillEvent(
            sequence=row["sequence"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            skill_id=row["skill_id"],
            version=row["version"],
            from_version=row["from_version"],
            to_version=row["to_version"],
            reason=row["reason"],
            metadata=json.loads(row["metadata_json"]),
            actor_id=row["actor_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
