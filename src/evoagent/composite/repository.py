from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.model_registry.models import canonical_sha256

from .builders import changed_component
from .models import (
    CompositeAuditEvent,
    CompositeEventType,
    CompositeHead,
    CompositeRegistryCheckpoint,
    CompositeSnapshotManifest,
    CompositeSnapshotRecord,
    CompositeSnapshotStatus,
)


_GENESIS_HASH = "0" * 64


class CompositeRegistryConflictError(RuntimeError):
    pass


class StaleCompositeRevision(RuntimeError):
    pass


class CompositeAuditIntegrityError(RuntimeError):
    pass


class SQLiteCompositeSnapshotRegistry:
    """Immutable composite snapshots plus one optimistic active pointer."""

    def __init__(self, path: str | Path):
        raw_path = Path(path).expanduser()
        if raw_path.is_symlink():
            raise ValueError(
                "Composite snapshot Registry path must not be a symlink."
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
                CREATE TABLE IF NOT EXISTS composite_snapshots (
                    lineage_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    parent_snapshot_id TEXT,
                    round_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    committed_by TEXT NOT NULL,
                    committed_at TEXT NOT NULL,
                    PRIMARY KEY(lineage_id, snapshot_id),
                    FOREIGN KEY(lineage_id, parent_snapshot_id)
                        REFERENCES composite_snapshots(lineage_id, snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS composite_heads (
                    lineage_id TEXT PRIMARY KEY,
                    active_snapshot_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(lineage_id, active_snapshot_id)
                        REFERENCES composite_snapshots(lineage_id, snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS composite_audit_events (
                    lineage_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    from_snapshot_id TEXT,
                    to_snapshot_id TEXT,
                    reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    PRIMARY KEY(lineage_id, sequence)
                );
                """
            )

    def register_initial(
        self,
        manifest: CompositeSnapshotManifest,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> CompositeSnapshotRecord:
        effective = self._effective_now(now)
        if manifest.round_index != 0 or manifest.parent_snapshot_id is not None:
            raise ValueError(
                "Composite initial registration requires a round-zero manifest."
            )
        if manifest.created_by != actor_id:
            raise ValueError(
                "Composite initial registrar differs from the manifest creator."
            )
        if manifest.created_at > effective:
            raise ValueError(
                "Composite initial manifest postdates its Registry write."
            )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._head_row(connection, manifest.lineage_id)
                if head is not None:
                    existing = self._require_record(
                        connection,
                        manifest.lineage_id,
                        manifest.snapshot_id,
                    )
                    if (
                        existing.manifest == manifest
                        and existing.status == CompositeSnapshotStatus.ACTIVE
                        and existing.committed_by == actor_id
                        and head["active_snapshot_id"] == manifest.snapshot_id
                        and int(head["revision"]) == 0
                    ):
                        connection.commit()
                        return existing
                    raise CompositeRegistryConflictError(
                        "Composite lineage is already registered."
                    )

                record = CompositeSnapshotRecord(
                    lineage_id=manifest.lineage_id,
                    snapshot_id=manifest.snapshot_id,
                    manifest=manifest,
                    status=CompositeSnapshotStatus.ACTIVE,
                    committed_by=actor_id,
                    committed_at=effective,
                )
                self._insert_record(connection, record)
                connection.execute(
                    "INSERT INTO composite_heads "
                    "(lineage_id, active_snapshot_id, revision, updated_at) "
                    "VALUES (?, ?, 0, ?)",
                    (
                        manifest.lineage_id,
                        manifest.snapshot_id,
                        effective.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    lineage_id=manifest.lineage_id,
                    event_type=CompositeEventType.REGISTERED,
                    snapshot_id=manifest.snapshot_id,
                    actor_id=actor_id,
                    reason="Initial composite snapshot registered.",
                    metadata={
                        "manifest_hash": manifest.manifest_hash,
                        "skill_content_hash": manifest.skill.content_hash,
                        "policy_checkpoint_hash": (
                            manifest.local_policy.checkpoint_hash
                        ),
                    },
                    created_at=effective,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def commit(
        self,
        manifest: CompositeSnapshotManifest,
        *,
        expected_active_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> CompositeSnapshotRecord:
        effective = self._effective_now(now)
        if manifest.round_index == 0 or manifest.parent_snapshot_id is None:
            raise ValueError(
                "Composite commit requires an evolved child manifest."
            )
        if actor_id == manifest.created_by:
            raise ValueError(
                "Composite snapshot creator cannot commit its own pointer change."
            )
        if manifest.created_at > effective:
            raise ValueError(
                "Composite child manifest postdates its Registry commit."
            )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = self._snapshot_row(
                    connection,
                    manifest.lineage_id,
                    manifest.snapshot_id,
                )
                head = self._require_head(connection, manifest.lineage_id)
                if existing_row is not None:
                    existing = self._row_to_record(existing_row)
                    if (
                        existing.manifest == manifest
                        and existing.status == CompositeSnapshotStatus.ACTIVE
                        and existing.committed_by == actor_id
                        and head["active_snapshot_id"] == manifest.snapshot_id
                    ):
                        if int(head["revision"]) != expected_active_revision + 1:
                            raise StaleCompositeRevision(
                                "Applied composite commit differs from the retried revision."
                            )
                        connection.commit()
                        return existing
                    raise CompositeRegistryConflictError(
                        "Composite snapshot ID contains another committed manifest."
                    )

                self._check_revision(head, expected_active_revision)
                if head["active_snapshot_id"] != manifest.parent_snapshot_id:
                    raise StaleCompositeRevision(
                        "Composite child parent is no longer the active snapshot."
                    )
                parent = self._require_record(
                    connection,
                    manifest.lineage_id,
                    manifest.parent_snapshot_id,
                )
                if parent.status != CompositeSnapshotStatus.ACTIVE:
                    raise ValueError(
                        "Composite child parent must be ACTIVE."
                    )
                if manifest.created_at < parent.committed_at:
                    raise ValueError(
                        "Composite child manifest predates the parent commit."
                    )
                component = changed_component(parent.manifest, manifest)

                connection.execute(
                    "UPDATE composite_snapshots SET status = ? "
                    "WHERE lineage_id = ? AND snapshot_id = ?",
                    (
                        CompositeSnapshotStatus.SUPERSEDED.value,
                        manifest.lineage_id,
                        parent.snapshot_id,
                    ),
                )
                record = CompositeSnapshotRecord(
                    lineage_id=manifest.lineage_id,
                    snapshot_id=manifest.snapshot_id,
                    manifest=manifest,
                    status=CompositeSnapshotStatus.ACTIVE,
                    committed_by=actor_id,
                    committed_at=effective,
                )
                self._insert_record(connection, record)
                changed = connection.execute(
                    "UPDATE composite_heads SET active_snapshot_id = ?, "
                    "revision = revision + 1, updated_at = ? "
                    "WHERE lineage_id = ? AND revision = ?",
                    (
                        manifest.snapshot_id,
                        effective.isoformat(),
                        manifest.lineage_id,
                        expected_active_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleCompositeRevision(
                        "Composite active revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    lineage_id=manifest.lineage_id,
                    event_type=CompositeEventType.COMMITTED,
                    snapshot_id=manifest.snapshot_id,
                    from_snapshot_id=parent.snapshot_id,
                    to_snapshot_id=manifest.snapshot_id,
                    actor_id=actor_id,
                    reason="Explicit composite snapshot pointer commit completed.",
                    metadata={
                        "manifest_hash": manifest.manifest_hash,
                        "parent_manifest_hash": parent.manifest.manifest_hash,
                        "changed_component": component,
                        "active_revision_before": expected_active_revision,
                    },
                    created_at=effective,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def active(self, lineage_id: str) -> CompositeSnapshotRecord:
        with self._connection() as connection:
            head = self._require_head(connection, lineage_id)
            return self._require_record(
                connection,
                lineage_id,
                head["active_snapshot_id"],
            )

    def head(self, lineage_id: str) -> CompositeHead:
        with self._connection() as connection:
            row = self._require_head(connection, lineage_id)
            return CompositeHead(
                lineage_id=lineage_id,
                active_snapshot_id=row["active_snapshot_id"],
                revision=int(row["revision"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def get(
        self,
        lineage_id: str,
        snapshot_id: str,
    ) -> CompositeSnapshotRecord:
        with self._connection() as connection:
            return self._require_record(connection, lineage_id, snapshot_id)

    def list_snapshots(
        self,
        lineage_id: str,
    ) -> tuple[CompositeSnapshotRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM composite_snapshots WHERE lineage_id = ? "
                "ORDER BY round_index, snapshot_id",
                (lineage_id,),
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    def events(
        self,
        lineage_id: str,
    ) -> tuple[CompositeAuditEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM composite_audit_events "
                "WHERE lineage_id = ? ORDER BY sequence",
                (lineage_id,),
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)

    def checkpoint(self, lineage_id: str) -> CompositeRegistryCheckpoint:
        events = self.events(lineage_id)
        return CompositeRegistryCheckpoint(
            lineage_id=lineage_id,
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_state(self, lineage_id: str) -> bool:
        records = self.list_snapshots(lineage_id)
        if not records:
            raise CompositeAuditIntegrityError(
                f"Composite lineage has no snapshots: {lineage_id}"
            )
        active = tuple(
            item
            for item in records
            if item.status == CompositeSnapshotStatus.ACTIVE
        )
        if len(active) != 1 or self.active(lineage_id) != active[0]:
            raise CompositeAuditIntegrityError(
                "Composite lineage must contain one pointer-matching ACTIVE snapshot."
            )
        head = self.head(lineage_id)
        if (
            head.revision != active[0].manifest.round_index
            or len(records) != head.revision + 1
        ):
            raise CompositeAuditIntegrityError(
                "Composite head revision differs from contiguous snapshot lineage."
            )

        by_id = {item.snapshot_id: item for item in records}
        rounds = tuple(item.manifest.round_index for item in records)
        if rounds != tuple(range(len(records))):
            raise CompositeAuditIntegrityError(
                "Composite snapshot rounds are missing, duplicated, or reordered."
            )
        for index, record in enumerate(records):
            if index == 0:
                if (
                    record.manifest.parent_snapshot_id is not None
                    or record.status
                    != (
                        CompositeSnapshotStatus.ACTIVE
                        if len(records) == 1
                        else CompositeSnapshotStatus.SUPERSEDED
                    )
                ):
                    raise CompositeAuditIntegrityError(
                        "Composite initial snapshot state is invalid."
                    )
                continue
            parent = by_id.get(record.manifest.parent_snapshot_id)
            if parent is None:
                raise CompositeAuditIntegrityError(
                    "Composite snapshot references an unknown parent."
                )
            try:
                changed_component(parent.manifest, record.manifest)
            except ValueError as exc:
                raise CompositeAuditIntegrityError(str(exc)) from exc
            expected_status = (
                CompositeSnapshotStatus.ACTIVE
                if index == len(records) - 1
                else CompositeSnapshotStatus.SUPERSEDED
            )
            if record.status != expected_status:
                raise CompositeAuditIntegrityError(
                    "Composite snapshot status differs from pointer lineage."
                )
            if (
                record.manifest.created_at < parent.committed_at
                or record.committed_at < record.manifest.created_at
            ):
                raise CompositeAuditIntegrityError(
                    "Composite snapshot chronology is invalid."
                )

        self.verify_audit(lineage_id)
        self._verify_semantic_events(lineage_id, records)
        return True

    def verify_audit(
        self,
        lineage_id: str,
        checkpoint: CompositeRegistryCheckpoint | None = None,
    ) -> bool:
        events = self.events(lineage_id)
        previous = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous
            ):
                raise CompositeAuditIntegrityError(
                    "Composite audit sequence or hash chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                lineage_id=event.lineage_id,
                snapshot_id=event.snapshot_id,
                from_snapshot_id=event.from_snapshot_id,
                to_snapshot_id=event.to_snapshot_id,
                reason=event.reason,
                metadata=event.metadata,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise CompositeAuditIntegrityError(
                    "Composite audit event content was modified."
                )
            previous = event.event_hash
        current = CompositeRegistryCheckpoint(
            lineage_id=lineage_id,
            event_count=len(events),
            head_hash=previous,
        )
        if checkpoint is not None and current != checkpoint:
            raise CompositeAuditIntegrityError(
                "Composite audit differs from its external checkpoint."
            )
        return True

    def _verify_semantic_events(
        self,
        lineage_id: str,
        records: tuple[CompositeSnapshotRecord, ...],
    ) -> None:
        events = self.events(lineage_id)
        if len(events) != len(records):
            raise CompositeAuditIntegrityError(
                "Composite audit omits or duplicates a pointer lifecycle event."
            )
        for index, (record, event) in enumerate(zip(records, events, strict=True)):
            if index == 0:
                expected_type = CompositeEventType.REGISTERED
                expected_reason = "Initial composite snapshot registered."
                expected_from = None
                expected_to = None
                expected_metadata = {
                    "manifest_hash": record.manifest.manifest_hash,
                    "skill_content_hash": record.manifest.skill.content_hash,
                    "policy_checkpoint_hash": (
                        record.manifest.local_policy.checkpoint_hash
                    ),
                }
            else:
                parent = records[index - 1]
                expected_type = CompositeEventType.COMMITTED
                expected_reason = (
                    "Explicit composite snapshot pointer commit completed."
                )
                expected_from = parent.snapshot_id
                expected_to = record.snapshot_id
                expected_metadata = {
                    "manifest_hash": record.manifest.manifest_hash,
                    "parent_manifest_hash": parent.manifest.manifest_hash,
                    "changed_component": changed_component(
                        parent.manifest,
                        record.manifest,
                    ),
                    "active_revision_before": index - 1,
                }
            if (
                event.event_type != expected_type
                or event.snapshot_id != record.snapshot_id
                or event.from_snapshot_id != expected_from
                or event.to_snapshot_id != expected_to
                or event.reason != expected_reason
                or event.metadata != expected_metadata
                or event.actor_id != record.committed_by
                or event.created_at != record.committed_at
            ):
                raise CompositeAuditIntegrityError(
                    "Composite audit semantics differ from committed snapshot lineage."
                )

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        record: CompositeSnapshotRecord,
    ) -> None:
        connection.execute(
            "INSERT INTO composite_snapshots "
            "(lineage_id, snapshot_id, manifest_json, parent_snapshot_id, "
            "round_index, status, committed_by, committed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.lineage_id,
                record.snapshot_id,
                self._json(record.manifest.model_dump(mode="json")),
                record.manifest.parent_snapshot_id,
                record.manifest.round_index,
                record.status.value,
                record.committed_by,
                record.committed_at.isoformat(),
            ),
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        lineage_id: str,
        event_type: CompositeEventType,
        snapshot_id: str,
        reason: str,
        metadata: dict[str, Any],
        actor_id: str,
        created_at: datetime,
        from_snapshot_id: str | None = None,
        to_snapshot_id: str | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM composite_audit_events "
            "WHERE lineage_id = ? ORDER BY sequence DESC LIMIT 1",
            (lineage_id,),
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"composite-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            lineage_id=lineage_id,
            snapshot_id=snapshot_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
            reason=reason,
            metadata=metadata,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO composite_audit_events "
            "(lineage_id, sequence, event_id, event_type, snapshot_id, "
            "from_snapshot_id, to_snapshot_id, reason, metadata_json, "
            "actor_id, created_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lineage_id,
                sequence,
                event_id,
                event_type.value,
                snapshot_id,
                from_snapshot_id,
                to_snapshot_id,
                reason,
                self._json(metadata),
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
        event_type: CompositeEventType,
        lineage_id: str,
        snapshot_id: str,
        from_snapshot_id: str | None,
        to_snapshot_id: str | None,
        reason: str,
        metadata: dict[str, Any],
        actor_id: str,
        created_at: datetime,
        previous_hash: str,
    ) -> str:
        return canonical_sha256(
            {
                "sequence": sequence,
                "event_id": event_id,
                "event_type": event_type.value,
                "lineage_id": lineage_id,
                "snapshot_id": snapshot_id,
                "from_snapshot_id": from_snapshot_id,
                "to_snapshot_id": to_snapshot_id,
                "reason": reason,
                "metadata": metadata,
                "actor_id": actor_id,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CompositeSnapshotRecord:
        return CompositeSnapshotRecord(
            lineage_id=row["lineage_id"],
            snapshot_id=row["snapshot_id"],
            manifest=CompositeSnapshotManifest.model_validate_json(
                row["manifest_json"]
            ),
            status=CompositeSnapshotStatus(row["status"]),
            committed_by=row["committed_by"],
            committed_at=datetime.fromisoformat(row["committed_at"]),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> CompositeAuditEvent:
        return CompositeAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            event_type=CompositeEventType(row["event_type"]),
            lineage_id=row["lineage_id"],
            snapshot_id=row["snapshot_id"],
            from_snapshot_id=row["from_snapshot_id"],
            to_snapshot_id=row["to_snapshot_id"],
            reason=row["reason"],
            metadata=json.loads(row["metadata_json"]),
            actor_id=row["actor_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    @staticmethod
    def _head_row(connection: sqlite3.Connection, lineage_id: str):
        return connection.execute(
            "SELECT * FROM composite_heads WHERE lineage_id = ?",
            (lineage_id,),
        ).fetchone()

    def _require_head(
        self,
        connection: sqlite3.Connection,
        lineage_id: str,
    ):
        row = self._head_row(connection, lineage_id)
        if row is None:
            raise KeyError(f"Unknown composite lineage: {lineage_id}")
        return row

    @staticmethod
    def _snapshot_row(
        connection: sqlite3.Connection,
        lineage_id: str,
        snapshot_id: str,
    ):
        return connection.execute(
            "SELECT * FROM composite_snapshots "
            "WHERE lineage_id = ? AND snapshot_id = ?",
            (lineage_id, snapshot_id),
        ).fetchone()

    def _require_record(
        self,
        connection: sqlite3.Connection,
        lineage_id: str,
        snapshot_id: str,
    ) -> CompositeSnapshotRecord:
        row = self._snapshot_row(connection, lineage_id, snapshot_id)
        if row is None:
            raise KeyError(
                f"Unknown composite snapshot: {lineage_id}/{snapshot_id}"
            )
        return self._row_to_record(row)

    @staticmethod
    def _check_revision(head: sqlite3.Row, expected_revision: int) -> None:
        actual = int(head["revision"])
        if actual != expected_revision:
            raise StaleCompositeRevision(
                f"Expected composite revision {expected_revision}, found {actual}."
            )

    @staticmethod
    def _effective_now(value: datetime | None) -> datetime:
        effective = value or datetime.now(timezone.utc)
        if effective.tzinfo is None or effective.utcoffset() is None:
            raise ValueError(
                "Composite Registry write time must include a timezone."
            )
        if effective > datetime.now(timezone.utc):
            raise ValueError(
                "Composite Registry write time must not be in the future."
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
    "CompositeAuditIntegrityError",
    "CompositeRegistryConflictError",
    "SQLiteCompositeSnapshotRegistry",
    "StaleCompositeRevision",
]
