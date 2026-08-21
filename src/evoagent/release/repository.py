from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.model_registry.models import canonical_sha256
from evoagent.release.models import (
    ReleaseAuditEvent,
    ReleaseDecisionAction,
    ReleaseEvidenceBatch,
    ReleaseEventType,
    ReleaseHead,
    ReleasePlan,
    ReleaseRegistryCheckpoint,
    ReleaseStageAssessment,
    ReleaseStageDecision,
    ReleaseStageKind,
    ReleaseState,
)


_GENESIS_HASH = "0" * 64


class ReleaseRegistryConflictError(RuntimeError):
    pass


class StaleReleaseRevision(RuntimeError):
    pass


class ReleaseAuditIntegrityError(RuntimeError):
    pass


class SQLiteReleaseRegistry:
    """Immutable release evidence plus one local, revisioned control-plane head."""

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
                CREATE TABLE IF NOT EXISTS release_plans (
                    plan_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL UNIQUE,
                    plan_hash TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS release_batches (
                    batch_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    batch_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS release_assessments (
                    assessment_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    assessment_hash TEXT NOT NULL,
                    assessment_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS release_decisions (
                    decision_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    decision_hash TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS release_heads (
                    plan_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    incumbent_snapshot_id TEXT NOT NULL,
                    challenger_snapshot_id TEXT NOT NULL,
                    primary_snapshot_id TEXT NOT NULL,
                    active_stage_id TEXT,
                    candidate_allocation_percent REAL NOT NULL,
                    revision INTEGER NOT NULL,
                    release_campaign_id TEXT,
                    rollback_campaign_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS release_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    stage_id TEXT,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def register_plan(
        self,
        plan: ReleasePlan,
        *,
        actor_id: str = "release-planner",
        now: datetime | None = None,
    ) -> tuple[ReleasePlan, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT plan_json FROM release_plans WHERE plan_id = ?",
                    (plan.plan_id,),
                ).fetchone()
                if row is not None:
                    existing = ReleasePlan.model_validate_json(row["plan_json"])
                    if existing != plan:
                        raise ReleaseRegistryConflictError(
                            "Conflicting release plan under the same plan ID."
                        )
                    connection.commit()
                    return existing, True
                family = connection.execute(
                    "SELECT plan_id FROM release_plans WHERE family_id = ?",
                    (plan.family_id,),
                ).fetchone()
                if family is not None:
                    raise ReleaseRegistryConflictError(
                        "Release family already has another active plan."
                    )
                connection.execute(
                    "INSERT INTO release_plans (plan_id, family_id, plan_hash, plan_json, "
                    "created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        plan.plan_id,
                        plan.family_id,
                        plan.plan_hash,
                        plan.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO release_heads (plan_id, family_id, state, "
                    "incumbent_snapshot_id, challenger_snapshot_id, primary_snapshot_id, "
                    "active_stage_id, candidate_allocation_percent, revision, "
                    "release_campaign_id, rollback_campaign_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 0, NULL, NULL, ?)",
                    (
                        plan.plan_id,
                        plan.family_id,
                        ReleaseState.PLANNED.value,
                        plan.incumbent_snapshot_id,
                        plan.challenger_snapshot_id,
                        plan.incumbent_snapshot_id,
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ReleaseEventType.PLAN_REGISTERED,
                    plan=plan,
                    stage_id=None,
                    reason="Immutable shadow and Canary release plan registered.",
                    payload={"plan_hash": plan.plan_hash},
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
                return plan, False
            except Exception:
                connection.rollback()
                raise

    def store_batch(
        self,
        batch: ReleaseEvidenceBatch,
        *,
        actor_id: str = "release-evidence-importer",
        now: datetime | None = None,
    ) -> tuple[ReleaseEvidenceBatch, bool]:
        now = now or datetime.now(timezone.utc)
        plan = self.get_plan(batch.plan_id)
        if batch.plan_hash != plan.plan_hash:
            raise ReleaseRegistryConflictError("Release batch plan hash differs.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT batch_json FROM release_batches WHERE batch_id = ?",
                    (batch.batch_id,),
                ).fetchone()
                if row is not None:
                    existing = ReleaseEvidenceBatch.model_validate_json(row["batch_json"])
                    if existing != batch:
                        raise ReleaseRegistryConflictError(
                            "Conflicting release evidence under the same batch ID."
                        )
                    connection.commit()
                    return existing, True
                connection.execute(
                    "INSERT INTO release_batches (batch_id, plan_id, evidence_hash, "
                    "batch_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        batch.batch_id,
                        batch.plan_id,
                        batch.evidence_hash,
                        batch.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ReleaseEventType.EVIDENCE_IMPORTED,
                    plan=plan,
                    stage_id=batch.stage_id,
                    reason="Caller-hashed observable release evidence imported.",
                    payload={
                        "batch_id": batch.batch_id,
                        "evidence_hash": batch.evidence_hash,
                        "source_file_sha256": batch.source_file_sha256,
                    },
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
                return batch, False
            except Exception:
                connection.rollback()
                raise

    def store_assessment(
        self,
        assessment: ReleaseStageAssessment,
        *,
        actor_id: str = "release-stage-gate",
        now: datetime | None = None,
    ) -> tuple[ReleaseStageAssessment, bool]:
        now = now or datetime.now(timezone.utc)
        plan = self.get_plan(assessment.plan_id)
        if assessment.plan_hash != plan.plan_hash:
            raise ReleaseRegistryConflictError("Release assessment plan hash differs.")
        batch = self.get_batch(assessment.batch_id)
        if batch.evidence_hash != assessment.batch_hash:
            raise ReleaseRegistryConflictError("Release assessment batch hash differs.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT assessment_json FROM release_assessments WHERE assessment_id = ?",
                    (assessment.assessment_id,),
                ).fetchone()
                if row is not None:
                    existing = ReleaseStageAssessment.model_validate_json(
                        row["assessment_json"]
                    )
                    if existing != assessment:
                        raise ReleaseRegistryConflictError(
                            "Conflicting release assessment under the same ID."
                        )
                    connection.commit()
                    return existing, True
                connection.execute(
                    "INSERT INTO release_assessments (assessment_id, plan_id, "
                    "assessment_hash, assessment_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        assessment.assessment_id,
                        assessment.plan_id,
                        assessment.assessment_hash,
                        assessment.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ReleaseEventType.STAGE_ASSESSED,
                    plan=plan,
                    stage_id=assessment.stage_id,
                    reason="Frozen release stage assessment stored.",
                    payload={
                        "assessment_id": assessment.assessment_id,
                        "assessment_hash": assessment.assessment_hash,
                        "status": assessment.status.value,
                    },
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
                return assessment, False
            except Exception:
                connection.rollback()
                raise

    def store_decision(
        self,
        decision: ReleaseStageDecision,
        *,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[ReleaseStageDecision, bool]:
        now = now or datetime.now(timezone.utc)
        plan = self.get_plan(decision.plan_id)
        assessment = self.get_assessment_by_hash(decision.assessment_hash)
        if decision.plan_hash != plan.plan_hash or assessment.stage_id != decision.stage_id:
            raise ReleaseRegistryConflictError("Release decision binding differs.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT decision_json FROM release_decisions WHERE decision_id = ?",
                    (decision.decision_id,),
                ).fetchone()
                if row is not None:
                    existing = ReleaseStageDecision.model_validate_json(row["decision_json"])
                    if existing != decision:
                        raise ReleaseRegistryConflictError(
                            "Conflicting release decision under the same ID."
                        )
                    connection.commit()
                    return existing, True
                connection.execute(
                    "INSERT INTO release_decisions (decision_id, plan_id, decision_hash, "
                    "decision_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        decision.decision_id,
                        decision.plan_id,
                        decision.decision_hash,
                        decision.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ReleaseEventType.DECISION_STORED,
                    plan=plan,
                    stage_id=decision.stage_id,
                    reason=decision.reason,
                    payload={
                        "decision_id": decision.decision_id,
                        "decision_hash": decision.decision_hash,
                        "action": decision.action.value,
                    },
                    actor_id=actor_id or decision.decision_actor_id,
                    created_at=now,
                )
                connection.commit()
                return decision, False
            except Exception:
                connection.rollback()
                raise

    def bind_release_campaign(
        self,
        plan_id: str,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        return self._transition_head(
            plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.PLANNED},
            state=ReleaseState.PLANNED,
            release_campaign_id=campaign_id,
            event_type=ReleaseEventType.RELEASE_CAMPAIGN_BOUND,
            reason="High-risk Champion release Campaign bound to the plan.",
            actor_id=actor_id,
            now=now,
        )

    def mark_authorized(
        self,
        plan_id: str,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        head = self.head(plan_id)
        if head.release_campaign_id != campaign_id:
            raise ReleaseRegistryConflictError("Release authorization Campaign differs.")
        return self._transition_head(
            plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.PLANNED},
            state=ReleaseState.AUTHORIZED,
            event_type=ReleaseEventType.RELEASE_AUTHORIZED,
            reason="Exact release Campaign authorization synchronized locally.",
            actor_id=actor_id,
            now=now,
        )

    def start_shadow(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        plan = self.get_plan(plan_id)
        stage = plan.stages[0]
        if stage.kind != ReleaseStageKind.SHADOW:
            raise ReleaseRegistryConflictError("Release plan does not begin with shadow.")
        return self._transition_head(
            plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.AUTHORIZED},
            state=ReleaseState.SHADOW,
            active_stage_id=stage.stage_id,
            candidate_allocation_percent=0.0,
            event_type=ReleaseEventType.STAGE_ACTIVATED,
            reason="Shadow stage activated in the local control plane only.",
            actor_id=actor_id,
            stage_id=stage.stage_id,
            now=now,
        )

    def advance(
        self,
        decision: ReleaseStageDecision,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        if decision.action != ReleaseDecisionAction.ADVANCE:
            raise ReleaseRegistryConflictError("Only an advance decision may advance a stage.")
        plan = self.get_plan(decision.plan_id)
        head = self.head(plan.plan_id)
        if head.active_stage_id != decision.stage_id:
            raise ReleaseRegistryConflictError("Release advance decision is stale.")
        next_stage = next(
            (item for item in plan.stages if item.stage_id == decision.next_stage_id),
            None,
        )
        if next_stage is None or next_stage.kind != ReleaseStageKind.CANARY:
            raise ReleaseRegistryConflictError("Release advance target is not a Canary stage.")
        current_index = next(
            item.stage_index for item in plan.stages if item.stage_id == decision.stage_id
        )
        if next_stage.stage_index != current_index + 1:
            raise ReleaseRegistryConflictError("Release stages cannot be skipped.")
        return self._transition_head(
            plan.plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.SHADOW, ReleaseState.CANARY, ReleaseState.HOLD},
            state=ReleaseState.CANARY,
            active_stage_id=next_stage.stage_id,
            candidate_allocation_percent=next_stage.candidate_traffic_percent,
            event_type=ReleaseEventType.STAGE_ADVANCED,
            reason=decision.reason,
            actor_id=actor_id,
            stage_id=next_stage.stage_id,
            payload={"decision_hash": decision.decision_hash},
            now=now,
        )

    def record_hold(
        self,
        decision: ReleaseStageDecision,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        if decision.action != ReleaseDecisionAction.HOLD:
            raise ReleaseRegistryConflictError("Only a hold decision may record hold.")
        head = self.head(decision.plan_id)
        if head.active_stage_id != decision.stage_id:
            raise ReleaseRegistryConflictError("Release hold decision is stale.")
        return self._transition_head(
            decision.plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.SHADOW, ReleaseState.CANARY},
            state=ReleaseState.HOLD,
            event_type=ReleaseEventType.HOLD_RECORDED,
            reason=decision.reason,
            actor_id=actor_id,
            stage_id=decision.stage_id,
            payload={"decision_hash": decision.decision_hash},
            now=now,
        )

    def recommend_rollback(
        self,
        decision: ReleaseStageDecision,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        if decision.action != ReleaseDecisionAction.ROLLBACK:
            raise ReleaseRegistryConflictError("Only a rollback decision may recommend rollback.")
        head = self.head(decision.plan_id)
        if head.active_stage_id != decision.stage_id:
            raise ReleaseRegistryConflictError("Release rollback decision is stale.")
        return self._transition_head(
            decision.plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.SHADOW, ReleaseState.CANARY, ReleaseState.HOLD},
            state=ReleaseState.ROLLBACK_RECOMMENDED,
            event_type=ReleaseEventType.ROLLBACK_RECOMMENDED,
            reason=decision.reason,
            actor_id=actor_id,
            stage_id=decision.stage_id,
            payload={"decision_hash": decision.decision_hash},
            now=now,
        )

    def bind_rollback_campaign(
        self,
        plan_id: str,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        return self._transition_head(
            plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.ROLLBACK_RECOMMENDED},
            state=ReleaseState.ROLLBACK_RECOMMENDED,
            rollback_campaign_id=campaign_id,
            event_type=ReleaseEventType.ROLLBACK_CAMPAIGN_BOUND,
            reason="High-risk Champion rollback Campaign bound locally.",
            actor_id=actor_id,
            now=now,
        )

    def mark_ready(
        self,
        decision: ReleaseStageDecision,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        if decision.action != ReleaseDecisionAction.READY:
            raise ReleaseRegistryConflictError("Only a ready decision may mark readiness.")
        plan = self.get_plan(decision.plan_id)
        head = self.head(plan.plan_id)
        if head.active_stage_id != decision.stage_id or decision.stage_id != plan.stages[-1].stage_id:
            raise ReleaseRegistryConflictError("Release readiness decision is not final-stage evidence.")
        return self._transition_head(
            plan.plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.CANARY},
            state=ReleaseState.READY,
            event_type=ReleaseEventType.READY_RECORDED,
            reason=decision.reason,
            actor_id=actor_id,
            stage_id=decision.stage_id,
            payload={"decision_hash": decision.decision_hash},
            now=now,
        )

    def rollback(
        self,
        decision: ReleaseStageDecision,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> ReleaseHead:
        head = self.head(decision.plan_id)
        if (
            decision.action != ReleaseDecisionAction.ROLLBACK
            or head.state != ReleaseState.ROLLBACK_RECOMMENDED
            or head.rollback_campaign_id != campaign_id
        ):
            raise ReleaseRegistryConflictError("Release rollback evidence or Campaign differs.")
        return self._transition_head(
            decision.plan_id,
            expected_revision=expected_revision,
            allowed_states={ReleaseState.ROLLBACK_RECOMMENDED},
            state=ReleaseState.ROLLED_BACK,
            active_stage_id=None,
            candidate_allocation_percent=0.0,
            event_type=ReleaseEventType.ROLLED_BACK,
            reason="Explicit local control-plane rollback restored the incumbent allocation.",
            actor_id=actor_id,
            payload={
                "decision_hash": decision.decision_hash,
                "rollback_campaign_id": campaign_id,
            },
            now=now,
        )

    def get_plan(self, plan_id: str) -> ReleasePlan:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT plan_json FROM release_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown release plan: {plan_id}")
            return ReleasePlan.model_validate_json(row["plan_json"])

    def get_batch(self, batch_id: str) -> ReleaseEvidenceBatch:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT batch_json FROM release_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown release batch: {batch_id}")
            return ReleaseEvidenceBatch.model_validate_json(row["batch_json"])

    def get_assessment(self, assessment_id: str) -> ReleaseStageAssessment:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT assessment_json FROM release_assessments WHERE assessment_id = ?",
                (assessment_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown release assessment: {assessment_id}")
            return ReleaseStageAssessment.model_validate_json(row["assessment_json"])

    def get_assessment_by_hash(self, assessment_hash: str) -> ReleaseStageAssessment:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT assessment_json FROM release_assessments WHERE assessment_hash = ?",
                (assessment_hash,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown release assessment hash: {assessment_hash}")
            return ReleaseStageAssessment.model_validate_json(row["assessment_json"])

    def get_decision(self, decision_id: str) -> ReleaseStageDecision:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT decision_json FROM release_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown release decision: {decision_id}")
            return ReleaseStageDecision.model_validate_json(row["decision_json"])

    def list_batches(self, plan_id: str) -> list[ReleaseEvidenceBatch]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT batch_json FROM release_batches WHERE plan_id = ? ORDER BY batch_id",
                (plan_id,),
            ).fetchall()
            return [ReleaseEvidenceBatch.model_validate_json(row["batch_json"]) for row in rows]

    def list_assessments(self, plan_id: str) -> list[ReleaseStageAssessment]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT assessment_json FROM release_assessments WHERE plan_id = ? "
                "ORDER BY assessment_id",
                (plan_id,),
            ).fetchall()
            return [
                ReleaseStageAssessment.model_validate_json(row["assessment_json"])
                for row in rows
            ]

    def list_decisions(self, plan_id: str) -> list[ReleaseStageDecision]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT decision_json FROM release_decisions WHERE plan_id = ? "
                "ORDER BY decision_id",
                (plan_id,),
            ).fetchall()
            return [
                ReleaseStageDecision.model_validate_json(row["decision_json"])
                for row in rows
            ]

    def head(self, plan_id: str) -> ReleaseHead:
        with self._connection() as connection:
            row = self._require_head(connection, plan_id)
            return self._row_to_head(row)

    def events(self) -> list[ReleaseAuditEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM release_audit_events ORDER BY sequence"
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> ReleaseRegistryCheckpoint:
        events = self.events()
        return ReleaseRegistryCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_audit(self, checkpoint: ReleaseRegistryCheckpoint | None = None) -> bool:
        events = self.events()
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise ReleaseAuditIntegrityError("Release audit sequence or chain is broken.")
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                plan_id=event.plan_id,
                family_id=event.family_id,
                stage_id=event.stage_id,
                reason=event.reason,
                payload=event.payload,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise ReleaseAuditIntegrityError("Release audit event content was modified.")
            previous_hash = event.event_hash
        current = ReleaseRegistryCheckpoint(event_count=len(events), head_hash=previous_hash)
        if checkpoint is not None and current != checkpoint:
            raise ReleaseAuditIntegrityError("Release audit differs from its checkpoint.")
        return True

    def verify_state(self) -> bool:
        with self._connection() as connection:
            heads = connection.execute("SELECT * FROM release_heads").fetchall()
            for row in heads:
                head = self._row_to_head(row)
                plan = self.get_plan(head.plan_id)
                if (
                    head.family_id != plan.family_id
                    or head.incumbent_snapshot_id != plan.incumbent_snapshot_id
                    or head.challenger_snapshot_id != plan.challenger_snapshot_id
                ):
                    raise ReleaseRegistryConflictError("Release head differs from its plan.")
                if head.active_stage_id is not None and head.active_stage_id not in {
                    item.stage_id for item in plan.stages
                }:
                    raise ReleaseRegistryConflictError("Release head stage is not in its plan.")
        self.verify_audit()
        return True

    def _transition_head(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        allowed_states: set[ReleaseState],
        state: ReleaseState,
        event_type: ReleaseEventType,
        reason: str,
        actor_id: str,
        active_stage_id: str | None | object = ...,
        candidate_allocation_percent: float | object = ...,
        release_campaign_id: str | None | object = ...,
        rollback_campaign_id: str | None | object = ...,
        stage_id: str | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ReleaseHead:
        now = now or datetime.now(timezone.utc)
        plan = self.get_plan(plan_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._require_head(connection, plan_id)
                current = self._row_to_head(row)
                if current.revision != expected_revision:
                    raise StaleReleaseRevision(
                        f"Expected release revision {expected_revision}, found {current.revision}."
                    )
                if current.state not in allowed_states:
                    raise ReleaseRegistryConflictError(
                        f"Release state {current.state.value} cannot perform this transition."
                    )
                next_stage = (
                    current.active_stage_id if active_stage_id is ... else active_stage_id
                )
                next_allocation = (
                    current.candidate_allocation_percent
                    if candidate_allocation_percent is ...
                    else candidate_allocation_percent
                )
                next_release_campaign = (
                    current.release_campaign_id
                    if release_campaign_id is ...
                    else release_campaign_id
                )
                next_rollback_campaign = (
                    current.rollback_campaign_id
                    if rollback_campaign_id is ...
                    else rollback_campaign_id
                )
                candidate = ReleaseHead(
                    plan_id=plan_id,
                    family_id=plan.family_id,
                    state=state,
                    incumbent_snapshot_id=plan.incumbent_snapshot_id,
                    challenger_snapshot_id=plan.challenger_snapshot_id,
                    primary_snapshot_id=plan.incumbent_snapshot_id,
                    active_stage_id=next_stage,
                    candidate_allocation_percent=next_allocation,
                    revision=current.revision + 1,
                    release_campaign_id=next_release_campaign,
                    rollback_campaign_id=next_rollback_campaign,
                    updated_at=now,
                )
                changed = connection.execute(
                    "UPDATE release_heads SET state = ?, active_stage_id = ?, "
                    "candidate_allocation_percent = ?, revision = ?, release_campaign_id = ?, "
                    "rollback_campaign_id = ?, updated_at = ? WHERE plan_id = ? AND revision = ?",
                    (
                        candidate.state.value,
                        candidate.active_stage_id,
                        candidate.candidate_allocation_percent,
                        candidate.revision,
                        candidate.release_campaign_id,
                        candidate.rollback_campaign_id,
                        candidate.updated_at.isoformat(),
                        plan_id,
                        expected_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleReleaseRevision("Release head changed concurrently.")
                self._append_event(
                    connection,
                    event_type=event_type,
                    plan=plan,
                    stage_id=stage_id,
                    reason=reason,
                    payload={
                        "from_state": current.state.value,
                        "to_state": candidate.state.value,
                        "from_revision": current.revision,
                        "to_revision": candidate.revision,
                        "candidate_allocation_percent": candidate.candidate_allocation_percent,
                        **(payload or {}),
                    },
                    actor_id=actor_id,
                    created_at=now,
                )
                connection.commit()
                return candidate
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _require_head(connection: sqlite3.Connection, plan_id: str):
        row = connection.execute(
            "SELECT * FROM release_heads WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown release head: {plan_id}")
        return row

    @staticmethod
    def _row_to_head(row: sqlite3.Row) -> ReleaseHead:
        return ReleaseHead(
            plan_id=row["plan_id"],
            family_id=row["family_id"],
            state=ReleaseState(row["state"]),
            incumbent_snapshot_id=row["incumbent_snapshot_id"],
            challenger_snapshot_id=row["challenger_snapshot_id"],
            primary_snapshot_id=row["primary_snapshot_id"],
            active_stage_id=row["active_stage_id"],
            candidate_allocation_percent=float(row["candidate_allocation_percent"]),
            revision=int(row["revision"]),
            release_campaign_id=row["release_campaign_id"],
            rollback_campaign_id=row["rollback_campaign_id"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: ReleaseEventType,
        plan: ReleasePlan,
        stage_id: str | None,
        reason: str,
        payload: dict[str, Any],
        actor_id: str,
        created_at: datetime,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM release_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"release-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            plan_id=plan.plan_id,
            family_id=plan.family_id,
            stage_id=stage_id,
            reason=reason,
            payload=payload,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO release_audit_events (sequence, event_id, event_type, plan_id, "
            "family_id, stage_id, reason, payload_json, actor_id, created_at, "
            "previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type.value,
                plan.plan_id,
                plan.family_id,
                stage_id,
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
        event_type: ReleaseEventType,
        plan_id: str,
        family_id: str,
        stage_id: str | None,
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
                "plan_id": plan_id,
                "family_id": family_id,
                "stage_id": stage_id,
                "reason": reason,
                "payload": payload,
                "actor_id": actor_id,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ReleaseAuditEvent:
        return ReleaseAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            event_type=ReleaseEventType(row["event_type"]),
            plan_id=row["plan_id"],
            family_id=row["family_id"],
            stage_id=row["stage_id"],
            reason=row["reason"],
            payload=json.loads(row["payload_json"]),
            actor_id=row["actor_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "ReleaseAuditIntegrityError",
    "ReleaseRegistryConflictError",
    "SQLiteReleaseRegistry",
    "StaleReleaseRevision",
]