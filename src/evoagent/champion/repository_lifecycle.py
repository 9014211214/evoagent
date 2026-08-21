from __future__ import annotations

from datetime import datetime, timezone

from evoagent.benchmark_evidence.models import BenchmarkRunEvidence
from evoagent.champion.models import (
    ChampionDecisionAction,
    ChampionEventType,
    ChampionSelectionDecision,
    ChampionSnapshotRecord,
    ChampionVersionStatus,
)


class ChampionRegistryLifecycleMixin:
    """State-changing Champion operations mixed into SQLiteChampionRegistry."""

    def register_initial(
        self,
        run: BenchmarkRunEvidence,
        *,
        benchmark_package_hash: str,
        actor_id: str = "champion-registry",
        reason: str = "Initial benchmarked Champion registration.",
        now: datetime | None = None,
    ) -> ChampionSnapshotRecord:
        now = now or datetime.now(timezone.utc)
        family_id = run.contract.agent.family_id
        snapshot_id = run.contract.agent.snapshot_id
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if self._head_row(connection, family_id) is not None:
                    raise self.conflict_error(
                        f"Champion family already registered: {family_id}"
                    )
                record = ChampionSnapshotRecord(
                    family_id=family_id,
                    snapshot_id=snapshot_id,
                    run_id=run.evidence_id,
                    benchmark_evidence_hash=run.evidence_hash,
                    benchmark_package_hash=benchmark_package_hash,
                    parent_snapshot_id=None,
                    status=ChampionVersionStatus.CHAMPION,
                    created_at=now,
                )
                self._insert_record(connection, record)
                connection.execute(
                    "INSERT INTO champion_heads "
                    "(family_id, active_snapshot_id, revision, updated_at) "
                    "VALUES (?, ?, 0, ?)",
                    (family_id, snapshot_id, now.isoformat()),
                )
                self._append_event(
                    connection,
                    event_type=ChampionEventType.REGISTERED,
                    family_id=family_id,
                    snapshot_id=snapshot_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    payload={
                        "run_id": run.evidence_id,
                        "benchmark_evidence_hash": run.evidence_hash,
                        "benchmark_package_hash": benchmark_package_hash,
                    },
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def store_decision(
        self,
        decision: ChampionSelectionDecision,
        *,
        family_id: str,
        actor_id: str = "champion-promotion-gate",
        now: datetime | None = None,
    ) -> tuple[ChampionSelectionDecision, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT decision_json FROM champion_decisions "
                    "WHERE decision_id = ?",
                    (decision.decision_id,),
                ).fetchone()
                if row is not None:
                    existing = ChampionSelectionDecision.model_validate_json(
                        row["decision_json"]
                    )
                    if existing != decision:
                        raise self.conflict_error(
                            "Conflicting Champion decision under the same ID."
                        )
                    connection.commit()
                    return existing, True
                connection.execute(
                    "INSERT INTO champion_decisions "
                    "(decision_id, decision_hash, decision_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        decision.decision_id,
                        decision.decision_hash,
                        decision.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ChampionEventType.DECISION_STORED,
                    family_id=family_id,
                    snapshot_id=(
                        decision.selected_snapshot_id
                        or decision.baseline_snapshot_id
                    ),
                    reason=decision.reason,
                    actor_id=actor_id,
                    created_at=now,
                    payload={
                        "decision_id": decision.decision_id,
                        "decision_hash": decision.decision_hash,
                        "action": decision.action.value,
                        "selected_snapshot_id": decision.selected_snapshot_id,
                    },
                )
                connection.commit()
                return decision, False
            except Exception:
                connection.rollback()
                raise

    def admit_challenger(
        self,
        run: BenchmarkRunEvidence,
        decision: ChampionSelectionDecision,
        *,
        campaign_id: str,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[ChampionSnapshotRecord, bool]:
        now = now or datetime.now(timezone.utc)
        if decision.action != ChampionDecisionAction.PROMOTE:
            raise ValueError("Only a promotion decision may admit a Challenger.")
        if (
            decision.selected_run_id != run.evidence_id
            or decision.selected_snapshot_id
            != run.contract.agent.snapshot_id
            or decision.selected_round
            != run.contract.agent.evolution_round
        ):
            raise ValueError("Challenger run differs from the selection decision.")
        family_id = run.contract.agent.family_id
        snapshot_id = run.contract.agent.snapshot_id
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = self._snapshot_row(
                    connection, family_id, snapshot_id
                )
                if existing_row is not None:
                    existing = self._row_to_record(existing_row)
                    immutable = (
                        existing.run_id,
                        existing.benchmark_evidence_hash,
                        existing.benchmark_package_hash,
                        existing.parent_snapshot_id,
                        existing.decision_id,
                        existing.decision_hash,
                        existing.policy_hash,
                        existing.campaign_id,
                    )
                    expected = (
                        run.evidence_id,
                        run.evidence_hash,
                        decision.benchmark_package_hash,
                        decision.baseline_snapshot_id,
                        decision.decision_id,
                        decision.decision_hash,
                        decision.policy.policy_hash,
                        campaign_id,
                    )
                    if immutable != expected:
                        raise self.conflict_error(
                            "Conflicting Challenger under the same snapshot ID."
                        )
                    connection.commit()
                    return existing, True
                head = self._require_head(connection, family_id)
                if head["active_snapshot_id"] != decision.baseline_snapshot_id:
                    raise self.stale_error(
                        "Champion decision baseline is no longer active."
                    )
                record = ChampionSnapshotRecord(
                    family_id=family_id,
                    snapshot_id=snapshot_id,
                    run_id=run.evidence_id,
                    benchmark_evidence_hash=run.evidence_hash,
                    benchmark_package_hash=decision.benchmark_package_hash,
                    parent_snapshot_id=decision.baseline_snapshot_id,
                    status=ChampionVersionStatus.CHALLENGER,
                    decision_id=decision.decision_id,
                    decision_hash=decision.decision_hash,
                    policy_hash=decision.policy.policy_hash,
                    campaign_id=campaign_id,
                    created_at=now,
                )
                self._insert_record(connection, record)
                self._append_event(
                    connection,
                    event_type=ChampionEventType.CHALLENGER_ADMITTED,
                    family_id=family_id,
                    snapshot_id=snapshot_id,
                    from_snapshot_id=decision.baseline_snapshot_id,
                    to_snapshot_id=snapshot_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    payload={
                        "run_id": run.evidence_id,
                        "evidence_hash": run.evidence_hash,
                        "decision_hash": decision.decision_hash,
                        "campaign_id": campaign_id,
                    },
                )
                connection.commit()
                return record, False
            except Exception:
                connection.rollback()
                raise

    def record_evaluation(
        self,
        family_id: str,
        snapshot_id: str,
        decision: ChampionSelectionDecision,
        *,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ChampionSnapshotRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_record(
                    connection, family_id, snapshot_id
                )
                if record.status in {
                    ChampionVersionStatus.EVALUATED,
                    ChampionVersionStatus.AUTHORIZED,
                    ChampionVersionStatus.CHAMPION,
                    ChampionVersionStatus.ROLLED_BACK,
                }:
                    if record.decision_hash != decision.decision_hash:
                        raise self.conflict_error(
                            "Existing evaluation differs from the decision."
                        )
                    connection.commit()
                    return record
                if record.status != ChampionVersionStatus.CHALLENGER:
                    raise ValueError("Only a Challenger may become evaluated.")
                if (
                    record.decision_id != decision.decision_id
                    or record.decision_hash != decision.decision_hash
                    or decision.selected_snapshot_id != snapshot_id
                ):
                    raise ValueError(
                        "Champion evaluation decision binding mismatch."
                    )
                connection.execute(
                    "UPDATE champion_snapshots SET status = ? "
                    "WHERE family_id = ? AND snapshot_id = ?",
                    (
                        ChampionVersionStatus.EVALUATED.value,
                        family_id,
                        snapshot_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ChampionEventType.EVALUATED,
                    family_id=family_id,
                    snapshot_id=snapshot_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    payload={
                        "decision_id": decision.decision_id,
                        "decision_hash": decision.decision_hash,
                    },
                )
                connection.commit()
                return self.get(family_id, snapshot_id)
            except Exception:
                connection.rollback()
                raise

    def mark_authorized(
        self,
        family_id: str,
        snapshot_id: str,
        *,
        campaign_id: str,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ChampionSnapshotRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_record(
                    connection, family_id, snapshot_id
                )
                if record.status in {
                    ChampionVersionStatus.AUTHORIZED,
                    ChampionVersionStatus.CHAMPION,
                    ChampionVersionStatus.ROLLED_BACK,
                }:
                    if record.campaign_id != campaign_id:
                        raise self.conflict_error(
                            "Authorized Challenger is bound to another Campaign."
                        )
                    connection.commit()
                    return record
                if record.status != ChampionVersionStatus.EVALUATED:
                    raise ValueError(
                        "Only an evaluated Challenger may be authorized."
                    )
                if record.campaign_id != campaign_id:
                    raise ValueError(
                        "Champion authorization Campaign binding mismatch."
                    )
                connection.execute(
                    "UPDATE champion_snapshots SET status = ? "
                    "WHERE family_id = ? AND snapshot_id = ?",
                    (
                        ChampionVersionStatus.AUTHORIZED.value,
                        family_id,
                        snapshot_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ChampionEventType.AUTHORIZED,
                    family_id=family_id,
                    snapshot_id=snapshot_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    payload={"campaign_id": campaign_id},
                )
                connection.commit()
                return self.get(family_id, snapshot_id)
            except Exception:
                connection.rollback()
                raise

    def activate(
        self,
        family_id: str,
        snapshot_id: str,
        *,
        campaign_id: str,
        expected_active_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ChampionSnapshotRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, family_id)
                self._check_revision(head, expected_active_revision)
                challenger = self._require_record(
                    connection, family_id, snapshot_id
                )
                if challenger.status != ChampionVersionStatus.AUTHORIZED:
                    raise ValueError(
                        "Only an authorized Challenger may become Champion."
                    )
                if challenger.campaign_id != campaign_id:
                    raise ValueError(
                        "Champion activation Campaign binding mismatch."
                    )
                previous_id = head["active_snapshot_id"]
                if challenger.parent_snapshot_id != previous_id:
                    raise self.stale_error(
                        "Challenger parent is no longer the active Champion."
                    )
                connection.execute(
                    "UPDATE champion_snapshots SET status = ? "
                    "WHERE family_id = ? AND snapshot_id = ?",
                    (
                        ChampionVersionStatus.RETIRED.value,
                        family_id,
                        previous_id,
                    ),
                )
                connection.execute(
                    "UPDATE champion_snapshots SET status = ? "
                    "WHERE family_id = ? AND snapshot_id = ?",
                    (
                        ChampionVersionStatus.CHAMPION.value,
                        family_id,
                        snapshot_id,
                    ),
                )
                changed = connection.execute(
                    "UPDATE champion_heads SET active_snapshot_id = ?, "
                    "revision = revision + 1, updated_at = ? "
                    "WHERE family_id = ? AND revision = ?",
                    (
                        snapshot_id,
                        now.isoformat(),
                        family_id,
                        expected_active_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise self.stale_error(
                        "Active Champion revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    event_type=ChampionEventType.ACTIVATED,
                    family_id=family_id,
                    snapshot_id=snapshot_id,
                    from_snapshot_id=previous_id,
                    to_snapshot_id=snapshot_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    payload={
                        "campaign_id": campaign_id,
                        "active_revision_before": expected_active_revision,
                        "active_revision_after": expected_active_revision + 1,
                    },
                )
                connection.commit()
                return self.get(family_id, snapshot_id)
            except Exception:
                connection.rollback()
                raise

    def rollback(
        self,
        family_id: str,
        to_snapshot_id: str,
        *,
        expected_active_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ChampionSnapshotRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, family_id)
                self._check_revision(head, expected_active_revision)
                current_id = head["active_snapshot_id"]
                if current_id == to_snapshot_id:
                    raise ValueError("Rollback target is already Champion.")
                current = self._require_record(connection, family_id, current_id)
                target = self._require_record(
                    connection, family_id, to_snapshot_id
                )
                if current.status != ChampionVersionStatus.CHAMPION:
                    raise ValueError("Current Champion pointer is inconsistent.")
                if target.status != ChampionVersionStatus.RETIRED:
                    raise ValueError(
                        "Rollback target must be a retired Champion."
                    )
                if current.parent_snapshot_id != to_snapshot_id:
                    raise ValueError(
                        "Rollback target is not the current Champion's parent."
                    )
                connection.execute(
                    "UPDATE champion_snapshots SET status = ? "
                    "WHERE family_id = ? AND snapshot_id = ?",
                    (
                        ChampionVersionStatus.ROLLED_BACK.value,
                        family_id,
                        current_id,
                    ),
                )
                connection.execute(
                    "UPDATE champion_snapshots SET status = ? "
                    "WHERE family_id = ? AND snapshot_id = ?",
                    (
                        ChampionVersionStatus.CHAMPION.value,
                        family_id,
                        to_snapshot_id,
                    ),
                )
                changed = connection.execute(
                    "UPDATE champion_heads SET active_snapshot_id = ?, "
                    "revision = revision + 1, updated_at = ? "
                    "WHERE family_id = ? AND revision = ?",
                    (
                        to_snapshot_id,
                        now.isoformat(),
                        family_id,
                        expected_active_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise self.stale_error(
                        "Active Champion revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    event_type=ChampionEventType.ROLLED_BACK,
                    family_id=family_id,
                    snapshot_id=current_id,
                    from_snapshot_id=current_id,
                    to_snapshot_id=to_snapshot_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    payload={
                        "active_revision_before": expected_active_revision,
                        "active_revision_after": expected_active_revision + 1,
                    },
                )
                connection.commit()
                return self.get(family_id, to_snapshot_id)
            except Exception:
                connection.rollback()
                raise
