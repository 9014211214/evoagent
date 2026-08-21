from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.campaigns.models import (
    ApprovalDecision,
    CampaignApproval,
    CampaignAuditEvent,
    CampaignCheckpoint,
    CampaignRecord,
    CampaignReservation,
    CampaignRisk,
    CampaignState,
    CampaignType,
    ModelEvidenceSnapshot,
)


_GENESIS_HASH = "0" * 64
_OPEN_STATES = {
    CampaignState.OPEN,
    CampaignState.EVIDENCE_ACCUMULATING,
    CampaignState.CANDIDATE_READY,
    CampaignState.EVALUATION_PENDING,
    CampaignState.APPROVAL_PENDING,
    CampaignState.AUTHORIZED,
}
_LEGAL_TRANSITIONS = {
    CampaignState.OPEN: {
        CampaignState.EVIDENCE_ACCUMULATING,
        CampaignState.CANDIDATE_READY,
        CampaignState.CANCELLED,
    },
    CampaignState.EVIDENCE_ACCUMULATING: {
        CampaignState.CANDIDATE_READY,
        CampaignState.CANCELLED,
    },
    CampaignState.CANDIDATE_READY: {
        CampaignState.EVALUATION_PENDING,
        CampaignState.REJECTED,
        CampaignState.CANCELLED,
    },
    CampaignState.EVALUATION_PENDING: {
        CampaignState.APPROVAL_PENDING,
        CampaignState.REJECTED,
        CampaignState.CANCELLED,
    },
    CampaignState.APPROVAL_PENDING: {
        CampaignState.AUTHORIZED,
        CampaignState.REJECTED,
        CampaignState.CANCELLED,
    },
    CampaignState.AUTHORIZED: {
        CampaignState.COMPLETED,
        CampaignState.CANCELLED,
    },
    CampaignState.REJECTED: set(),
    CampaignState.CANCELLED: set(),
    CampaignState.COMPLETED: set(),
}


class CampaignConflictError(RuntimeError):
    pass


class CampaignCooldownError(RuntimeError):
    pass


class InvalidCampaignTransition(ValueError):
    pass


class StaleCampaignRevision(RuntimeError):
    pass


class CampaignApprovalError(ValueError):
    pass


class CampaignAuditIntegrityError(RuntimeError):
    pass


def fingerprint_payload(payload: Any) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SQLiteCampaignRepository:
    """Transactional Campaign, evidence, approval and audit storage."""

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
        active_states = ",".join(f"'{item.value}'" for item in _OPEN_STATES)
        with self._connection() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    campaign_type TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    generated_by TEXT NOT NULL,
                    required_approvals INTEGER NOT NULL,
                    candidate_ref TEXT,
                    artifact_json TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    cooldown_until TEXT,
                    revision INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_campaign_open_target
                    ON campaigns(target_key)
                    WHERE state IN ({active_states});
                CREATE INDEX IF NOT EXISTS ix_campaign_fingerprint
                    ON campaigns(fingerprint);

                CREATE TABLE IF NOT EXISTS campaign_approvals (
                    approval_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                    actor_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(campaign_id, actor_id)
                );

                CREATE TABLE IF NOT EXISTS model_evidence (
                    base_model_id TEXT NOT NULL,
                    problem_cluster TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(base_model_id, problem_cluster, trace_id)
                );

                CREATE TABLE IF NOT EXISTS campaign_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    campaign_id TEXT,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def reserve_campaign(
        self,
        *,
        campaign_type: CampaignType,
        target_key: str,
        fingerprint: str,
        risk: CampaignRisk,
        generated_by: str,
        required_approvals: int,
        initial_state: CampaignState = CampaignState.OPEN,
        metadata: dict[str, Any] | None = None,
        campaign_id: str | None = None,
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> CampaignReservation:
        if required_approvals < 1:
            raise ValueError("Campaign requires at least one approval.")
        if initial_state not in {CampaignState.OPEN, CampaignState.EVIDENCE_ACCUMULATING}:
            raise ValueError("A new Campaign must start open or accumulating evidence.")
        now = now or datetime.now(timezone.utc)
        campaign_id = campaign_id or f"campaign:{uuid.uuid4()}"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM campaigns WHERE target_key = ? AND state IN "
                    "('open','evidence_accumulating','candidate_ready','evaluation_pending',"
                    "'approval_pending','authorized') LIMIT 1",
                    (target_key,),
                ).fetchone()
                if existing is not None:
                    record = self._row_to_campaign(existing)
                    if record.fingerprint != fingerprint:
                        raise CampaignConflictError(
                            f"Open Campaign {record.campaign_id} already owns target {target_key}."
                        )
                    self._append_event(
                        connection,
                        campaign_id=record.campaign_id,
                        event_type="campaign_reused",
                        actor_id=actor_id,
                        payload={
                            "target_key": target_key,
                            "fingerprint": fingerprint,
                            "metadata": metadata or {},
                        },
                        created_at=now,
                    )
                    connection.commit()
                    return CampaignReservation(campaign=record, reused=True)

                closed = connection.execute(
                    "SELECT cooldown_until FROM campaigns WHERE target_key = ? "
                    "AND cooldown_until IS NOT NULL ORDER BY updated_at DESC LIMIT 1",
                    (target_key,),
                ).fetchone()
                if closed and closed["cooldown_until"]:
                    cooldown_until = datetime.fromisoformat(closed["cooldown_until"])
                    if cooldown_until > now:
                        raise CampaignCooldownError(
                            f"Target {target_key} is cooling down until {cooldown_until.isoformat()}."
                        )

                connection.execute(
                    "INSERT INTO campaigns (campaign_id, campaign_type, target_key, fingerprint, "
                    "state, risk, generated_by, required_approvals, candidate_ref, artifact_json, "
                    "metadata_json, created_at, updated_at, cooldown_until, revision) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, 0)",
                    (
                        campaign_id,
                        campaign_type.value,
                        target_key,
                        fingerprint,
                        initial_state.value,
                        risk.value,
                        generated_by,
                        required_approvals,
                        self._json(metadata or {}),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    campaign_id=campaign_id,
                    event_type="campaign_created",
                    actor_id=actor_id,
                    payload={
                        "campaign_type": campaign_type.value,
                        "target_key": target_key,
                        "fingerprint": fingerprint,
                        "state": initial_state.value,
                    },
                    created_at=now,
                )
                connection.commit()
                return CampaignReservation(
                    campaign=self._get_in_connection(connection, campaign_id),
                    reused=False,
                )
            except Exception:
                connection.rollback()
                raise

    def attach_candidate(
        self,
        campaign_id: str,
        *,
        candidate_ref: str,
        artifact_payload: dict[str, Any],
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> CampaignRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._get_in_connection(connection, campaign_id)
                if record.revision != expected_revision:
                    raise StaleCampaignRevision(
                        f"Expected revision {expected_revision}, found {record.revision}."
                    )
                if CampaignState.CANDIDATE_READY not in _LEGAL_TRANSITIONS[record.state]:
                    raise InvalidCampaignTransition(
                        f"Cannot attach a candidate while Campaign is {record.state.value}."
                    )
                connection.execute(
                    "UPDATE campaigns SET candidate_ref = ?, artifact_json = ?, state = ?, "
                    "updated_at = ?, revision = revision + 1 WHERE campaign_id = ? AND revision = ?",
                    (
                        candidate_ref,
                        self._json(artifact_payload),
                        CampaignState.CANDIDATE_READY.value,
                        now.isoformat(),
                        campaign_id,
                        expected_revision,
                    ),
                )
                self._append_event(
                    connection,
                    campaign_id=campaign_id,
                    event_type="candidate_attached",
                    actor_id=actor_id,
                    payload={"candidate_ref": candidate_ref},
                    created_at=now,
                )
                connection.commit()
                return self._get_in_connection(connection, campaign_id)
            except Exception:
                connection.rollback()
                raise

    def transition(
        self,
        campaign_id: str,
        *,
        to_state: CampaignState,
        expected_revision: int,
        actor_id: str,
        reason: str,
        cooldown_seconds: int = 0,
        now: datetime | None = None,
    ) -> CampaignRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._get_in_connection(connection, campaign_id)
                if record.revision != expected_revision:
                    raise StaleCampaignRevision(
                        f"Expected revision {expected_revision}, found {record.revision}."
                    )
                if to_state not in _LEGAL_TRANSITIONS[record.state]:
                    raise InvalidCampaignTransition(
                        f"Illegal Campaign transition: {record.state.value} -> {to_state.value}."
                    )
                cooldown_until = None
                if to_state in {CampaignState.REJECTED, CampaignState.CANCELLED} and cooldown_seconds:
                    cooldown_until = now + timedelta(seconds=cooldown_seconds)
                connection.execute(
                    "UPDATE campaigns SET state = ?, updated_at = ?, cooldown_until = ?, "
                    "revision = revision + 1 WHERE campaign_id = ? AND revision = ?",
                    (
                        to_state.value,
                        now.isoformat(),
                        cooldown_until.isoformat() if cooldown_until else None,
                        campaign_id,
                        expected_revision,
                    ),
                )
                self._append_event(
                    connection,
                    campaign_id=campaign_id,
                    event_type="campaign_transitioned",
                    actor_id=actor_id,
                    payload={
                        "from_state": record.state.value,
                        "to_state": to_state.value,
                        "reason": reason,
                        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._get_in_connection(connection, campaign_id)
            except Exception:
                connection.rollback()
                raise

    def record_approval(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        decision: ApprovalDecision,
        reason: str,
        expected_revision: int,
        rejection_cooldown_seconds: int = 0,
        now: datetime | None = None,
    ) -> CampaignRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._get_in_connection(connection, campaign_id)
                if record.revision != expected_revision:
                    raise StaleCampaignRevision(
                        f"Expected revision {expected_revision}, found {record.revision}."
                    )
                if record.state != CampaignState.APPROVAL_PENDING:
                    raise CampaignApprovalError("Campaign is not awaiting approval.")
                if actor_id == record.generated_by:
                    raise CampaignApprovalError("Candidate generator cannot approve its own Campaign.")
                try:
                    connection.execute(
                        "INSERT INTO campaign_approvals (approval_id, campaign_id, actor_id, "
                        "decision, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            f"approval:{uuid.uuid4()}",
                            campaign_id,
                            actor_id,
                            decision.value,
                            reason,
                            now.isoformat(),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise CampaignApprovalError("Actor already submitted a Campaign decision.") from exc

                next_state = record.state
                cooldown_until = None
                if decision == ApprovalDecision.REJECT:
                    next_state = CampaignState.REJECTED
                    if rejection_cooldown_seconds:
                        cooldown_until = now + timedelta(seconds=rejection_cooldown_seconds)
                else:
                    approvals = connection.execute(
                        "SELECT COUNT(*) AS count FROM campaign_approvals "
                        "WHERE campaign_id = ? AND decision = 'approve'",
                        (campaign_id,),
                    ).fetchone()["count"]
                    if approvals >= record.required_approvals:
                        next_state = CampaignState.AUTHORIZED

                connection.execute(
                    "UPDATE campaigns SET state = ?, updated_at = ?, cooldown_until = ?, "
                    "revision = revision + 1 WHERE campaign_id = ? AND revision = ?",
                    (
                        next_state.value,
                        now.isoformat(),
                        cooldown_until.isoformat() if cooldown_until else None,
                        campaign_id,
                        expected_revision,
                    ),
                )
                self._append_event(
                    connection,
                    campaign_id=campaign_id,
                    event_type="approval_recorded",
                    actor_id=actor_id,
                    payload={
                        "decision": decision.value,
                        "reason": reason,
                        "resulting_state": next_state.value,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._get_in_connection(connection, campaign_id)
            except Exception:
                connection.rollback()
                raise

    def add_model_evidence(
        self,
        *,
        base_model_id: str,
        problem_cluster: str,
        trace_id: str,
        task_id: str,
        trust_level: str,
        minimum_traces: int,
        minimum_distinct_tasks: int,
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> ModelEvidenceSnapshot:
        if trust_level == "untrusted":
            raise ValueError("Untrusted traces cannot contribute model evidence.")
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO model_evidence (base_model_id, problem_cluster, trace_id, "
                    "task_id, trust_level, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        base_model_id,
                        problem_cluster,
                        trace_id,
                        task_id,
                        trust_level,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount:
                    self._append_event(
                        connection,
                        campaign_id=None,
                        event_type="model_evidence_added",
                        actor_id=actor_id,
                        payload={
                            "base_model_id": base_model_id,
                            "problem_cluster": problem_cluster,
                            "trace_id": trace_id,
                            "task_id": task_id,
                            "trust_level": trust_level,
                        },
                        created_at=now,
                    )
                snapshot = self._model_evidence_snapshot(
                    connection,
                    base_model_id=base_model_id,
                    problem_cluster=problem_cluster,
                    minimum_traces=minimum_traces,
                    minimum_distinct_tasks=minimum_distinct_tasks,
                )
                connection.commit()
                return snapshot
            except Exception:
                connection.rollback()
                raise

    def get_model_evidence(
        self,
        *,
        base_model_id: str,
        problem_cluster: str,
        minimum_traces: int,
        minimum_distinct_tasks: int,
    ) -> ModelEvidenceSnapshot:
        with self._connection() as connection:
            return self._model_evidence_snapshot(
                connection,
                base_model_id=base_model_id,
                problem_cluster=problem_cluster,
                minimum_traces=minimum_traces,
                minimum_distinct_tasks=minimum_distinct_tasks,
            )

    def get(self, campaign_id: str) -> CampaignRecord:
        with self._connection() as connection:
            return self._get_in_connection(connection, campaign_id)

    def find_open_by_target(self, target_key: str) -> CampaignRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM campaigns WHERE target_key = ? AND state IN "
                "('open','evidence_accumulating','candidate_ready','evaluation_pending',"
                "'approval_pending','authorized') LIMIT 1",
                (target_key,),
            ).fetchone()
            return self._row_to_campaign(row) if row else None

    def approvals(self, campaign_id: str) -> list[CampaignApproval]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM campaign_approvals WHERE campaign_id = ? ORDER BY created_at",
                (campaign_id,),
            ).fetchall()
            return [
                CampaignApproval(
                    approval_id=row["approval_id"],
                    campaign_id=row["campaign_id"],
                    actor_id=row["actor_id"],
                    decision=ApprovalDecision(row["decision"]),
                    reason=row["reason"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    def audit_events(self) -> list[CampaignAuditEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM campaign_audit_events ORDER BY sequence"
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> CampaignCheckpoint:
        events = self.audit_events()
        return CampaignCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(self, checkpoint: CampaignCheckpoint | None = None) -> bool:
        events = self.audit_events()
        previous = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous:
                raise CampaignAuditIntegrityError("Campaign audit sequence or hash chain is broken.")
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                campaign_id=event.campaign_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise CampaignAuditIntegrityError("Campaign audit event content was modified.")
            previous = event.event_hash
        if checkpoint is not None:
            current = CampaignCheckpoint(event_count=len(events), head_hash=previous)
            if (
                current.event_count != checkpoint.event_count
                or current.head_hash != checkpoint.head_hash
            ):
                raise CampaignAuditIntegrityError(
                    "Campaign audit does not match the externally anchored checkpoint."
                )
        return True

    def _model_evidence_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        base_model_id: str,
        problem_cluster: str,
        minimum_traces: int,
        minimum_distinct_tasks: int,
    ) -> ModelEvidenceSnapshot:
        rows = connection.execute(
            "SELECT trace_id, task_id FROM model_evidence WHERE base_model_id = ? "
            "AND problem_cluster = ? ORDER BY created_at, trace_id",
            (base_model_id, problem_cluster),
        ).fetchall()
        trace_ids = tuple(row["trace_id"] for row in rows)
        task_ids = tuple(dict.fromkeys(row["task_id"] for row in rows))
        return ModelEvidenceSnapshot(
            base_model_id=base_model_id,
            problem_cluster=problem_cluster,
            trace_ids=trace_ids,
            task_ids=task_ids,
            ready=(
                len(trace_ids) >= minimum_traces
                and len(task_ids) >= minimum_distinct_tasks
            ),
        )

    def _get_in_connection(self, connection: sqlite3.Connection, campaign_id: str) -> CampaignRecord:
        row = connection.execute(
            "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Campaign: {campaign_id}")
        return self._row_to_campaign(row)

    @staticmethod
    def _row_to_campaign(row: sqlite3.Row) -> CampaignRecord:
        return CampaignRecord(
            campaign_id=row["campaign_id"],
            campaign_type=CampaignType(row["campaign_type"]),
            target_key=row["target_key"],
            fingerprint=row["fingerprint"],
            state=CampaignState(row["state"]),
            risk=CampaignRisk(row["risk"]),
            generated_by=row["generated_by"],
            required_approvals=row["required_approvals"],
            candidate_ref=row["candidate_ref"],
            artifact_payload=json.loads(row["artifact_json"]) if row["artifact_json"] else None,
            metadata=json.loads(row["metadata_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            cooldown_until=(
                datetime.fromisoformat(row["cooldown_until"]) if row["cooldown_until"] else None
            ),
            revision=row["revision"],
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        campaign_id: str | None,
        event_type: str,
        actor_id: str,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM campaign_audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = (last["sequence"] + 1) if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            campaign_id=campaign_id,
            event_type=event_type,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO campaign_audit_events (sequence, event_id, campaign_id, event_type, "
            "actor_id, payload_json, created_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                campaign_id,
                event_type,
                actor_id,
                self._json(payload),
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
        campaign_id: str | None,
        event_type: str,
        actor_id: str,
        payload: dict[str, Any],
        created_at: datetime,
        previous_hash: str,
    ) -> str:
        canonical = cls._json(
            {
                "sequence": sequence,
                "event_id": event_id,
                "campaign_id": campaign_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "payload": payload,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> CampaignAuditEvent:
        return CampaignAuditEvent(
            sequence=row["sequence"],
            event_id=row["event_id"],
            campaign_id=row["campaign_id"],
            event_type=row["event_type"],
            actor_id=row["actor_id"],
            payload=json.loads(row["payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
