from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.model_registry.models import canonical_sha256

from .builders import validate_one_component_transition
from .evaluation import ContinualPromotionDecision
from .models import SnapshotStatus, UnifiedAgentSnapshot


_HASH = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class ContinualSnapshotRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot: UnifiedAgentSnapshot
    status: SnapshotStatus
    registration_actor_id: str = Field(pattern=_SAFE_ID)
    activation_decision_hash: str | None = Field(default=None, pattern=_HASH)
    activation_actor_id: str | None = Field(default=None, pattern=_SAFE_ID)


class ContinualSnapshotHead(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID)
    active_snapshot_id: str = Field(pattern=_SAFE_ID)
    revision: int = Field(ge=0)


class ContinualSnapshotEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=0)
    event_type: str
    lineage_id: str = Field(pattern=_SAFE_ID)
    snapshot_id: str = Field(pattern=_SAFE_ID)
    actor_id: str = Field(pattern=_SAFE_ID)
    payload_hash: str = Field(pattern=_HASH)
    previous_event_hash: str | None = Field(default=None, pattern=_HASH)
    event_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"event_hash"})
        if self.event_hash != canonical_sha256(payload):
            raise ValueError("Continual Registry event hash mismatch.")
        return self


class StaleContinualRevision(RuntimeError):
    pass


class ContinualRegistryConflict(RuntimeError):
    pass


class SQLiteContinualSnapshotRegistry:
    """Persistent candidate/activation boundary for complete Agent snapshots."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def register_initial(
        self,
        snapshot: UnifiedAgentSnapshot,
        *,
        actor_id: str,
    ) -> ContinualSnapshotRecord:
        if snapshot.round_index != 0:
            raise ValueError("Initial Registry snapshot must be round zero.")
        existing = self._record_or_none(snapshot.snapshot_id)
        if existing:
            if existing.snapshot == snapshot and existing.status == SnapshotStatus.ACTIVE:
                return existing
            raise ContinualRegistryConflict("Conflicting initial snapshot registration.")
        with self._connect() as connection:
            if connection.execute(
                "SELECT COUNT(*) FROM heads WHERE lineage_id=?",
                (snapshot.lineage_id,),
            ).fetchone()[0]:
                raise ContinualRegistryConflict("Registry already has an active lineage.")
            record = ContinualSnapshotRecord(
                snapshot=snapshot,
                status=SnapshotStatus.ACTIVE,
                registration_actor_id=actor_id,
            )
            self._insert_record(connection, record)
            connection.execute(
                "INSERT INTO heads(lineage_id, active_snapshot_id, revision) VALUES(?, ?, 0)",
                (snapshot.lineage_id, snapshot.snapshot_id),
            )
            self._append_event(
                connection,
                event_type="initial_activated",
                snapshot=snapshot,
                actor_id=actor_id,
                payload_hash=snapshot.snapshot_hash,
            )
        return record

    def register_candidate(
        self,
        snapshot: UnifiedAgentSnapshot,
        *,
        actor_id: str,
    ) -> ContinualSnapshotRecord:
        head = self.head(snapshot.lineage_id)
        if snapshot.parent_snapshot_id != head.active_snapshot_id:
            raise ContinualRegistryConflict("Candidate parent is not the active snapshot.")
        active = self.record(head.active_snapshot_id).snapshot
        try:
            validate_one_component_transition(active, snapshot)
        except ValueError as exc:
            raise ContinualRegistryConflict(
                "Candidate does not make one exact active-parent component change."
            ) from exc
        existing = self._record_or_none(snapshot.snapshot_id)
        if existing:
            if existing.snapshot == snapshot and existing.status in {
                SnapshotStatus.CANDIDATE,
                SnapshotStatus.ACTIVE,
            }:
                return existing
            raise ContinualRegistryConflict("Conflicting candidate registration.")
        record = ContinualSnapshotRecord(
            snapshot=snapshot,
            status=SnapshotStatus.CANDIDATE,
            registration_actor_id=actor_id,
        )
        with self._connect() as connection:
            self._insert_record(connection, record)
            self._append_event(
                connection,
                event_type="candidate_registered",
                snapshot=snapshot,
                actor_id=actor_id,
                payload_hash=snapshot.snapshot_hash,
            )
        if self.head(snapshot.lineage_id) != head:
            raise ContinualRegistryConflict("Candidate registration changed the active pointer.")
        return record

    def activate(
        self,
        snapshot_id: str,
        decision: ContinualPromotionDecision,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> ContinualSnapshotRecord:
        candidate = self.record(snapshot_id)
        snapshot = candidate.snapshot
        head = self.head(snapshot.lineage_id)
        if (
            candidate.status == SnapshotStatus.ACTIVE
            and head.active_snapshot_id == snapshot_id
            and head.revision == expected_revision + 1
            and candidate.activation_decision_hash == decision.decision_hash
            and candidate.activation_actor_id == actor_id
        ):
            return candidate
        if head.revision != expected_revision:
            raise StaleContinualRevision("Continual Registry revision is stale.")
        if candidate.status != SnapshotStatus.CANDIDATE:
            raise ContinualRegistryConflict("Only a candidate snapshot can be activated.")
        if not decision.eligible:
            raise PermissionError("Ineligible continual candidate cannot be activated.")
        if decision.candidate_snapshot_hash != snapshot.snapshot_hash:
            raise ContinualRegistryConflict("Promotion decision names another candidate.")
        if decision.changed_component != snapshot.changed_component:
            raise ContinualRegistryConflict("Promotion decision names another component.")
        if snapshot.parent_snapshot_id != head.active_snapshot_id:
            raise ContinualRegistryConflict("Active parent moved before candidate activation.")
        if actor_id in {snapshot.creator_id, candidate.registration_actor_id}:
            raise PermissionError("Candidate creator/registrar cannot activate it.")
        active = self.record(head.active_snapshot_id)
        superseded = active.model_copy(update={"status": SnapshotStatus.SUPERSEDED})
        activated = candidate.model_copy(
            update={
                "status": SnapshotStatus.ACTIVE,
                "activation_decision_hash": decision.decision_hash,
                "activation_actor_id": actor_id,
            }
        )
        with self._connect() as connection:
            self._replace_record(connection, superseded)
            self._replace_record(connection, activated)
            updated = connection.execute(
                "UPDATE heads SET active_snapshot_id=?, revision=revision+1 "
                "WHERE lineage_id=? AND revision=?",
                (snapshot_id, snapshot.lineage_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise StaleContinualRevision("Continual Registry revision changed.")
            self._append_event(
                connection,
                event_type="candidate_activated",
                snapshot=snapshot,
                actor_id=actor_id,
                payload_hash=decision.decision_hash,
            )
        return activated

    def reject(
        self,
        snapshot_id: str,
        decision: ContinualPromotionDecision,
        *,
        actor_id: str,
    ) -> ContinualSnapshotRecord:
        record = self.record(snapshot_id)
        if record.status == SnapshotStatus.REJECTED:
            if record.activation_decision_hash == decision.decision_hash:
                return record
            raise ContinualRegistryConflict("Conflicting candidate rejection retry.")
        if record.status != SnapshotStatus.CANDIDATE or decision.eligible:
            raise ValueError("Only an ineligible candidate can be rejected.")
        if decision.candidate_snapshot_hash != record.snapshot.snapshot_hash:
            raise ContinualRegistryConflict("Rejection decision names another candidate.")
        if decision.changed_component != record.snapshot.changed_component:
            raise ContinualRegistryConflict("Rejection decision names another component.")
        rejected = record.model_copy(
            update={
                "status": SnapshotStatus.REJECTED,
                "activation_decision_hash": decision.decision_hash,
                "activation_actor_id": actor_id,
            }
        )
        with self._connect() as connection:
            self._replace_record(connection, rejected)
            self._append_event(
                connection,
                event_type="candidate_rejected",
                snapshot=record.snapshot,
                actor_id=actor_id,
                payload_hash=decision.decision_hash,
            )
        return rejected

    def head(self, lineage_id: str) -> ContinualSnapshotHead:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT active_snapshot_id, revision FROM heads WHERE lineage_id=?",
                (lineage_id,),
            ).fetchone()
        if row is None:
            raise KeyError(lineage_id)
        return ContinualSnapshotHead(
            lineage_id=lineage_id,
            active_snapshot_id=row[0],
            revision=row[1],
        )

    def record(self, snapshot_id: str) -> ContinualSnapshotRecord:
        record = self._record_or_none(snapshot_id)
        if record is None:
            raise KeyError(snapshot_id)
        return record

    def events(self) -> tuple[ContinualSnapshotEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM events ORDER BY sequence"
            ).fetchall()
        return tuple(ContinualSnapshotEvent.model_validate_json(row[0]) for row in rows)

    def verify_state(self, lineage_id: str) -> None:
        head = self.head(lineage_id)
        active = self.record(head.active_snapshot_id)
        if active.status != SnapshotStatus.ACTIVE:
            raise ContinualRegistryConflict("Registry head is not ACTIVE.")
        events = self.events()
        previous = None
        for sequence, event in enumerate(events):
            if event.sequence != sequence or event.previous_event_hash != previous:
                raise ContinualRegistryConflict("Continual Registry audit chain is broken.")
            previous = event.event_hash
        activated = sum(
            event.event_type == "candidate_activated" and event.lineage_id == lineage_id
            for event in events
        )
        if head.revision != activated:
            raise ContinualRegistryConflict("Registry revision differs from activation history.")

    def _record_or_none(self, snapshot_id: str) -> ContinualSnapshotRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        return ContinualSnapshotRecord.model_validate_json(row[0]) if row else None

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots(
                    snapshot_id TEXT PRIMARY KEY,
                    lineage_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS heads(
                    lineage_id TEXT PRIMARY KEY,
                    active_snapshot_id TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _insert_record(connection, record: ContinualSnapshotRecord) -> None:
        connection.execute(
            "INSERT INTO snapshots(snapshot_id, lineage_id, payload) VALUES(?, ?, ?)",
            (
                record.snapshot.snapshot_id,
                record.snapshot.lineage_id,
                record.model_dump_json(),
            ),
        )

    @staticmethod
    def _replace_record(connection, record: ContinualSnapshotRecord) -> None:
        connection.execute(
            "UPDATE snapshots SET payload=? WHERE snapshot_id=?",
            (record.model_dump_json(), record.snapshot.snapshot_id),
        )

    @staticmethod
    def _append_event(
        connection,
        *,
        event_type: str,
        snapshot: UnifiedAgentSnapshot,
        actor_id: str,
        payload_hash: str,
    ) -> None:
        row = connection.execute(
            "SELECT sequence, payload FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = 0 if row is None else row[0] + 1
        previous = None
        if row is not None:
            previous = ContinualSnapshotEvent.model_validate_json(row[1]).event_hash
        payload = {
            "sequence": sequence,
            "event_type": event_type,
            "lineage_id": snapshot.lineage_id,
            "snapshot_id": snapshot.snapshot_id,
            "actor_id": actor_id,
            "payload_hash": payload_hash,
            "previous_event_hash": previous,
        }
        event = ContinualSnapshotEvent(**payload, event_hash=canonical_sha256(payload))
        connection.execute(
            "INSERT INTO events(sequence, payload) VALUES(?, ?)",
            (sequence, event.model_dump_json()),
        )


__all__ = [
    "ContinualRegistryConflict",
    "ContinualSnapshotEvent",
    "ContinualSnapshotHead",
    "ContinualSnapshotRecord",
    "SQLiteContinualSnapshotRegistry",
    "StaleContinualRevision",
]
