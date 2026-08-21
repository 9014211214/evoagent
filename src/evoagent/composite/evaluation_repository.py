from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256

from .evaluation import (
    CompositeSnapshotEvaluation,
    CompositeStopAction,
    CompositeStopDecision,
    CompositeStopPolicy,
    build_composite_stop_decision,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_GENESIS_HASH = "0" * 64


class CompositeEvaluationEventType(str, Enum):
    POLICY_REGISTERED = "policy_registered"
    EVALUATION_RECORDED = "evaluation_recorded"
    DECISION_RECORDED = "decision_recorded"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


class CompositeEvaluationPolicyRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy: CompositeStopPolicy
    registered_by: str = Field(pattern=_SAFE_ID_PATTERN)
    registered_at: datetime

    @field_validator("registered_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite policy registration time")


class CompositeEvaluationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evaluation: CompositeSnapshotEvaluation
    recorded_by: str = Field(pattern=_SAFE_ID_PATTERN)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite evaluation recording time")

    @model_validator(mode="after")
    def validate_identity(self):
        if (
            self.lineage_id != self.evaluation.lineage_id
            or self.snapshot_id != self.evaluation.snapshot_id
        ):
            raise ValueError(
                "Composite evaluation record identity differs from its evidence."
            )
        if self.recorded_by != self.evaluation.evaluator_id:
            raise ValueError(
                "Composite evaluation recorder differs from the evaluator."
            )
        if self.recorded_at < self.evaluation.evaluated_at:
            raise ValueError(
                "Composite evaluation recording predates its evidence."
            )
        return self


class CompositeDecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    decision: CompositeStopDecision
    recorded_by: str = Field(pattern=_SAFE_ID_PATTERN)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite decision recording time")

    @model_validator(mode="after")
    def validate_identity(self):
        if (
            self.lineage_id != self.decision.lineage_id
            or self.snapshot_id != self.decision.snapshot_id
        ):
            raise ValueError(
                "Composite decision record identity differs from its evidence."
            )
        if self.recorded_by != self.decision.decided_by:
            raise ValueError(
                "Composite decision recorder differs from the decision actor."
            )
        if self.recorded_at < self.decision.decided_at:
            raise ValueError(
                "Composite decision recording predates its evidence."
            )
        return self


class CompositeEvaluationAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    event_type: CompositeEvaluationEventType
    reason: str
    metadata: dict[str, Any]
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite evaluation audit time")


class CompositeEvaluationCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


class CompositeEvaluationConflictError(RuntimeError):
    pass


class CompositeEvaluationAuditIntegrityError(RuntimeError):
    pass


class SQLiteCompositeEvaluationRepository:
    """Persistent frozen evaluations and deterministic stop decisions."""

    def __init__(self, path: str | Path):
        raw_path = Path(path).expanduser()
        if raw_path.is_symlink():
            raise ValueError(
                "Composite evaluation Repository path must not be a symlink."
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
                CREATE TABLE IF NOT EXISTS composite_evaluation_policies (
                    lineage_id TEXT PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    registered_by TEXT NOT NULL,
                    registered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS composite_evaluations (
                    lineage_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    round_index INTEGER NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    recorded_by TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(lineage_id, snapshot_id),
                    UNIQUE(lineage_id, round_index),
                    FOREIGN KEY(lineage_id)
                        REFERENCES composite_evaluation_policies(lineage_id)
                );

                CREATE TABLE IF NOT EXISTS composite_stop_decisions (
                    lineage_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    recorded_by TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(lineage_id, snapshot_id),
                    FOREIGN KEY(lineage_id, snapshot_id)
                        REFERENCES composite_evaluations(lineage_id, snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS composite_evaluation_audit_events (
                    lineage_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    snapshot_id TEXT,
                    event_type TEXT NOT NULL,
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

    def register_policy(
        self,
        lineage_id: str,
        policy: CompositeStopPolicy,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> CompositeEvaluationPolicyRecord:
        effective = self._effective_now(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM composite_evaluation_policies "
                    "WHERE lineage_id = ?",
                    (lineage_id,),
                ).fetchone()
                if row is not None:
                    existing = self._row_to_policy(row)
                    if (
                        existing.policy == policy
                        and existing.registered_by == actor_id
                    ):
                        connection.commit()
                        return existing
                    raise CompositeEvaluationConflictError(
                        "Composite lineage contains another frozen stop policy."
                    )
                record = CompositeEvaluationPolicyRecord(
                    lineage_id=lineage_id,
                    policy=policy,
                    registered_by=actor_id,
                    registered_at=effective,
                )
                connection.execute(
                    "INSERT INTO composite_evaluation_policies "
                    "(lineage_id, policy_json, registered_by, registered_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        lineage_id,
                        self._json(policy.model_dump(mode="json")),
                        actor_id,
                        effective.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    lineage_id=lineage_id,
                    event_type=CompositeEvaluationEventType.POLICY_REGISTERED,
                    actor_id=actor_id,
                    reason="Frozen composite stop policy registered.",
                    metadata={
                        "policy_hash": policy.policy_hash,
                        "max_rounds": policy.max_rounds,
                        "target_composite_score": (
                            policy.target_composite_score
                        ),
                    },
                    created_at=effective,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def record_evaluation(
        self,
        evaluation: CompositeSnapshotEvaluation,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> CompositeEvaluationRecord:
        effective = self._effective_now(now)
        if actor_id != evaluation.evaluator_id:
            raise ValueError(
                "Composite evaluation recorder must be the evaluator."
            )
        if evaluation.evaluated_at > effective:
            raise ValueError(
                "Composite evaluation postdates its Repository write."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_policy(connection, evaluation.lineage_id)
                row = self._evaluation_row(
                    connection,
                    evaluation.lineage_id,
                    evaluation.snapshot_id,
                )
                if row is not None:
                    existing = self._row_to_evaluation(row)
                    if (
                        existing.evaluation == evaluation
                        and existing.recorded_by == actor_id
                    ):
                        connection.commit()
                        return existing
                    raise CompositeEvaluationConflictError(
                        "Composite snapshot contains another frozen evaluation."
                    )
                self._validate_next_evaluation(connection, evaluation)
                record = CompositeEvaluationRecord(
                    lineage_id=evaluation.lineage_id,
                    snapshot_id=evaluation.snapshot_id,
                    evaluation=evaluation,
                    recorded_by=actor_id,
                    recorded_at=effective,
                )
                connection.execute(
                    "INSERT INTO composite_evaluations "
                    "(lineage_id, snapshot_id, round_index, evaluation_json, "
                    "recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        evaluation.lineage_id,
                        evaluation.snapshot_id,
                        evaluation.round_index,
                        self._json(evaluation.model_dump(mode="json")),
                        actor_id,
                        effective.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    lineage_id=evaluation.lineage_id,
                    snapshot_id=evaluation.snapshot_id,
                    event_type=(
                        CompositeEvaluationEventType.EVALUATION_RECORDED
                    ),
                    actor_id=actor_id,
                    reason="Frozen composite snapshot evaluation recorded.",
                    metadata={
                        "evaluation_hash": evaluation.evaluation_hash,
                        "snapshot_manifest_hash": (
                            evaluation.snapshot_manifest_hash
                        ),
                        "composite_score": evaluation.composite_score,
                        "safety_violation_count": (
                            evaluation.safety_violation_count
                        ),
                        "regression_count": evaluation.regression_count,
                    },
                    created_at=effective,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def record_decision(
        self,
        decision: CompositeStopDecision,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> CompositeDecisionRecord:
        effective = self._effective_now(now)
        if actor_id != decision.decided_by:
            raise ValueError(
                "Composite decision recorder must be the decision actor."
            )
        if decision.decided_at > effective:
            raise ValueError(
                "Composite stop decision postdates its Repository write."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                policy = self._require_policy(
                    connection,
                    decision.lineage_id,
                ).policy
                evaluation_record = self._require_evaluation(
                    connection,
                    decision.lineage_id,
                    decision.snapshot_id,
                )
                evaluation = evaluation_record.evaluation
                self._verify_decision(evaluation, policy, decision)
                row = self._decision_row(
                    connection,
                    decision.lineage_id,
                    decision.snapshot_id,
                )
                if row is not None:
                    existing = self._row_to_decision(row)
                    if (
                        existing.decision == decision
                        and existing.recorded_by == actor_id
                    ):
                        connection.commit()
                        return existing
                    raise CompositeEvaluationConflictError(
                        "Composite snapshot contains another stop decision."
                    )
                record = CompositeDecisionRecord(
                    lineage_id=decision.lineage_id,
                    snapshot_id=decision.snapshot_id,
                    decision=decision,
                    recorded_by=actor_id,
                    recorded_at=effective,
                )
                connection.execute(
                    "INSERT INTO composite_stop_decisions "
                    "(lineage_id, snapshot_id, decision_json, recorded_by, "
                    "recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        decision.lineage_id,
                        decision.snapshot_id,
                        self._json(decision.model_dump(mode="json")),
                        actor_id,
                        effective.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    lineage_id=decision.lineage_id,
                    snapshot_id=decision.snapshot_id,
                    event_type=(
                        CompositeEvaluationEventType.DECISION_RECORDED
                    ),
                    actor_id=actor_id,
                    reason="Deterministic composite stop decision recorded.",
                    metadata={
                        "decision_hash": decision.decision_hash,
                        "evaluation_hash": decision.evaluation_hash,
                        "action": decision.action.value,
                        "actionable_case_count": len(
                            decision.actionable_case_ids
                        ),
                        "budget_exhausted": decision.budget_exhausted,
                    },
                    created_at=effective,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def policy(self, lineage_id: str) -> CompositeEvaluationPolicyRecord:
        with self._connection() as connection:
            return self._require_policy(connection, lineage_id)

    def evaluation(
        self,
        lineage_id: str,
        snapshot_id: str,
    ) -> CompositeEvaluationRecord:
        with self._connection() as connection:
            return self._require_evaluation(
                connection,
                lineage_id,
                snapshot_id,
            )

    def decision(
        self,
        lineage_id: str,
        snapshot_id: str,
    ) -> CompositeDecisionRecord:
        with self._connection() as connection:
            row = self._decision_row(connection, lineage_id, snapshot_id)
            if row is None:
                raise KeyError(
                    f"Unknown composite decision: {lineage_id}/{snapshot_id}"
                )
            return self._row_to_decision(row)

    def list_evaluations(
        self,
        lineage_id: str,
    ) -> tuple[CompositeEvaluationRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM composite_evaluations WHERE lineage_id = ? "
                "ORDER BY round_index",
                (lineage_id,),
            ).fetchall()
            return tuple(self._row_to_evaluation(row) for row in rows)

    def list_decisions(
        self,
        lineage_id: str,
    ) -> tuple[CompositeDecisionRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT d.* FROM composite_stop_decisions d "
                "JOIN composite_evaluations e ON "
                "e.lineage_id = d.lineage_id AND "
                "e.snapshot_id = d.snapshot_id "
                "WHERE d.lineage_id = ? ORDER BY e.round_index",
                (lineage_id,),
            ).fetchall()
            return tuple(self._row_to_decision(row) for row in rows)

    def events(
        self,
        lineage_id: str,
    ) -> tuple[CompositeEvaluationAuditEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM composite_evaluation_audit_events "
                "WHERE lineage_id = ? ORDER BY sequence",
                (lineage_id,),
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)

    def checkpoint(self, lineage_id: str) -> CompositeEvaluationCheckpoint:
        events = self.events(lineage_id)
        return CompositeEvaluationCheckpoint(
            lineage_id=lineage_id,
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_state(self, lineage_id: str) -> bool:
        policy_record = self.policy(lineage_id)
        evaluations = self.list_evaluations(lineage_id)
        decisions = self.list_decisions(lineage_id)
        if not evaluations:
            raise CompositeEvaluationAuditIntegrityError(
                "Composite evaluation lineage contains no evaluations."
            )
        rounds = tuple(item.evaluation.round_index for item in evaluations)
        if rounds != tuple(range(len(evaluations))):
            raise CompositeEvaluationAuditIntegrityError(
                "Composite evaluation rounds are missing or reordered."
            )
        if len(decisions) != len(evaluations):
            raise CompositeEvaluationAuditIntegrityError(
                "Composite evaluation lineage lacks one decision per round."
            )
        task_manifest_hash = evaluations[0].evaluation.task_manifest_hash
        for index, (evaluation_record, decision_record) in enumerate(
            zip(evaluations, decisions, strict=True)
        ):
            evaluation = evaluation_record.evaluation
            decision = decision_record.decision
            if evaluation.task_manifest_hash != task_manifest_hash:
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite evaluation changed the frozen Task manifest."
                )
            if index == 0:
                if evaluation.parent_evaluation_hash is not None:
                    raise CompositeEvaluationAuditIntegrityError(
                        "Initial composite evaluation claims a parent."
                    )
            elif (
                evaluation.parent_evaluation_hash
                != evaluations[index - 1].evaluation.evaluation_hash
            ):
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite evaluation parent hash chain is broken."
                )
            try:
                self._verify_decision(
                    evaluation,
                    policy_record.policy,
                    decision,
                )
            except ValueError as exc:
                raise CompositeEvaluationAuditIntegrityError(str(exc)) from exc
            if (
                index < len(decisions) - 1
                and decision.action != CompositeStopAction.CONTINUE
            ):
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite evaluation continued after a terminal decision."
                )
            if (
                evaluation_record.recorded_at > decision_record.recorded_at
                or evaluation.evaluated_at > decision.decided_at
            ):
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite evaluation and decision chronology is invalid."
                )
        self.verify_audit(lineage_id)
        self._verify_semantic_events(
            policy_record,
            evaluations,
            decisions,
        )
        return True

    def verify_audit(
        self,
        lineage_id: str,
        checkpoint: CompositeEvaluationCheckpoint | None = None,
    ) -> bool:
        events = self.events(lineage_id)
        previous = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous
            ):
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite evaluation audit sequence or chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                lineage_id=event.lineage_id,
                snapshot_id=event.snapshot_id,
                event_type=event.event_type,
                reason=event.reason,
                metadata=event.metadata,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite evaluation audit event was modified."
                )
            previous = event.event_hash
        current = CompositeEvaluationCheckpoint(
            lineage_id=lineage_id,
            event_count=len(events),
            head_hash=previous,
        )
        if checkpoint is not None and current != checkpoint:
            raise CompositeEvaluationAuditIntegrityError(
                "Composite evaluation audit differs from its checkpoint."
            )
        return True

    def _validate_next_evaluation(
        self,
        connection: sqlite3.Connection,
        evaluation: CompositeSnapshotEvaluation,
    ) -> None:
        prior_rows = connection.execute(
            "SELECT * FROM composite_evaluations WHERE lineage_id = ? "
            "ORDER BY round_index",
            (evaluation.lineage_id,),
        ).fetchall()
        if evaluation.round_index != len(prior_rows):
            raise CompositeEvaluationConflictError(
                "Composite evaluation round is not the next contiguous round."
            )
        if not prior_rows:
            if evaluation.parent_evaluation_hash is not None:
                raise ValueError(
                    "Initial composite evaluation must not claim a parent."
                )
            return
        parent = self._row_to_evaluation(prior_rows[-1]).evaluation
        parent_decision_row = self._decision_row(
            connection,
            parent.lineage_id,
            parent.snapshot_id,
        )
        if parent_decision_row is None:
            raise CompositeEvaluationConflictError(
                "Composite child evaluation requires its parent stop decision."
            )
        parent_decision = self._row_to_decision(
            parent_decision_row
        ).decision
        if parent_decision.action != CompositeStopAction.CONTINUE:
            raise CompositeEvaluationConflictError(
                "Composite evaluation cannot continue after a terminal decision."
            )
        if (
            evaluation.parent_evaluation_hash != parent.evaluation_hash
            or evaluation.task_manifest_hash != parent.task_manifest_hash
        ):
            raise ValueError(
                "Composite child evaluation differs from its frozen parent evidence."
            )

    @staticmethod
    def _verify_decision(
        evaluation: CompositeSnapshotEvaluation,
        policy: CompositeStopPolicy,
        decision: CompositeStopDecision,
    ) -> None:
        if (
            decision.lineage_id != evaluation.lineage_id
            or decision.snapshot_id != evaluation.snapshot_id
            or decision.round_index != evaluation.round_index
            or decision.evaluation_hash != evaluation.evaluation_hash
            or decision.policy_hash != policy.policy_hash
        ):
            raise ValueError(
                "Composite stop decision differs from evaluation or policy."
            )
        expected = build_composite_stop_decision(
            evaluation,
            policy,
            decision_id=decision.decision_id,
            actionable_case_ids=decision.actionable_case_ids,
            budget_exhausted=decision.budget_exhausted,
            decided_by=decision.decided_by,
            decided_at=decision.decided_at,
        )
        if expected != decision:
            raise ValueError(
                "Composite stop decision differs from deterministic policy."
            )

    def _verify_semantic_events(
        self,
        policy_record: CompositeEvaluationPolicyRecord,
        evaluations: tuple[CompositeEvaluationRecord, ...],
        decisions: tuple[CompositeDecisionRecord, ...],
    ) -> None:
        events = self.events(policy_record.lineage_id)
        if len(events) != 1 + 2 * len(evaluations):
            raise CompositeEvaluationAuditIntegrityError(
                "Composite evaluation audit omits or duplicates lifecycle events."
            )
        policy_event = events[0]
        policy = policy_record.policy
        if (
            policy_event.event_type
            != CompositeEvaluationEventType.POLICY_REGISTERED
            or policy_event.snapshot_id is not None
            or policy_event.reason
            != "Frozen composite stop policy registered."
            or policy_event.metadata
            != {
                "policy_hash": policy.policy_hash,
                "max_rounds": policy.max_rounds,
                "target_composite_score": policy.target_composite_score,
            }
            or policy_event.actor_id != policy_record.registered_by
            or policy_event.created_at != policy_record.registered_at
        ):
            raise CompositeEvaluationAuditIntegrityError(
                "Composite stop policy audit semantics differ."
            )
        for index, (evaluation_record, decision_record) in enumerate(
            zip(evaluations, decisions, strict=True)
        ):
            evaluation_event = events[1 + 2 * index]
            decision_event = events[2 + 2 * index]
            evaluation = evaluation_record.evaluation
            decision = decision_record.decision
            if (
                evaluation_event.event_type
                != CompositeEvaluationEventType.EVALUATION_RECORDED
                or evaluation_event.snapshot_id != evaluation.snapshot_id
                or evaluation_event.reason
                != "Frozen composite snapshot evaluation recorded."
                or evaluation_event.metadata
                != {
                    "evaluation_hash": evaluation.evaluation_hash,
                    "snapshot_manifest_hash": (
                        evaluation.snapshot_manifest_hash
                    ),
                    "composite_score": evaluation.composite_score,
                    "safety_violation_count": (
                        evaluation.safety_violation_count
                    ),
                    "regression_count": evaluation.regression_count,
                }
                or evaluation_event.actor_id != evaluation_record.recorded_by
                or evaluation_event.created_at != evaluation_record.recorded_at
            ):
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite evaluation audit semantics differ."
                )
            if (
                decision_event.event_type
                != CompositeEvaluationEventType.DECISION_RECORDED
                or decision_event.snapshot_id != decision.snapshot_id
                or decision_event.reason
                != "Deterministic composite stop decision recorded."
                or decision_event.metadata
                != {
                    "decision_hash": decision.decision_hash,
                    "evaluation_hash": decision.evaluation_hash,
                    "action": decision.action.value,
                    "actionable_case_count": len(
                        decision.actionable_case_ids
                    ),
                    "budget_exhausted": decision.budget_exhausted,
                }
                or decision_event.actor_id != decision_record.recorded_by
                or decision_event.created_at != decision_record.recorded_at
            ):
                raise CompositeEvaluationAuditIntegrityError(
                    "Composite stop decision audit semantics differ."
                )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        lineage_id: str,
        event_type: CompositeEvaluationEventType,
        actor_id: str,
        reason: str,
        metadata: dict[str, Any],
        created_at: datetime,
        snapshot_id: str | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash "
            "FROM composite_evaluation_audit_events "
            "WHERE lineage_id = ? ORDER BY sequence DESC LIMIT 1",
            (lineage_id,),
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"composite-evaluation-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            lineage_id=lineage_id,
            snapshot_id=snapshot_id,
            event_type=event_type,
            reason=reason,
            metadata=metadata,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO composite_evaluation_audit_events "
            "(lineage_id, sequence, event_id, snapshot_id, event_type, "
            "reason, metadata_json, actor_id, created_at, previous_hash, "
            "event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lineage_id,
                sequence,
                event_id,
                snapshot_id,
                event_type.value,
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
        lineage_id: str,
        snapshot_id: str | None,
        event_type: CompositeEvaluationEventType,
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
                "lineage_id": lineage_id,
                "snapshot_id": snapshot_id,
                "event_type": event_type.value,
                "reason": reason,
                "metadata": metadata,
                "actor_id": actor_id,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_policy(row: sqlite3.Row) -> CompositeEvaluationPolicyRecord:
        return CompositeEvaluationPolicyRecord(
            lineage_id=row["lineage_id"],
            policy=CompositeStopPolicy.model_validate_json(row["policy_json"]),
            registered_by=row["registered_by"],
            registered_at=datetime.fromisoformat(row["registered_at"]),
        )

    @staticmethod
    def _row_to_evaluation(row: sqlite3.Row) -> CompositeEvaluationRecord:
        return CompositeEvaluationRecord(
            lineage_id=row["lineage_id"],
            snapshot_id=row["snapshot_id"],
            evaluation=CompositeSnapshotEvaluation.model_validate_json(
                row["evaluation_json"]
            ),
            recorded_by=row["recorded_by"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    @staticmethod
    def _row_to_decision(row: sqlite3.Row) -> CompositeDecisionRecord:
        return CompositeDecisionRecord(
            lineage_id=row["lineage_id"],
            snapshot_id=row["snapshot_id"],
            decision=CompositeStopDecision.model_validate_json(
                row["decision_json"]
            ),
            recorded_by=row["recorded_by"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> CompositeEvaluationAuditEvent:
        return CompositeEvaluationAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            lineage_id=row["lineage_id"],
            snapshot_id=row["snapshot_id"],
            event_type=CompositeEvaluationEventType(row["event_type"]),
            reason=row["reason"],
            metadata=json.loads(row["metadata_json"]),
            actor_id=row["actor_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    def _require_policy(
        self,
        connection: sqlite3.Connection,
        lineage_id: str,
    ) -> CompositeEvaluationPolicyRecord:
        row = connection.execute(
            "SELECT * FROM composite_evaluation_policies "
            "WHERE lineage_id = ?",
            (lineage_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"Unknown composite evaluation policy: {lineage_id}"
            )
        return self._row_to_policy(row)

    @staticmethod
    def _evaluation_row(
        connection: sqlite3.Connection,
        lineage_id: str,
        snapshot_id: str,
    ):
        return connection.execute(
            "SELECT * FROM composite_evaluations "
            "WHERE lineage_id = ? AND snapshot_id = ?",
            (lineage_id, snapshot_id),
        ).fetchone()

    def _require_evaluation(
        self,
        connection: sqlite3.Connection,
        lineage_id: str,
        snapshot_id: str,
    ) -> CompositeEvaluationRecord:
        row = self._evaluation_row(connection, lineage_id, snapshot_id)
        if row is None:
            raise KeyError(
                f"Unknown composite evaluation: {lineage_id}/{snapshot_id}"
            )
        return self._row_to_evaluation(row)

    @staticmethod
    def _decision_row(
        connection: sqlite3.Connection,
        lineage_id: str,
        snapshot_id: str,
    ):
        return connection.execute(
            "SELECT * FROM composite_stop_decisions "
            "WHERE lineage_id = ? AND snapshot_id = ?",
            (lineage_id, snapshot_id),
        ).fetchone()

    @staticmethod
    def _effective_now(value: datetime | None) -> datetime:
        effective = value or datetime.now(timezone.utc)
        if effective.tzinfo is None or effective.utcoffset() is None:
            raise ValueError(
                "Composite evaluation Repository time must include a timezone."
            )
        if effective > datetime.now(timezone.utc):
            raise ValueError(
                "Composite evaluation Repository time must not be in the future."
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
    "CompositeDecisionRecord",
    "CompositeEvaluationAuditEvent",
    "CompositeEvaluationAuditIntegrityError",
    "CompositeEvaluationCheckpoint",
    "CompositeEvaluationConflictError",
    "CompositeEvaluationEventType",
    "CompositeEvaluationPolicyRecord",
    "CompositeEvaluationRecord",
    "SQLiteCompositeEvaluationRepository",
]
