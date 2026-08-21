from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.model_registry.models import canonical_sha256
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationOutcome,
    GenerationPlan,
    GenerationRecord,
    GenerationStatus,
    ProgramAction,
    ProgramAuditEvent,
    ProgramCheckpoint,
    ProgramDecision,
    ProgramEventType,
    ProgramHead,
    ProgramLearningSignal,
    ProgramRecord,
    ProgramState,
)
from evoagent.release.models import ReleaseDecisionAction


_GENESIS_HASH = "0" * 64


class ProgramConflictError(RuntimeError):
    pass


class StaleProgramRevision(RuntimeError):
    pass


class ProgramAuditIntegrityError(RuntimeError):
    pass


class SQLiteEvolutionProgramRepository:
    """Immutable multi-generation evidence with one optimistic Program head."""

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
                CREATE TABLE IF NOT EXISTS evolution_programs (
                    program_id TEXT PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS program_heads (
                    program_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    current_generation_index INTEGER NOT NULL,
                    active_generation_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    rollback_count INTEGER NOT NULL,
                    hold_count INTEGER NOT NULL,
                    generation_campaign_count INTEGER NOT NULL,
                    total_pairs INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    total_cost_usd REAL NOT NULL,
                    last_decision_id TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(program_id) REFERENCES evolution_programs(program_id)
                );

                CREATE TABLE IF NOT EXISTS program_generations (
                    program_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    generation_index INTEGER NOT NULL,
                    parent_generation_id TEXT,
                    status TEXT NOT NULL,
                    plan_json TEXT,
                    outcome_json TEXT,
                    campaign_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(program_id, generation_id),
                    UNIQUE(program_id, generation_index),
                    FOREIGN KEY(program_id) REFERENCES evolution_programs(program_id)
                );

                CREATE TABLE IF NOT EXISTS program_signals (
                    signal_id TEXT PRIMARY KEY,
                    program_id TEXT NOT NULL,
                    signal_hash TEXT NOT NULL,
                    signal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(program_id) REFERENCES evolution_programs(program_id)
                );

                CREATE TABLE IF NOT EXISTS program_attributions (
                    receipt_id TEXT PRIMARY KEY,
                    program_id TEXT NOT NULL,
                    signal_id TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(program_id) REFERENCES evolution_programs(program_id)
                );

                CREATE TABLE IF NOT EXISTS program_decisions (
                    decision_id TEXT PRIMARY KEY,
                    program_id TEXT NOT NULL,
                    decision_hash TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(program_id) REFERENCES evolution_programs(program_id)
                );

                CREATE TABLE IF NOT EXISTS program_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    program_id TEXT NOT NULL,
                    generation_id TEXT,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def register(
        self,
        *,
        program_id: str,
        policy: EvolutionProgramPolicy,
        initial_outcome: GenerationOutcome,
        created_by: str,
        now: datetime | None = None,
    ) -> tuple[ProgramRecord, bool]:
        now = now or datetime.now(timezone.utc)
        if initial_outcome.program_id != program_id or initial_outcome.generation_index != 0:
            raise ValueError("Program registration requires its observed Generation 0 outcome.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM evolution_programs WHERE program_id = ?",
                    (program_id,),
                ).fetchone()
                if existing is not None:
                    record = self._row_to_program(existing)
                    generation = self._require_generation(
                        connection, program_id, initial_outcome.generation_id
                    )
                    if record.policy != policy or generation.outcome != initial_outcome:
                        raise ProgramConflictError(
                            "Existing Program differs from registration evidence."
                        )
                    connection.rollback()
                    return record, True
                state = ProgramState.RUNNING
                connection.execute(
                    "INSERT INTO evolution_programs "
                    "(program_id, policy_json, state, created_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        program_id,
                        self._json(policy.model_dump(mode="json")),
                        state.value,
                        created_by,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                generation_status = self._status_for_outcome(initial_outcome)
                self._insert_generation(
                    connection,
                    GenerationRecord(
                        program_id=program_id,
                        generation_id=initial_outcome.generation_id,
                        generation_index=0,
                        parent_generation_id=None,
                        status=generation_status,
                        outcome=initial_outcome,
                        created_at=now,
                        updated_at=now,
                    ),
                )
                rollback_count = int(
                    initial_outcome.release_action == ReleaseDecisionAction.ROLLBACK
                )
                hold_count = int(
                    initial_outcome.release_action == ReleaseDecisionAction.HOLD
                )
                connection.execute(
                    "INSERT INTO program_heads "
                    "(program_id, state, current_generation_index, active_generation_id, "
                    "revision, rollback_count, hold_count, generation_campaign_count, "
                    "total_pairs, total_tokens, total_cost_usd, last_decision_id, updated_at) "
                    "VALUES (?, ?, 0, ?, 0, ?, ?, 0, ?, ?, ?, NULL, ?)",
                    (
                        program_id,
                        state.value,
                        initial_outcome.generation_id,
                        rollback_count,
                        hold_count,
                        initial_outcome.pair_count,
                        initial_outcome.total_tokens,
                        initial_outcome.total_cost_usd,
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    program_id=program_id,
                    generation_id=None,
                    event_type=ProgramEventType.PROGRAM_REGISTERED,
                    actor_id=created_by,
                    reason="Persistent multi-generation Program registered.",
                    payload={"policy_hash": policy.policy_hash},
                    created_at=now,
                )
                self._append_event(
                    connection,
                    program_id=program_id,
                    generation_id=initial_outcome.generation_id,
                    event_type=ProgramEventType.GENERATION_OBSERVED,
                    actor_id=created_by,
                    reason="Observed terminal release evidence recorded as Generation 0.",
                    payload={
                        "outcome_hash": initial_outcome.outcome_hash,
                        "release_action": initial_outcome.release_action.value,
                    },
                    created_at=now,
                )
                connection.commit()
                return self.get_program(program_id), False
            except Exception:
                connection.rollback()
                raise

    def store_signal(
        self,
        signal: ProgramLearningSignal,
        *,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[ProgramLearningSignal, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_program(connection, signal.program_id)
                row = connection.execute(
                    "SELECT signal_json FROM program_signals WHERE signal_id = ?",
                    (signal.signal_id,),
                ).fetchone()
                if row is not None:
                    existing = ProgramLearningSignal.model_validate_json(row["signal_json"])
                    if existing != signal:
                        raise ProgramConflictError("Learning signal ID has conflicting content.")
                    connection.rollback()
                    return existing, True
                connection.execute(
                    "INSERT INTO program_signals "
                    "(signal_id, program_id, signal_hash, signal_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        signal.signal_id,
                        signal.program_id,
                        signal.signal_hash,
                        signal.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    program_id=signal.program_id,
                    generation_id=f"generation:{signal.program_id}:{signal.generation_index}",
                    event_type=ProgramEventType.SIGNAL_STORED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={
                        "signal_id": signal.signal_id,
                        "signal_hash": signal.signal_hash,
                        "causal_attribution_claimed": False,
                    },
                    created_at=now,
                )
                connection.commit()
                return signal, False
            except Exception:
                connection.rollback()
                raise

    def store_attribution(
        self,
        program_id: str,
        attribution: AttributionReceipt,
        *,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[AttributionReceipt, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                signal = self._require_signal(connection, attribution.signal_id)
                if signal.program_id != program_id or signal.signal_hash != attribution.signal_hash:
                    raise ValueError("Attribution does not match its persisted Program signal.")
                row = connection.execute(
                    "SELECT receipt_json FROM program_attributions WHERE receipt_id = ?",
                    (attribution.receipt_id,),
                ).fetchone()
                if row is not None:
                    existing = AttributionReceipt.model_validate_json(row["receipt_json"])
                    if existing != attribution:
                        raise ProgramConflictError(
                            "Attribution receipt ID has conflicting content."
                        )
                    connection.rollback()
                    return existing, True
                connection.execute(
                    "INSERT INTO program_attributions "
                    "(receipt_id, program_id, signal_id, receipt_hash, receipt_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        attribution.receipt_id,
                        program_id,
                        attribution.signal_id,
                        attribution.receipt_hash,
                        attribution.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    program_id=program_id,
                    generation_id=None,
                    event_type=ProgramEventType.ATTRIBUTION_STORED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={
                        "receipt_id": attribution.receipt_id,
                        "receipt_hash": attribution.receipt_hash,
                        "failure_layer": attribution.failure_layer.value,
                        "action": attribution.action.value,
                    },
                    created_at=now,
                )
                connection.commit()
                return attribution, False
            except Exception:
                connection.rollback()
                raise

    def plan_generation(
        self,
        plan: GenerationPlan,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[GenerationRecord, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                program = self._require_program(connection, plan.program_id)
                head = self._require_head(connection, plan.program_id)
                self._check_revision(head, expected_revision)
                if ProgramState(head["state"]) != ProgramState.RUNNING:
                    raise ValueError("Program is not accepting a successor generation.")
                if plan.generation_index != int(head["current_generation_index"]) + 1:
                    raise ValueError("Generation indexes must be consecutive.")
                if plan.parent_generation_id != head["active_generation_id"]:
                    raise ValueError("Generation parent differs from the active Program generation.")
                signal = self._require_signal(connection, plan.source_signal_id)
                attribution = self._require_attribution(
                    connection, plan.attribution_receipt_id
                )
                if (
                    signal.signal_hash != plan.source_signal_hash
                    or attribution.receipt_hash != plan.attribution_receipt_hash
                    or attribution.signal_id != signal.signal_id
                ):
                    raise ValueError("Generation plan evidence differs from persisted inputs.")
                existing = self._generation_row(
                    connection, plan.program_id, plan.generation_id
                )
                if existing is not None:
                    record = self._row_to_generation(existing)
                    if record.plan != plan:
                        raise ProgramConflictError(
                            "Generation ID has conflicting immutable plan."
                        )
                    connection.rollback()
                    return record, True
                if plan.generation_index >= program.policy.budget.max_generations:
                    raise ValueError("Generation plan exceeds Program generation budget.")
                record = GenerationRecord(
                    program_id=plan.program_id,
                    generation_id=plan.generation_id,
                    generation_index=plan.generation_index,
                    parent_generation_id=plan.parent_generation_id,
                    status=GenerationStatus.PLANNED,
                    plan=plan,
                    created_at=now,
                    updated_at=now,
                )
                self._insert_generation(connection, record)
                self._append_event(
                    connection,
                    program_id=plan.program_id,
                    generation_id=plan.generation_id,
                    event_type=ProgramEventType.GENERATION_PLANNED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={
                        "plan_id": plan.plan_id,
                        "plan_hash": plan.plan_hash,
                        "parent_generation_id": plan.parent_generation_id,
                    },
                    created_at=now,
                )
                connection.commit()
                return record, False
            except Exception:
                connection.rollback()
                raise

    def bind_campaign(
        self,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, program_id)
                self._check_revision(head, expected_revision)
                record = self._require_generation(connection, program_id, generation_id)
                if record.status != GenerationStatus.PLANNED:
                    if record.campaign_id == campaign_id:
                        connection.rollback()
                        return record
                    raise ValueError("Only a planned generation may bind a Campaign.")
                if record.campaign_id is not None and record.campaign_id != campaign_id:
                    raise ProgramConflictError("Generation is bound to another Campaign.")
                program = self._require_program(connection, program_id)
                campaign_count = int(head["generation_campaign_count"]) + 1
                if campaign_count > program.policy.budget.max_generation_campaigns:
                    raise ValueError("Generation Campaign budget exceeded.")
                connection.execute(
                    "UPDATE program_generations SET campaign_id = ?, updated_at = ? "
                    "WHERE program_id = ? AND generation_id = ?",
                    (campaign_id, now.isoformat(), program_id, generation_id),
                )
                self._update_head(
                    connection,
                    head,
                    generation_campaign_count=campaign_count,
                    revision=int(head["revision"]) + 1,
                    updated_at=now,
                )
                self._append_event(
                    connection,
                    program_id=program_id,
                    generation_id=generation_id,
                    event_type=ProgramEventType.GENERATION_CAMPAIGN_BOUND,
                    actor_id=actor_id,
                    reason=reason,
                    payload={"campaign_id": campaign_id},
                    created_at=now,
                )
                connection.commit()
                return self.get_generation(program_id, generation_id)
            except Exception:
                connection.rollback()
                raise

    def authorize_generation(
        self,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, program_id)
                self._check_revision(head, expected_revision)
                record = self._require_generation(connection, program_id, generation_id)
                if record.campaign_id != campaign_id:
                    raise ValueError("Generation authorization Campaign mismatch.")
                if record.status == GenerationStatus.AUTHORIZED:
                    connection.rollback()
                    return record
                if record.status != GenerationStatus.PLANNED:
                    raise ValueError("Only a planned generation may become authorized.")
                connection.execute(
                    "UPDATE program_generations SET status = ?, updated_at = ? "
                    "WHERE program_id = ? AND generation_id = ?",
                    (
                        GenerationStatus.AUTHORIZED.value,
                        now.isoformat(),
                        program_id,
                        generation_id,
                    ),
                )
                self._set_program_state(
                    connection,
                    program_id,
                    ProgramState.GENERATION_AUTHORIZED,
                    now,
                )
                self._update_head(
                    connection,
                    head,
                    state=ProgramState.GENERATION_AUTHORIZED.value,
                    revision=int(head["revision"]) + 1,
                    updated_at=now,
                )
                self._append_event(
                    connection,
                    program_id=program_id,
                    generation_id=generation_id,
                    event_type=ProgramEventType.GENERATION_AUTHORIZED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={"campaign_id": campaign_id},
                    created_at=now,
                )
                connection.commit()
                return self.get_generation(program_id, generation_id)
            except Exception:
                connection.rollback()
                raise

    def start_generation(
        self,
        program_id: str,
        generation_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, program_id)
                self._check_revision(head, expected_revision)
                record = self._require_generation(connection, program_id, generation_id)
                if record.status != GenerationStatus.AUTHORIZED:
                    raise ValueError("Only an authorized generation may start.")
                connection.execute(
                    "UPDATE program_generations SET status = ?, updated_at = ? "
                    "WHERE program_id = ? AND generation_id = ?",
                    (
                        GenerationStatus.RUNNING.value,
                        now.isoformat(),
                        program_id,
                        generation_id,
                    ),
                )
                self._set_program_state(
                    connection, program_id, ProgramState.GENERATION_RUNNING, now
                )
                self._update_head(
                    connection,
                    head,
                    state=ProgramState.GENERATION_RUNNING.value,
                    active_generation_id=generation_id,
                    revision=int(head["revision"]) + 1,
                    updated_at=now,
                )
                self._append_event(
                    connection,
                    program_id=program_id,
                    generation_id=generation_id,
                    event_type=ProgramEventType.GENERATION_STARTED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={"plan_hash": record.plan.plan_hash},
                    created_at=now,
                )
                connection.commit()
                return self.get_generation(program_id, generation_id)
            except Exception:
                connection.rollback()
                raise

    def complete_generation(
        self,
        outcome: GenerationOutcome,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, outcome.program_id)
                self._check_revision(head, expected_revision)
                record = self._require_generation(
                    connection, outcome.program_id, outcome.generation_id
                )
                if record.status != GenerationStatus.RUNNING:
                    if record.outcome == outcome:
                        connection.rollback()
                        return record
                    raise ValueError("Only a running generation may complete.")
                if record.plan is None or (
                    outcome.plan_id != record.plan.plan_id
                    or outcome.plan_hash != record.plan.plan_hash
                    or outcome.generation_index != record.generation_index
                ):
                    raise ValueError("Generation outcome differs from authorized plan.")
                self._validate_generation_budget(record.plan, outcome)
                program = self._require_program(connection, outcome.program_id)
                total_pairs = int(head["total_pairs"]) + outcome.pair_count
                total_tokens = int(head["total_tokens"]) + outcome.total_tokens
                total_cost = float(head["total_cost_usd"]) + outcome.total_cost_usd
                if (
                    total_pairs > program.policy.budget.max_total_pairs
                    or total_tokens > program.policy.budget.max_total_tokens
                    or total_cost > program.policy.budget.max_total_cost_usd + 1e-12
                ):
                    raise ValueError("Program cumulative resource budget exceeded.")
                rollback_count = int(head["rollback_count"]) + int(
                    outcome.release_action == ReleaseDecisionAction.ROLLBACK
                )
                hold_count = int(head["hold_count"]) + int(
                    outcome.release_action == ReleaseDecisionAction.HOLD
                )
                status = self._status_for_outcome(outcome)
                connection.execute(
                    "UPDATE program_generations SET status = ?, outcome_json = ?, "
                    "updated_at = ? WHERE program_id = ? AND generation_id = ?",
                    (
                        status.value,
                        outcome.model_dump_json(),
                        now.isoformat(),
                        outcome.program_id,
                        outcome.generation_id,
                    ),
                )
                self._set_program_state(
                    connection, outcome.program_id, ProgramState.RUNNING, now
                )
                self._update_head(
                    connection,
                    head,
                    state=ProgramState.RUNNING.value,
                    current_generation_index=outcome.generation_index,
                    active_generation_id=outcome.generation_id,
                    rollback_count=rollback_count,
                    hold_count=hold_count,
                    total_pairs=total_pairs,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    revision=int(head["revision"]) + 1,
                    updated_at=now,
                )
                self._append_event(
                    connection,
                    program_id=outcome.program_id,
                    generation_id=outcome.generation_id,
                    event_type=ProgramEventType.GENERATION_COMPLETED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={
                        "outcome_hash": outcome.outcome_hash,
                        "release_action": outcome.release_action.value,
                        "release_package_hash": outcome.release_package_hash,
                    },
                    created_at=now,
                )
                connection.commit()
                return self.get_generation(outcome.program_id, outcome.generation_id)
            except Exception:
                connection.rollback()
                raise

    def store_decision(
        self,
        decision: ProgramDecision,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> tuple[ProgramDecision, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, decision.program_id)
                self._check_revision(head, expected_revision)
                generation = self._require_generation(
                    connection, decision.program_id, decision.generation_id
                )
                if generation.outcome is None or (
                    generation.outcome.outcome_hash != decision.source_outcome_hash
                    or generation.generation_index != decision.generation_index
                ):
                    raise ValueError("Program decision differs from generation outcome.")
                row = connection.execute(
                    "SELECT decision_json FROM program_decisions WHERE decision_id = ?",
                    (decision.decision_id,),
                ).fetchone()
                if row is not None:
                    existing = ProgramDecision.model_validate_json(row["decision_json"])
                    if existing != decision:
                        raise ProgramConflictError("Program decision ID has conflicting content.")
                    connection.rollback()
                    return existing, True
                connection.execute(
                    "INSERT INTO program_decisions "
                    "(decision_id, program_id, decision_hash, decision_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        decision.decision_id,
                        decision.program_id,
                        decision.decision_hash,
                        decision.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                state, terminal_event = self._state_for_action(decision.action)
                self._set_program_state(connection, decision.program_id, state, now)
                self._update_head(
                    connection,
                    head,
                    state=state.value,
                    last_decision_id=decision.decision_id,
                    revision=int(head["revision"]) + 1,
                    updated_at=now,
                )
                self._append_event(
                    connection,
                    program_id=decision.program_id,
                    generation_id=decision.generation_id,
                    event_type=ProgramEventType.DECISION_STORED,
                    actor_id=actor_id,
                    reason=decision.reason,
                    payload={
                        "decision_id": decision.decision_id,
                        "decision_hash": decision.decision_hash,
                        "action": decision.action.value,
                    },
                    created_at=now,
                )
                if terminal_event is not None:
                    self._append_event(
                        connection,
                        program_id=decision.program_id,
                        generation_id=decision.generation_id,
                        event_type=terminal_event,
                        actor_id=actor_id,
                        reason=decision.reason,
                        payload={"decision_hash": decision.decision_hash},
                        created_at=now,
                    )
                connection.commit()
                return decision, False
            except Exception:
                connection.rollback()
                raise

    def get_program(self, program_id: str) -> ProgramRecord:
        with self._connection() as connection:
            return self._require_program(connection, program_id)

    def head(self, program_id: str) -> ProgramHead:
        with self._connection() as connection:
            return self._row_to_head(self._require_head(connection, program_id))

    def get_generation(self, program_id: str, generation_id: str) -> GenerationRecord:
        with self._connection() as connection:
            return self._require_generation(connection, program_id, generation_id)

    def list_generations(self, program_id: str) -> list[GenerationRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM program_generations WHERE program_id = ? "
                "ORDER BY generation_index",
                (program_id,),
            ).fetchall()
            return [self._row_to_generation(row) for row in rows]

    def list_signals(self, program_id: str) -> list[ProgramLearningSignal]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT signal_json FROM program_signals WHERE program_id = ? "
                "ORDER BY created_at, signal_id",
                (program_id,),
            ).fetchall()
            return [ProgramLearningSignal.model_validate_json(row["signal_json"]) for row in rows]

    def list_attributions(self, program_id: str) -> list[AttributionReceipt]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT receipt_json FROM program_attributions WHERE program_id = ? "
                "ORDER BY created_at, receipt_id",
                (program_id,),
            ).fetchall()
            return [AttributionReceipt.model_validate_json(row["receipt_json"]) for row in rows]

    def list_decisions(self, program_id: str) -> list[ProgramDecision]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT decision_json FROM program_decisions WHERE program_id = ? "
                "ORDER BY created_at, decision_id",
                (program_id,),
            ).fetchall()
            return [ProgramDecision.model_validate_json(row["decision_json"]) for row in rows]

    def events(self) -> list[ProgramAuditEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM program_audit_events ORDER BY sequence"
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> ProgramCheckpoint:
        events = self.events()
        return ProgramCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(self, checkpoint: ProgramCheckpoint | None = None) -> bool:
        previous_hash = _GENESIS_HASH
        events = self.events()
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise ProgramAuditIntegrityError("Program audit sequence or chain is broken.")
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                program_id=event.program_id,
                generation_id=event.generation_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                reason=event.reason,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise ProgramAuditIntegrityError("Program audit event content was modified.")
            previous_hash = event.event_hash
        current = ProgramCheckpoint(event_count=len(events), head_hash=previous_hash)
        if checkpoint is not None and current != checkpoint:
            raise ProgramAuditIntegrityError(
                "Program audit does not match the external checkpoint."
            )
        return True

    def verify_state(self, program_id: str) -> bool:
        program = self.get_program(program_id)
        head = self.head(program_id)
        generations = self.list_generations(program_id)
        if not generations or [item.generation_index for item in generations] != list(
            range(len(generations))
        ):
            raise ProgramConflictError("Program generations are not contiguous from zero.")
        active = next(
            (item for item in generations if item.generation_id == head.active_generation_id),
            None,
        )
        if active is None or active.generation_index != head.current_generation_index:
            raise ProgramConflictError("Program head differs from active generation record.")
        if program.state != head.state:
            raise ProgramConflictError("Program record and head states differ.")
        outcomes = [item.outcome for item in generations if item.outcome is not None]
        if (
            head.rollback_count
            != sum(item.release_action == ReleaseDecisionAction.ROLLBACK for item in outcomes)
            or head.hold_count
            != sum(item.release_action == ReleaseDecisionAction.HOLD for item in outcomes)
            or head.total_pairs != sum(item.pair_count for item in outcomes)
            or head.total_tokens != sum(item.total_tokens for item in outcomes)
            or abs(head.total_cost_usd - sum(item.total_cost_usd for item in outcomes)) > 1e-12
        ):
            raise ProgramConflictError("Program head counters differ from generation outcomes.")
        decisions = self.list_decisions(program_id)
        if head.last_decision_id != (decisions[-1].decision_id if decisions else None):
            raise ProgramConflictError("Program head differs from persisted decisions.")
        self.verify_audit()
        return True

    @staticmethod
    def _status_for_outcome(outcome: GenerationOutcome) -> GenerationStatus:
        return {
            ReleaseDecisionAction.READY: GenerationStatus.COMPLETED,
            ReleaseDecisionAction.ROLLBACK: GenerationStatus.ROLLED_BACK,
            ReleaseDecisionAction.HOLD: GenerationStatus.HELD,
        }[outcome.release_action]

    @staticmethod
    def _validate_generation_budget(plan: GenerationPlan, outcome: GenerationOutcome) -> None:
        if (
            outcome.pair_count > plan.budget.max_pairs
            or outcome.total_tokens > plan.budget.max_tokens
            or outcome.total_cost_usd > plan.budget.max_cost_usd + 1e-12
        ):
            raise ValueError("Generation outcome exceeds its authorized budget.")

    @staticmethod
    def _state_for_action(
        action: ProgramAction,
    ) -> tuple[ProgramState, ProgramEventType | None]:
        return {
            ProgramAction.CONTINUE: (ProgramState.RUNNING, None),
            ProgramAction.STOP_SUCCESS: (
                ProgramState.COMPLETED,
                ProgramEventType.PROGRAM_COMPLETED,
            ),
            ProgramAction.STOP_BUDGET: (
                ProgramState.BUDGET_EXHAUSTED,
                ProgramEventType.PROGRAM_BUDGET_EXHAUSTED,
            ),
            ProgramAction.PAUSE: (
                ProgramState.PAUSED,
                ProgramEventType.PROGRAM_PAUSED,
            ),
            ProgramAction.ESCALATE: (
                ProgramState.ESCALATED,
                ProgramEventType.PROGRAM_ESCALATED,
            ),
            ProgramAction.FAIL: (
                ProgramState.FAILED,
                ProgramEventType.PROGRAM_FAILED,
            ),
        }[action]

    def _insert_generation(
        self, connection: sqlite3.Connection, record: GenerationRecord
    ) -> None:
        connection.execute(
            "INSERT INTO program_generations "
            "(program_id, generation_id, generation_index, parent_generation_id, status, "
            "plan_json, outcome_json, campaign_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.program_id,
                record.generation_id,
                record.generation_index,
                record.parent_generation_id,
                record.status.value,
                record.plan.model_dump_json() if record.plan else None,
                record.outcome.model_dump_json() if record.outcome else None,
                record.campaign_id,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
            ),
        )

    @staticmethod
    def _generation_row(
        connection: sqlite3.Connection, program_id: str, generation_id: str
    ):
        return connection.execute(
            "SELECT * FROM program_generations WHERE program_id = ? AND generation_id = ?",
            (program_id, generation_id),
        ).fetchone()

    def _require_generation(
        self, connection: sqlite3.Connection, program_id: str, generation_id: str
    ) -> GenerationRecord:
        row = self._generation_row(connection, program_id, generation_id)
        if row is None:
            raise KeyError(f"Unknown Program generation: {program_id}/{generation_id}")
        return self._row_to_generation(row)

    @staticmethod
    def _row_to_generation(row: sqlite3.Row) -> GenerationRecord:
        return GenerationRecord(
            program_id=row["program_id"],
            generation_id=row["generation_id"],
            generation_index=int(row["generation_index"]),
            parent_generation_id=row["parent_generation_id"],
            status=GenerationStatus(row["status"]),
            plan=(
                GenerationPlan.model_validate_json(row["plan_json"])
                if row["plan_json"]
                else None
            ),
            outcome=(
                GenerationOutcome.model_validate_json(row["outcome_json"])
                if row["outcome_json"]
                else None
            ),
            campaign_id=row["campaign_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_program(row: sqlite3.Row) -> ProgramRecord:
        return ProgramRecord(
            program_id=row["program_id"],
            policy=EvolutionProgramPolicy.model_validate_json(row["policy_json"]),
            state=ProgramState(row["state"]),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _require_program(
        self, connection: sqlite3.Connection, program_id: str
    ) -> ProgramRecord:
        row = connection.execute(
            "SELECT * FROM evolution_programs WHERE program_id = ?", (program_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Evolution Program: {program_id}")
        return self._row_to_program(row)

    @staticmethod
    def _row_to_head(row: sqlite3.Row) -> ProgramHead:
        return ProgramHead(
            program_id=row["program_id"],
            state=ProgramState(row["state"]),
            current_generation_index=int(row["current_generation_index"]),
            active_generation_id=row["active_generation_id"],
            revision=int(row["revision"]),
            rollback_count=int(row["rollback_count"]),
            hold_count=int(row["hold_count"]),
            generation_campaign_count=int(row["generation_campaign_count"]),
            total_pairs=int(row["total_pairs"]),
            total_tokens=int(row["total_tokens"]),
            total_cost_usd=float(row["total_cost_usd"]),
            last_decision_id=row["last_decision_id"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _require_head(connection: sqlite3.Connection, program_id: str):
        row = connection.execute(
            "SELECT * FROM program_heads WHERE program_id = ?", (program_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Program head: {program_id}")
        return row

    def _require_signal(
        self, connection: sqlite3.Connection, signal_id: str
    ) -> ProgramLearningSignal:
        row = connection.execute(
            "SELECT signal_json FROM program_signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Program learning signal: {signal_id}")
        return ProgramLearningSignal.model_validate_json(row["signal_json"])

    @staticmethod
    def _require_attribution(
        connection: sqlite3.Connection, receipt_id: str
    ) -> AttributionReceipt:
        row = connection.execute(
            "SELECT receipt_json FROM program_attributions WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Program attribution: {receipt_id}")
        return AttributionReceipt.model_validate_json(row["receipt_json"])

    @staticmethod
    def _check_revision(head: sqlite3.Row, expected_revision: int) -> None:
        if int(head["revision"]) != expected_revision:
            raise StaleProgramRevision(
                f"Expected Program revision {expected_revision}, found {head['revision']}."
            )

    @staticmethod
    def _set_program_state(
        connection: sqlite3.Connection,
        program_id: str,
        state: ProgramState,
        now: datetime,
    ) -> None:
        connection.execute(
            "UPDATE evolution_programs SET state = ?, updated_at = ? WHERE program_id = ?",
            (state.value, now.isoformat(), program_id),
        )

    @staticmethod
    def _update_head(
        connection: sqlite3.Connection,
        head: sqlite3.Row,
        **updates: Any,
    ) -> None:
        values = {
            "state": head["state"],
            "current_generation_index": head["current_generation_index"],
            "active_generation_id": head["active_generation_id"],
            "revision": head["revision"],
            "rollback_count": head["rollback_count"],
            "hold_count": head["hold_count"],
            "generation_campaign_count": head["generation_campaign_count"],
            "total_pairs": head["total_pairs"],
            "total_tokens": head["total_tokens"],
            "total_cost_usd": head["total_cost_usd"],
            "last_decision_id": head["last_decision_id"],
            "updated_at": datetime.fromisoformat(head["updated_at"]),
        }
        values.update(updates)
        connection.execute(
            "UPDATE program_heads SET state = ?, current_generation_index = ?, "
            "active_generation_id = ?, revision = ?, rollback_count = ?, hold_count = ?, "
            "generation_campaign_count = ?, total_pairs = ?, total_tokens = ?, "
            "total_cost_usd = ?, last_decision_id = ?, updated_at = ? WHERE program_id = ?",
            (
                values["state"],
                values["current_generation_index"],
                values["active_generation_id"],
                values["revision"],
                values["rollback_count"],
                values["hold_count"],
                values["generation_campaign_count"],
                values["total_pairs"],
                values["total_tokens"],
                values["total_cost_usd"],
                values["last_decision_id"],
                values["updated_at"].isoformat(),
                head["program_id"],
            ),
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        program_id: str,
        generation_id: str | None,
        event_type: ProgramEventType,
        actor_id: str,
        reason: str,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM program_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"program-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            program_id=program_id,
            generation_id=generation_id,
            event_type=event_type,
            actor_id=actor_id,
            reason=reason,
            payload=payload,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO program_audit_events "
            "(sequence, event_id, program_id, generation_id, event_type, actor_id, "
            "reason, payload_json, created_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                program_id,
                generation_id,
                event_type.value,
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
        program_id: str,
        generation_id: str | None,
        event_type: ProgramEventType,
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
                "program_id": program_id,
                "generation_id": generation_id,
                "event_type": event_type.value,
                "actor_id": actor_id,
                "reason": reason,
                "payload": payload,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ProgramAuditEvent:
        return ProgramAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            program_id=row["program_id"],
            generation_id=row["generation_id"],
            event_type=ProgramEventType(row["event_type"]),
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
    "ProgramAuditIntegrityError",
    "ProgramConflictError",
    "SQLiteEvolutionProgramRepository",
    "StaleProgramRevision",
]
