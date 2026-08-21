from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evoagent.campaigns import CampaignRecord, CampaignState, CampaignType
from evoagent.model_registry.models import canonical_sha256

from .models import (
    LOCAL_POLICY_MANIFEST_ADAPTER,
    InitialLocalPolicyManifest,
    LocalPolicyAuditEvent,
    LocalPolicyCandidateManifest,
    LocalPolicyEventType,
    LocalPolicyHead,
    LocalPolicyPromotionDecision,
    LocalPolicyPromotionReport,
    LocalPolicyRegistryCheckpoint,
    LocalPolicyRollbackReport,
    LocalPolicyRollbackRequest,
    LocalPolicyVersionRecord,
    LocalPolicyVersionStatus,
)


_GENESIS_HASH = "0" * 64


class LocalPolicyRegistryConflictError(RuntimeError):
    pass


class StaleLocalPolicyRevision(RuntimeError):
    pass


class LocalPolicyAuditIntegrityError(RuntimeError):
    pass


class SQLiteLocalPolicyRegistry:
    """Immutable local-policy versions plus one optimistic active pointer."""

    def __init__(self, path: str | Path):
        raw_path = Path(path).expanduser()
        if raw_path.is_symlink():
            raise ValueError("Local-policy Registry path must not be a symlink.")
        self.path = raw_path.resolve()
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
                CREATE TABLE IF NOT EXISTS local_policy_versions (
                    family_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    parent_policy_id TEXT,
                    status TEXT NOT NULL,
                    promotion_report_json TEXT,
                    promotion_decision_json TEXT,
                    promotion_campaign_id TEXT,
                    promotion_authorized_by TEXT,
                    activated_by TEXT,
                    activated_at TEXT,
                    rollback_request_json TEXT,
                    rollback_report_json TEXT,
                    rollback_campaign_id TEXT,
                    rollback_authorized_by TEXT,
                    rolled_back_by TEXT,
                    rolled_back_at TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(family_id, policy_id)
                );

                CREATE TABLE IF NOT EXISTS local_policy_heads (
                    family_id TEXT PRIMARY KEY,
                    active_policy_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(family_id, active_policy_id)
                        REFERENCES local_policy_versions(family_id, policy_id)
                );

                CREATE TABLE IF NOT EXISTS local_policy_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    from_policy_id TEXT,
                    to_policy_id TEXT,
                    reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                """
            )

    def family_exists(self, family_id: str) -> bool:
        with self._connection() as connection:
            return self._head_row(connection, family_id) is not None

    def register_initial(
        self,
        manifest: InitialLocalPolicyManifest,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._head_row(connection, manifest.family_id)
                if head is not None:
                    existing = self._require_record(
                        connection,
                        manifest.family_id,
                        manifest.policy_id,
                    )
                    if (
                        existing.manifest == manifest
                        and existing.status == LocalPolicyVersionStatus.ACTIVE
                        and head["active_policy_id"] == manifest.policy_id
                    ):
                        connection.commit()
                        return existing
                    raise LocalPolicyRegistryConflictError(
                        f"Local-policy family already registered: {manifest.family_id}"
                    )
                record = LocalPolicyVersionRecord(
                    family_id=manifest.family_id,
                    policy_id=manifest.policy_id,
                    manifest=manifest,
                    parent_policy_id=None,
                    status=LocalPolicyVersionStatus.ACTIVE,
                    created_at=now,
                )
                self._insert_record(connection, record)
                connection.execute(
                    "INSERT INTO local_policy_heads "
                    "(family_id, active_policy_id, revision, updated_at) "
                    "VALUES (?, ?, 0, ?)",
                    (manifest.family_id, manifest.policy_id, now.isoformat()),
                )
                self._append_event(
                    connection,
                    event_type=LocalPolicyEventType.REGISTERED,
                    family_id=manifest.family_id,
                    policy_id=manifest.policy_id,
                    actor_id=actor_id,
                    reason="Initial local policy registered.",
                    metadata={"manifest_hash": manifest.manifest_hash},
                    created_at=now,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def admit_candidate(
        self,
        manifest: LocalPolicyCandidateManifest,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_row = self._version_row(
                    connection,
                    manifest.family_id,
                    manifest.candidate_id,
                )
                if existing_row is not None:
                    existing = self._row_to_record(existing_row)
                    if existing.manifest == manifest:
                        connection.commit()
                        return existing
                    raise LocalPolicyRegistryConflictError(
                        "Local-policy candidate ID contains another manifest."
                    )
                head = self._require_head(connection, manifest.family_id)
                if head["active_policy_id"] != manifest.base_policy_id:
                    raise StaleLocalPolicyRevision(
                        "Candidate base policy is no longer active."
                    )
                parent = self._require_record(
                    connection,
                    manifest.family_id,
                    manifest.base_policy_id,
                )
                if parent.status != LocalPolicyVersionStatus.ACTIVE:
                    raise ValueError("Candidate base policy must be ACTIVE.")
                parent_checkpoint = (
                    parent.manifest.checkpoint_hash
                    if isinstance(parent.manifest, InitialLocalPolicyManifest)
                    else parent.manifest.selected_checkpoint_hash
                )
                if parent_checkpoint != manifest.base_checkpoint_hash:
                    raise ValueError(
                        "Candidate base checkpoint differs from the active policy."
                    )
                record = LocalPolicyVersionRecord(
                    family_id=manifest.family_id,
                    policy_id=manifest.candidate_id,
                    manifest=manifest,
                    parent_policy_id=manifest.base_policy_id,
                    status=LocalPolicyVersionStatus.CANDIDATE,
                    created_at=now,
                )
                self._insert_record(connection, record)
                self._append_event(
                    connection,
                    event_type=LocalPolicyEventType.CANDIDATE_ADMITTED,
                    family_id=manifest.family_id,
                    policy_id=manifest.candidate_id,
                    from_policy_id=manifest.base_policy_id,
                    to_policy_id=manifest.candidate_id,
                    actor_id=actor_id,
                    reason=(
                        "Accepted local-RL checkpoint admitted as immutable candidate."
                    ),
                    metadata={
                        "manifest_hash": manifest.manifest_hash,
                        "fully_attested_package_hash": (
                            manifest.fully_attested_package_hash
                        ),
                        "acceptance_receipt_hash": (
                            manifest.acceptance_receipt_hash
                        ),
                    },
                    created_at=now,
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def record_promotion(
        self,
        family_id: str,
        candidate_id: str,
        report: LocalPolicyPromotionReport,
        decision: LocalPolicyPromotionDecision,
        *,
        campaign_id: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_record(connection, family_id, candidate_id)
                self._validate_promotion_evidence(record, report, decision)
                if record.promotion_report is not None:
                    if (
                        record.promotion_report == report
                        and record.promotion_decision == decision
                        and record.promotion_campaign_id == campaign_id
                    ):
                        connection.commit()
                        return record
                    raise LocalPolicyRegistryConflictError(
                        "Candidate already contains different promotion evidence."
                    )
                if record.status != LocalPolicyVersionStatus.CANDIDATE:
                    raise ValueError(
                        "Only an unevaluated candidate may receive promotion evidence."
                    )
                status = (
                    LocalPolicyVersionStatus.EVALUATED
                    if decision.promote
                    else LocalPolicyVersionStatus.REJECTED
                )
                connection.execute(
                    "UPDATE local_policy_versions SET status = ?, "
                    "promotion_report_json = ?, promotion_decision_json = ?, "
                    "promotion_campaign_id = ? WHERE family_id = ? AND policy_id = ?",
                    (
                        status.value,
                        self._json(report.model_dump(mode="json")),
                        self._json(decision.model_dump(mode="json")),
                        campaign_id,
                        family_id,
                        candidate_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=(
                        LocalPolicyEventType.EVALUATED
                        if decision.promote
                        else LocalPolicyEventType.REJECTED
                    ),
                    family_id=family_id,
                    policy_id=candidate_id,
                    actor_id=actor_id,
                    reason=decision.reason,
                    metadata={
                        "report_hash": report.report_hash,
                        "decision_hash": decision.decision_hash,
                        "campaign_id": campaign_id,
                        "promote": decision.promote,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._require_record(connection, family_id, candidate_id)
            except Exception:
                connection.rollback()
                raise

    def mark_promotion_authorized(
        self,
        family_id: str,
        candidate_id: str,
        campaign: CampaignRecord,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_record(connection, family_id, candidate_id)
                self._validate_promotion_campaign(record, campaign)
                if record.promotion_authorized_by is not None:
                    if record.promotion_authorized_by != actor_id:
                        raise LocalPolicyRegistryConflictError(
                            "Promotion authorization retry used another actor."
                        )
                    connection.commit()
                    return record
                if record.status != LocalPolicyVersionStatus.EVALUATED:
                    raise ValueError(
                        "Only a passing evaluated candidate may be authorized."
                    )
                connection.execute(
                    "UPDATE local_policy_versions SET status = ?, "
                    "promotion_authorized_by = ? "
                    "WHERE family_id = ? AND policy_id = ?",
                    (
                        LocalPolicyVersionStatus.AUTHORIZED.value,
                        actor_id,
                        family_id,
                        candidate_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=LocalPolicyEventType.AUTHORIZED,
                    family_id=family_id,
                    policy_id=candidate_id,
                    actor_id=actor_id,
                    reason="Exact high-risk promotion Campaign authorized.",
                    metadata={
                        "campaign_id": campaign.campaign_id,
                        "campaign_revision": campaign.revision,
                        "decision_hash": record.promotion_decision.decision_hash,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._require_record(connection, family_id, candidate_id)
            except Exception:
                connection.rollback()
                raise

    def activate(
        self,
        family_id: str,
        candidate_id: str,
        campaign: CampaignRecord,
        *,
        expected_active_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, family_id)
                record = self._require_record(connection, family_id, candidate_id)
                self._validate_promotion_campaign(record, campaign)
                if (
                    head["active_policy_id"] == candidate_id
                    and record.status == LocalPolicyVersionStatus.ACTIVE
                ):
                    if record.activated_by != actor_id:
                        raise LocalPolicyRegistryConflictError(
                            "Activation retry used another actor."
                        )
                    connection.commit()
                    return record
                self._check_revision(head, expected_active_revision)
                if record.status != LocalPolicyVersionStatus.AUTHORIZED:
                    raise ValueError(
                        "Only an authorized local-policy candidate may become active."
                    )
                if record.parent_policy_id is None:
                    raise ValueError("Candidate lacks a parent policy.")
                if head["active_policy_id"] != record.parent_policy_id:
                    raise StaleLocalPolicyRevision(
                        "Candidate parent is no longer the active policy."
                    )
                parent = self._require_record(
                    connection,
                    family_id,
                    record.parent_policy_id,
                )
                if parent.status != LocalPolicyVersionStatus.ACTIVE:
                    raise ValueError("Candidate parent is not ACTIVE.")
                connection.execute(
                    "UPDATE local_policy_versions SET status = ? "
                    "WHERE family_id = ? AND policy_id = ?",
                    (
                        LocalPolicyVersionStatus.SUPERSEDED.value,
                        family_id,
                        record.parent_policy_id,
                    ),
                )
                connection.execute(
                    "UPDATE local_policy_versions SET status = ?, activated_by = ?, "
                    "activated_at = ? WHERE family_id = ? AND policy_id = ?",
                    (
                        LocalPolicyVersionStatus.ACTIVE.value,
                        actor_id,
                        now.isoformat(),
                        family_id,
                        candidate_id,
                    ),
                )
                changed = connection.execute(
                    "UPDATE local_policy_heads SET active_policy_id = ?, "
                    "revision = revision + 1, updated_at = ? "
                    "WHERE family_id = ? AND revision = ?",
                    (
                        candidate_id,
                        now.isoformat(),
                        family_id,
                        expected_active_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleLocalPolicyRevision(
                        "Active local-policy revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    event_type=LocalPolicyEventType.ACTIVATED,
                    family_id=family_id,
                    policy_id=candidate_id,
                    from_policy_id=record.parent_policy_id,
                    to_policy_id=candidate_id,
                    actor_id=actor_id,
                    reason="Explicit local policy pointer activation completed.",
                    metadata={
                        "campaign_id": campaign.campaign_id,
                        "decision_hash": record.promotion_decision.decision_hash,
                        "active_revision_before": expected_active_revision,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._require_record(connection, family_id, candidate_id)
            except Exception:
                connection.rollback()
                raise

    def record_rollback_submission(
        self,
        family_id: str,
        candidate_id: str,
        request: LocalPolicyRollbackRequest,
        report: LocalPolicyRollbackReport,
        *,
        campaign_id: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, family_id)
                record = self._require_record(connection, family_id, candidate_id)
                self._validate_rollback_evidence(record, request, report)
                if record.rollback_request is not None:
                    if (
                        record.rollback_request == request
                        and record.rollback_report == report
                        and record.rollback_campaign_id == campaign_id
                    ):
                        connection.commit()
                        return record
                    raise LocalPolicyRegistryConflictError(
                        "Candidate already contains different rollback evidence."
                    )
                if (
                    record.status != LocalPolicyVersionStatus.ACTIVE
                    or head["active_policy_id"] != candidate_id
                ):
                    raise ValueError(
                        "Rollback submission requires the candidate to be ACTIVE."
                    )
                if record.parent_policy_id is None:
                    raise ValueError("Rollback candidate lacks a parent policy.")
                target = self._require_record(
                    connection,
                    family_id,
                    record.parent_policy_id,
                )
                if target.status != LocalPolicyVersionStatus.SUPERSEDED:
                    raise ValueError(
                        "Rollback target must be the direct SUPERSEDED parent."
                    )
                connection.execute(
                    "UPDATE local_policy_versions SET rollback_request_json = ?, "
                    "rollback_report_json = ?, rollback_campaign_id = ? "
                    "WHERE family_id = ? AND policy_id = ?",
                    (
                        self._json(request.model_dump(mode="json")),
                        self._json(report.model_dump(mode="json")),
                        campaign_id,
                        family_id,
                        candidate_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=LocalPolicyEventType.ROLLBACK_SUBMITTED,
                    family_id=family_id,
                    policy_id=candidate_id,
                    from_policy_id=candidate_id,
                    to_policy_id=record.parent_policy_id,
                    actor_id=actor_id,
                    reason="Independent local policy rollback assessment submitted.",
                    metadata={
                        "request_hash": request.request_hash,
                        "report_hash": report.report_hash,
                        "campaign_id": campaign_id,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._require_record(connection, family_id, candidate_id)
            except Exception:
                connection.rollback()
                raise

    def mark_rollback_authorized(
        self,
        family_id: str,
        candidate_id: str,
        campaign: CampaignRecord,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_record(connection, family_id, candidate_id)
                self._validate_rollback_campaign(record, campaign)
                if record.rollback_authorized_by is not None:
                    if record.rollback_authorized_by != actor_id:
                        raise LocalPolicyRegistryConflictError(
                            "Rollback authorization retry used another actor."
                        )
                    connection.commit()
                    return record
                if record.status != LocalPolicyVersionStatus.ACTIVE:
                    raise ValueError(
                        "Rollback authorization requires an ACTIVE source policy."
                    )
                connection.execute(
                    "UPDATE local_policy_versions SET rollback_authorized_by = ? "
                    "WHERE family_id = ? AND policy_id = ?",
                    (actor_id, family_id, candidate_id),
                )
                self._append_event(
                    connection,
                    event_type=LocalPolicyEventType.ROLLBACK_AUTHORIZED,
                    family_id=family_id,
                    policy_id=candidate_id,
                    from_policy_id=candidate_id,
                    to_policy_id=record.parent_policy_id,
                    actor_id=actor_id,
                    reason="Exact high-risk rollback Campaign authorized.",
                    metadata={
                        "campaign_id": campaign.campaign_id,
                        "campaign_revision": campaign.revision,
                        "request_hash": record.rollback_request.request_hash,
                        "report_hash": record.rollback_report.report_hash,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._require_record(connection, family_id, candidate_id)
            except Exception:
                connection.rollback()
                raise

    def rollback(
        self,
        family_id: str,
        *,
        from_policy_id: str,
        to_policy_id: str,
        campaign: CampaignRecord,
        expected_active_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> LocalPolicyVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, family_id)
                current = self._require_record(connection, family_id, from_policy_id)
                target = self._require_record(connection, family_id, to_policy_id)
                self._validate_rollback_campaign(current, campaign)
                if (
                    head["active_policy_id"] == to_policy_id
                    and current.status == LocalPolicyVersionStatus.ROLLED_BACK
                    and target.status == LocalPolicyVersionStatus.ACTIVE
                ):
                    if current.rolled_back_by != actor_id:
                        raise LocalPolicyRegistryConflictError(
                            "Rollback retry used another actor."
                        )
                    connection.commit()
                    return target
                self._check_revision(head, expected_active_revision)
                if head["active_policy_id"] != from_policy_id:
                    raise StaleLocalPolicyRevision(
                        "Rollback source is no longer the active policy."
                    )
                if current.status != LocalPolicyVersionStatus.ACTIVE:
                    raise ValueError("Rollback source is not ACTIVE.")
                if target.status != LocalPolicyVersionStatus.SUPERSEDED:
                    raise ValueError(
                        "Rollback target must be a SUPERSEDED policy."
                    )
                if current.parent_policy_id != to_policy_id:
                    raise ValueError(
                        "Rollback target is not the candidate's direct parent."
                    )
                if current.rollback_authorized_by is None:
                    raise ValueError("Rollback lacks Registry authorization.")
                connection.execute(
                    "UPDATE local_policy_versions SET status = ?, rolled_back_by = ?, "
                    "rolled_back_at = ? WHERE family_id = ? AND policy_id = ?",
                    (
                        LocalPolicyVersionStatus.ROLLED_BACK.value,
                        actor_id,
                        now.isoformat(),
                        family_id,
                        from_policy_id,
                    ),
                )
                connection.execute(
                    "UPDATE local_policy_versions SET status = ? "
                    "WHERE family_id = ? AND policy_id = ?",
                    (
                        LocalPolicyVersionStatus.ACTIVE.value,
                        family_id,
                        to_policy_id,
                    ),
                )
                changed = connection.execute(
                    "UPDATE local_policy_heads SET active_policy_id = ?, "
                    "revision = revision + 1, updated_at = ? "
                    "WHERE family_id = ? AND revision = ?",
                    (
                        to_policy_id,
                        now.isoformat(),
                        family_id,
                        expected_active_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleLocalPolicyRevision(
                        "Active local-policy revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    event_type=LocalPolicyEventType.ROLLED_BACK,
                    family_id=family_id,
                    policy_id=from_policy_id,
                    from_policy_id=from_policy_id,
                    to_policy_id=to_policy_id,
                    actor_id=actor_id,
                    reason="Explicit local policy pointer rollback completed.",
                    metadata={
                        "campaign_id": campaign.campaign_id,
                        "request_hash": current.rollback_request.request_hash,
                        "active_revision_before": expected_active_revision,
                    },
                    created_at=now,
                )
                connection.commit()
                return self._require_record(connection, family_id, to_policy_id)
            except Exception:
                connection.rollback()
                raise

    def active(self, family_id: str) -> LocalPolicyVersionRecord:
        with self._connection() as connection:
            head = self._require_head(connection, family_id)
            return self._require_record(
                connection,
                family_id,
                head["active_policy_id"],
            )

    def head(self, family_id: str) -> LocalPolicyHead:
        with self._connection() as connection:
            row = self._require_head(connection, family_id)
            return LocalPolicyHead(
                family_id=family_id,
                active_policy_id=row["active_policy_id"],
                revision=int(row["revision"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def active_revision(self, family_id: str) -> int:
        return self.head(family_id).revision

    def get(self, family_id: str, policy_id: str) -> LocalPolicyVersionRecord:
        with self._connection() as connection:
            return self._require_record(connection, family_id, policy_id)

    def list_versions(self, family_id: str) -> tuple[LocalPolicyVersionRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM local_policy_versions WHERE family_id = ? "
                "ORDER BY created_at, policy_id",
                (family_id,),
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    def events(self) -> tuple[LocalPolicyAuditEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM local_policy_audit_events ORDER BY sequence"
            ).fetchall()
            return tuple(self._row_to_event(row) for row in rows)

    def checkpoint(self) -> LocalPolicyRegistryCheckpoint:
        events = self.events()
        return LocalPolicyRegistryCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_state(self, family_id: str) -> bool:
        records = self.list_versions(family_id)
        if not records:
            raise LocalPolicyAuditIntegrityError(
                f"Local-policy family has no records: {family_id}"
            )
        active = [
            item for item in records
            if item.status == LocalPolicyVersionStatus.ACTIVE
        ]
        if len(active) != 1:
            raise LocalPolicyAuditIntegrityError(
                "Local-policy family must contain exactly one ACTIVE version."
            )
        if self.active(family_id) != active[0]:
            raise LocalPolicyAuditIntegrityError(
                "Local-policy pointer differs from the ACTIVE record."
            )
        by_id = {item.policy_id: item for item in records}
        for record in records:
            if record.parent_policy_id is not None:
                parent = by_id.get(record.parent_policy_id)
                if parent is None:
                    raise LocalPolicyAuditIntegrityError(
                        "Local-policy Registry contains an unknown parent."
                    )
                if parent.created_at > record.created_at:
                    raise LocalPolicyAuditIntegrityError(
                        "Local-policy parent was created after its child."
                    )
        self.verify_audit()
        return True

    def verify_audit(
        self,
        checkpoint: LocalPolicyRegistryCheckpoint | None = None,
    ) -> bool:
        events = self.events()
        previous = _GENESIS_HASH
        for sequence, event in enumerate(events, start=1):
            if event.sequence != sequence or event.previous_hash != previous:
                raise LocalPolicyAuditIntegrityError(
                    "Local-policy audit sequence or hash chain is broken."
                )
            expected = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                family_id=event.family_id,
                policy_id=event.policy_id,
                from_policy_id=event.from_policy_id,
                to_policy_id=event.to_policy_id,
                reason=event.reason,
                metadata=event.metadata,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected:
                raise LocalPolicyAuditIntegrityError(
                    "Local-policy audit event content was modified."
                )
            previous = event.event_hash
        current = LocalPolicyRegistryCheckpoint(
            event_count=len(events),
            head_hash=previous,
        )
        if checkpoint is not None and current != checkpoint:
            raise LocalPolicyAuditIntegrityError(
                "Local-policy audit differs from the external checkpoint."
            )
        return True

    @staticmethod
    def _validate_promotion_evidence(
        record: LocalPolicyVersionRecord,
        report: LocalPolicyPromotionReport,
        decision: LocalPolicyPromotionDecision,
    ) -> None:
        if not isinstance(record.manifest, LocalPolicyCandidateManifest):
            raise ValueError("Initial local policy cannot receive promotion evidence.")
        if (
            report.family_id != record.family_id
            or report.candidate_id != record.policy_id
            or report.base_policy_id != record.parent_policy_id
            or report.candidate_manifest_hash != record.manifest.manifest_hash
            or decision.family_id != record.family_id
            or decision.candidate_id != record.policy_id
            or decision.base_policy_id != record.parent_policy_id
            or decision.report_id != report.report_id
            or decision.report_hash != report.report_hash
            or decision.report_passed != report.passed
        ):
            raise ValueError(
                "Local-policy promotion evidence differs from the candidate record."
            )

    @staticmethod
    def _validate_promotion_campaign(
        record: LocalPolicyVersionRecord,
        campaign: CampaignRecord,
    ) -> None:
        if campaign.campaign_type != CampaignType.LOCAL_POLICY_PROMOTION:
            raise ValueError("Candidate requires a Local Policy Promotion Campaign.")
        if campaign.state not in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}:
            raise ValueError("Local Policy Promotion Campaign is not authorized.")
        if record.promotion_campaign_id != campaign.campaign_id:
            raise ValueError("Candidate is bound to another promotion Campaign.")
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "local_policy_promotion_candidate":
            raise ValueError("Promotion Campaign contains another artifact kind.")
        manifest = LocalPolicyCandidateManifest.model_validate(
            payload.get("candidate_manifest")
        )
        report = LocalPolicyPromotionReport.model_validate(
            payload.get("promotion_report")
        )
        decision = LocalPolicyPromotionDecision.model_validate(
            payload.get("promotion_decision")
        )
        if (
            manifest != record.manifest
            or report != record.promotion_report
            or decision != record.promotion_decision
        ):
            raise ValueError(
                "Promotion Campaign evidence differs from the Registry candidate."
            )

    @staticmethod
    def _validate_rollback_evidence(
        record: LocalPolicyVersionRecord,
        request: LocalPolicyRollbackRequest,
        report: LocalPolicyRollbackReport,
    ) -> None:
        if (
            request.family_id != record.family_id
            or request.from_policy_id != record.policy_id
            or request.to_policy_id != record.parent_policy_id
            or request.promotion_campaign_id != record.promotion_campaign_id
            or record.promotion_decision is None
            or request.promotion_decision_hash
            != record.promotion_decision.decision_hash
            or report.request_id != request.request_id
            or report.request_hash != request.request_hash
            or report.family_id != request.family_id
            or report.from_policy_id != request.from_policy_id
            or report.to_policy_id != request.to_policy_id
        ):
            raise ValueError(
                "Local-policy rollback evidence differs from the active candidate."
            )

    @staticmethod
    def _validate_rollback_campaign(
        record: LocalPolicyVersionRecord,
        campaign: CampaignRecord,
    ) -> None:
        if campaign.campaign_type != CampaignType.LOCAL_POLICY_ROLLBACK:
            raise ValueError("Rollback requires a Local Policy Rollback Campaign.")
        if campaign.state not in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}:
            raise ValueError("Local Policy Rollback Campaign is not authorized.")
        if record.rollback_campaign_id != campaign.campaign_id:
            raise ValueError("Candidate is bound to another rollback Campaign.")
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "local_policy_rollback_candidate":
            raise ValueError("Rollback Campaign contains another artifact kind.")
        request = LocalPolicyRollbackRequest.model_validate(
            payload.get("rollback_request")
        )
        report = LocalPolicyRollbackReport.model_validate(
            payload.get("rollback_report")
        )
        if request != record.rollback_request or report != record.rollback_report:
            raise ValueError(
                "Rollback Campaign evidence differs from the Registry candidate."
            )

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        record: LocalPolicyVersionRecord,
    ) -> None:
        connection.execute(
            "INSERT INTO local_policy_versions "
            "(family_id, policy_id, manifest_json, parent_policy_id, status, "
            "promotion_report_json, promotion_decision_json, promotion_campaign_id, "
            "promotion_authorized_by, activated_by, activated_at, "
            "rollback_request_json, rollback_report_json, rollback_campaign_id, "
            "rollback_authorized_by, rolled_back_by, rolled_back_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.family_id,
                record.policy_id,
                self._json(record.manifest.model_dump(mode="json")),
                record.parent_policy_id,
                record.status.value,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                record.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _head_row(connection: sqlite3.Connection, family_id: str):
        return connection.execute(
            "SELECT * FROM local_policy_heads WHERE family_id = ?",
            (family_id,),
        ).fetchone()

    def _require_head(self, connection: sqlite3.Connection, family_id: str):
        row = self._head_row(connection, family_id)
        if row is None:
            raise KeyError(f"Unknown local-policy family: {family_id}")
        return row

    @staticmethod
    def _version_row(
        connection: sqlite3.Connection,
        family_id: str,
        policy_id: str,
    ):
        return connection.execute(
            "SELECT * FROM local_policy_versions "
            "WHERE family_id = ? AND policy_id = ?",
            (family_id, policy_id),
        ).fetchone()

    def _require_record(
        self,
        connection: sqlite3.Connection,
        family_id: str,
        policy_id: str,
    ) -> LocalPolicyVersionRecord:
        row = self._version_row(connection, family_id, policy_id)
        if row is None:
            raise KeyError(f"Unknown local policy: {family_id}/{policy_id}")
        return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> LocalPolicyVersionRecord:
        return LocalPolicyVersionRecord(
            family_id=row["family_id"],
            policy_id=row["policy_id"],
            manifest=LOCAL_POLICY_MANIFEST_ADAPTER.validate_python(
                json.loads(row["manifest_json"])
            ),
            parent_policy_id=row["parent_policy_id"],
            status=LocalPolicyVersionStatus(row["status"]),
            promotion_report=(
                LocalPolicyPromotionReport.model_validate(
                    json.loads(row["promotion_report_json"])
                )
                if row["promotion_report_json"]
                else None
            ),
            promotion_decision=(
                LocalPolicyPromotionDecision.model_validate(
                    json.loads(row["promotion_decision_json"])
                )
                if row["promotion_decision_json"]
                else None
            ),
            promotion_campaign_id=row["promotion_campaign_id"],
            promotion_authorized_by=row["promotion_authorized_by"],
            activated_by=row["activated_by"],
            activated_at=(
                datetime.fromisoformat(row["activated_at"])
                if row["activated_at"]
                else None
            ),
            rollback_request=(
                LocalPolicyRollbackRequest.model_validate(
                    json.loads(row["rollback_request_json"])
                )
                if row["rollback_request_json"]
                else None
            ),
            rollback_report=(
                LocalPolicyRollbackReport.model_validate(
                    json.loads(row["rollback_report_json"])
                )
                if row["rollback_report_json"]
                else None
            ),
            rollback_campaign_id=row["rollback_campaign_id"],
            rollback_authorized_by=row["rollback_authorized_by"],
            rolled_back_by=row["rolled_back_by"],
            rolled_back_at=(
                datetime.fromisoformat(row["rolled_back_at"])
                if row["rolled_back_at"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: LocalPolicyEventType,
        family_id: str,
        policy_id: str,
        reason: str,
        metadata: dict[str, Any],
        actor_id: str,
        created_at: datetime,
        from_policy_id: str | None = None,
        to_policy_id: str | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM local_policy_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"local-policy-event:{uuid.uuid4()}"
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            family_id=family_id,
            policy_id=policy_id,
            from_policy_id=from_policy_id,
            to_policy_id=to_policy_id,
            reason=reason,
            metadata=metadata,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO local_policy_audit_events "
            "(sequence, event_id, event_type, family_id, policy_id, "
            "from_policy_id, to_policy_id, reason, metadata_json, actor_id, "
            "created_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type.value,
                family_id,
                policy_id,
                from_policy_id,
                to_policy_id,
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
        event_type: LocalPolicyEventType,
        family_id: str,
        policy_id: str,
        from_policy_id: str | None,
        to_policy_id: str | None,
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
                "family_id": family_id,
                "policy_id": policy_id,
                "from_policy_id": from_policy_id,
                "to_policy_id": to_policy_id,
                "reason": reason,
                "metadata": metadata,
                "actor_id": actor_id,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LocalPolicyAuditEvent:
        return LocalPolicyAuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            event_type=LocalPolicyEventType(row["event_type"]),
            family_id=row["family_id"],
            policy_id=row["policy_id"],
            from_policy_id=row["from_policy_id"],
            to_policy_id=row["to_policy_id"],
            reason=row["reason"],
            metadata=json.loads(row["metadata_json"]),
            actor_id=row["actor_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
        )

    @staticmethod
    def _check_revision(head: sqlite3.Row, expected_revision: int) -> None:
        if int(head["revision"]) != expected_revision:
            raise StaleLocalPolicyRevision(
                f"Expected active revision {expected_revision}, found {head['revision']}."
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
    "LocalPolicyAuditIntegrityError",
    "LocalPolicyRegistryConflictError",
    "SQLiteLocalPolicyRegistry",
    "StaleLocalPolicyRevision",
]
