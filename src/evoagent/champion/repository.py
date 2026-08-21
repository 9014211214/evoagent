from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from evoagent.champion.models import (
    ChampionAuditEvent,
    ChampionEventType,
    ChampionRegistryCheckpoint,
    ChampionSelectionDecision,
    ChampionSnapshotRecord,
    ChampionVersionStatus,
)
from evoagent.champion.repository_lifecycle import (
    ChampionRegistryLifecycleMixin,
)
from evoagent.model_registry.models import canonical_sha256


_GENESIS_HASH = "0" * 64


class ChampionRegistryConflictError(RuntimeError):
    pass


class StaleChampionRevision(RuntimeError):
    pass


class ChampionAuditIntegrityError(RuntimeError):
    pass


class SQLiteChampionRegistry(ChampionRegistryLifecycleMixin):
    """Persistent immutable Agent snapshots with one active Champion pointer."""

    conflict_error = ChampionRegistryConflictError
    stale_error = StaleChampionRevision

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
                CREATE TABLE IF NOT EXISTS champion_decisions (
                    decision_id TEXT PRIMARY KEY,
                    decision_hash TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS champion_snapshots (
                    family_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    benchmark_evidence_hash TEXT NOT NULL,
                    benchmark_package_hash TEXT NOT NULL,
                    parent_snapshot_id TEXT,
                    status TEXT NOT NULL,
                    decision_id TEXT,
                    decision_hash TEXT,
                    policy_hash TEXT,
                    campaign_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(family_id, snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS champion_heads (
                    family_id TEXT PRIMARY KEY,
                    active_snapshot_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(family_id, active_snapshot_id)
                        REFERENCES champion_snapshots(family_id, snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS champion_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    from_snapshot_id TEXT,
                    to_snapshot_id TEXT,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def get_decision(self, decision_id: str) -> ChampionSelectionDecision:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT decision_json FROM champion_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown Champion decision: {decision_id}")
            return ChampionSelectionDecision.model_validate_json(
                row["decision_json"]
            )

    def list_decisions(self) -> list[ChampionSelectionDecision]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT decision_json FROM champion_decisions ORDER BY decision_id"
            ).fetchall()
            return [
                ChampionSelectionDecision.model_validate_json(
                    row["decision_json"]
                )
                for row in rows
            ]

    def active(self, family_id: str) -> ChampionSnapshotRecord:
        with self._connection() as connection:
            head = self._require_head(connection, family_id)
            return self._require_record(
                connection,
                family_id,
                head["active_snapshot_id"],
            )

    def active_revision(self, family_id: str) -> int:
        with self._connection() as connection:
            return int(self._require_head(connection, family_id)["revision"])

    def get(self, family_id: str, snapshot_id: str) -> ChampionSnapshotRecord:
        with self._connection() as connection:
            return self._require_record(connection, family_id, snapshot_id)

    def list_snapshots(self, family_id: str) -> list[ChampionSnapshotRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM champion_snapshots WHERE family_id = ? "
                "ORDER BY created_at, snapshot_id",
                (family_id,),
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

    def events(self) -> list[ChampionAuditEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM champion_audit_events ORDER BY sequence"
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> ChampionRegistryCheckpoint:
        events = self.events()
        return ChampionRegistryCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(
        self,
        checkpoint: ChampionRegistryCheckpoint | None = None,
    ) -> bool:
        events = self.events()
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
            ):
                raise ChampionAuditIntegrityError(
                    "Champion audit sequence or hash chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                family_id=event.family_id,
                snapshot_id=event.snapshot_id,
                from_snapshot_id=event.from_snapshot_id,
                to_snapshot_id=event.to_snapshot_id,
                reason=event.reason,
                payload=event.payload,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise ChampionAuditIntegrityError(
                    "Champion audit event content was modified."
                )
            previous_hash = event.event_hash
        current = ChampionRegistryCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint is not None and current != checkpoint:
            raise ChampionAuditIntegrityError(
                "Champion audit does not match the external checkpoint."
            )
        return True

    def verify_state(self) -> bool:
        with self._connection() as connection:
            heads = connection.execute(
                "SELECT family_id, active_snapshot_id FROM champion_heads"
            ).fetchall()
            for head in heads:
                rows = connection.execute(
                    "SELECT * FROM champion_snapshots WHERE family_id = ?",
                    (head["family_id"],),
                ).fetchall()
                records = [self._row_to_record(row) for row in rows]
                champions = [
                    item
                    for item in records
                    if item.status == ChampionVersionStatus.CHAMPION
                ]
                if len(champions) != 1:
                    raise ChampionRegistryConflictError(
                        "Champion family must contain exactly one active Champion."
                    )
                if champions[0].snapshot_id != head["active_snapshot_id"]:
                    raise ChampionRegistryConflictError(
                        "Champion head differs from the Champion record."
                    )
                decision_ids = {
                    item.decision_id
                    for item in records
                    if item.decision_id is not None
                }
                if decision_ids:
                    stored = {
                        row["decision_id"]
                        for row in connection.execute(
                            "SELECT decision_id FROM champion_decisions"
                        ).fetchall()
                    }
                    if not decision_ids.issubset(stored):
                        raise ChampionRegistryConflictError(
                            "Champion snapshot references a missing decision."
                        )
        self.verify_audit()
        return True

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        record: ChampionSnapshotRecord,
    ) -> None:
        connection.execute(
            "INSERT INTO champion_snapshots "
            "(family_id, snapshot_id, run_id, benchmark_evidence_hash, "
            "benchmark_package_hash, parent_snapshot_id, status, decision_id, "
            "decision_hash, policy_hash, campaign_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.family_id,
                record.snapshot_id,
                record.run_id,
                record.benchmark_evidence_hash,
                record.benchmark_package_hash,
                record.parent_snapshot_id,
                record.status.value,
                record.decision_id,
                record.decision_hash,
                record.policy_hash,
                record.campaign_id,
                record.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _head_row(connection: sqlite3.Connection, family_id: str):
        return connection.execute(
            "SELECT * FROM champion_heads WHERE family_id = ?",
            (family_id,),
        ).fetchone()

    def _require_head(self, connection: sqlite3.Connection, family_id: str):
        row = self._head_row(connection, family_id)
        if row is None:
            raise KeyError(f"Unknown Champion family: {family_id}")
        return row

    @staticmethod
    def _snapshot_row(
        connection: sqlite3.Connection,
        family_id: str,
        snapshot_id: str,
    ):
        return connection.execute(
            "SELECT * FROM champion_snapshots "
            "WHERE family_id = ? AND snapshot_id = ?",
            (family_id, snapshot_id),
        ).fetchone()

    def _require_record(
        self,
        connection: sqlite3.Connection,
        family_id: str,
        snapshot_id: str,
    ) -> ChampionSnapshotRecord:
        row = self._snapshot_row(connection, family_id, snapshot_id)
        if row is None:
            raise KeyError(f"Unknown Champion snapshot: {family_id}/{snapshot_id}")
        return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ChampionSnapshotRecord:
        return ChampionSnapshotRecord(
            family_id=row["family_id"],
            snapshot_id=row["snapshot_id"],
            run_id=row["run_id"],
            benchmark_evidence_hash=row["benchmark_evidence_hash"],
            benchmark_package_hash=row["benchmark_package_hash"],
            parent_snapshot_id=row["parent_snapshot_id"],
            status=ChampionVersionStatus(row["status"]),
            decision_id=row["decision_id"],
            decision_hash=row["decision_hash"],
            policy_hash=row["policy_hash"],
            campaign_id=row["campaign_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _check_revision(head: sqlite3.Row, expected_revision: int) -> None:
        if int(head["revision"]) != expected_revision:
            raise StaleChampionRevision(
                f"Expected Champion revision {expected_revision}, "
                f"found {head['revision']}."
            )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: ChampionEventType,
        family_id: str,
        snapshot_id: str,
        reason: str,
        actor_id: str,
        created_at: datetime,
        payload: dict[str, Any],
        from_snapshot_id: str | None = None,
        to_snapshot_id: str | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM champion_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"champion-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            family_id=family_id,
            snapshot_id=snapshot_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            reason=reason,
            payload=payload,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO champion_audit_events "
            "(sequence, event_id, event_type, family_id, snapshot_id, "
            "from_snapshot_id, to_snapshot_id, reason, payload_json, actor_id, "
            "created_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type.value,
                family_id,
                snapshot_id,
                from_snapshot_id,
                to_snapshot_id,
                reason,
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
        event_type: ChampionEventType,
        family_id: str,
        snapshot_id: str,
        from_snapshot_id: str | None,
        to_snapshot_id: str | None,
        reason: str,
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
                "family_id": family_id,
                "snapshot_id": snapshot_id,
                "from_snapshot_id": from_snapshot_id,
                "to_snapshot_id": to_snapshot_id,
                "reason": reason,
                "payload": payload,
                "actor_id": actor_id,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ChampionAuditEvent:
        return ChampionAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            event_type=ChampionEventType(row["event_type"]),
            family_id=row["family_id"],
            snapshot_id=row["snapshot_id"],
            from_snapshot_id=row["from_snapshot_id"],
            to_snapshot_id=row["to_snapshot_id"],
            reason=row["reason"],
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
    "ChampionAuditIntegrityError",
    "ChampionRegistryConflictError",
    "SQLiteChampionRegistry",
    "StaleChampionRevision",
]
