from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import TypeAdapter

from evoagent.campaigns.models import CampaignRecord, CampaignState, CampaignType
from evoagent.model_registry.models import (
    ExternalModelCandidateManifest,
    ExternalTrainingReceipt,
    InitialModelManifest,
    ModelActivationDecision,
    ModelCandidateEvaluationReport,
    ModelEventType,
    ModelManifest,
    ModelRegistryCheckpoint,
    ModelVersionRecord,
    ModelVersionStatus,
    PersistentModelEvent,
    canonical_sha256,
)


_GENESIS_HASH = "0" * 64
_MANIFEST_ADAPTER = TypeAdapter(ModelManifest)


class ModelRegistryConflictError(RuntimeError):
    pass


class StaleModelRevision(RuntimeError):
    pass


class ModelAuditIntegrityError(RuntimeError):
    pass


class SQLiteModelRegistry:
    """Transactional immutable model versions plus one optimistic active pointer."""

    def __init__(self, path: str | Path):
        raw_path = Path(path).expanduser()
        if raw_path.is_symlink():
            raise ValueError("Model Registry database path must not be a symlink.")
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
                CREATE TABLE IF NOT EXISTS model_versions (
                    family_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    parent_model_id TEXT,
                    status TEXT NOT NULL,
                    training_receipt_json TEXT,
                    training_package_hash TEXT,
                    evaluation_json TEXT,
                    activation_decision_json TEXT,
                    activation_campaign_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(family_id, model_id)
                );

                CREATE TABLE IF NOT EXISTS model_heads (
                    family_id TEXT PRIMARY KEY,
                    active_model_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(family_id, active_model_id)
                        REFERENCES model_versions(family_id, model_id)
                );

                CREATE TABLE IF NOT EXISTS model_audit_events (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    from_model_id TEXT,
                    to_model_id TEXT,
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
        manifest: InitialModelManifest,
        *,
        reason: str = "initial model registration",
        actor_id: str = "evoagent-system",
        now: datetime | None = None,
    ) -> ModelVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if self._head_row(connection, manifest.family_id):
                    raise ModelRegistryConflictError(
                        f"Model family already registered: {manifest.family_id}"
                    )
                record = ModelVersionRecord(
                    family_id=manifest.family_id,
                    model_id=manifest.model_id,
                    manifest=manifest,
                    parent_model_id=None,
                    status=ModelVersionStatus.ACTIVE,
                    created_at=now,
                )
                self._insert_record(connection, record)
                connection.execute(
                    "INSERT INTO model_heads "
                    "(family_id, active_model_id, revision, updated_at) "
                    "VALUES (?, ?, 0, ?)",
                    (manifest.family_id, manifest.model_id, now.isoformat()),
                )
                self._append_event(
                    connection,
                    event_type=ModelEventType.REGISTERED,
                    family_id=manifest.family_id,
                    model_id=manifest.model_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    metadata={"manifest_hash": manifest.manifest_hash},
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def add_candidate(
        self,
        manifest: ExternalModelCandidateManifest,
        receipt: ExternalTrainingReceipt,
        *,
        parent_model_id: str,
        training_package_hash: str,
        reason: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> ModelVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if self._version_row(
                    connection,
                    manifest.family_id,
                    manifest.candidate_id,
                ):
                    raise ModelRegistryConflictError(
                        f"Duplicate model version: "
                        f"{manifest.family_id}/{manifest.candidate_id}"
                    )
                head = self._require_head(connection, manifest.family_id)
                if head["active_model_id"] != parent_model_id:
                    raise StaleModelRevision(
                        "Candidate parent does not match the active model."
                    )
                parent = self._require_record(
                    connection,
                    manifest.family_id,
                    parent_model_id,
                )
                if parent.status != ModelVersionStatus.ACTIVE:
                    raise ValueError("Candidate parent must be the active model.")
                if manifest.base_model_id != parent_model_id:
                    raise ValueError(
                        "Candidate manifest base model differs from its parent."
                    )
                if receipt.candidate_id != manifest.candidate_id:
                    raise ValueError(
                        "Training receipt does not identify the candidate manifest."
                    )
                record = ModelVersionRecord(
                    family_id=manifest.family_id,
                    model_id=manifest.candidate_id,
                    manifest=manifest,
                    parent_model_id=parent_model_id,
                    status=ModelVersionStatus.CANDIDATE,
                    training_receipt=receipt,
                    training_package_hash=training_package_hash,
                    created_at=now,
                )
                self._insert_record(connection, record)
                self._append_event(
                    connection,
                    event_type=ModelEventType.CANDIDATE_ADMITTED,
                    family_id=manifest.family_id,
                    model_id=manifest.candidate_id,
                    from_model_id=parent_model_id,
                    to_model_id=manifest.candidate_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    metadata={
                        "manifest_hash": manifest.manifest_hash,
                        "receipt_hash": receipt.receipt_hash,
                        "training_package_hash": training_package_hash,
                    },
                )
                connection.commit()
                return record
            except Exception:
                connection.rollback()
                raise

    def record_evaluation(
        self,
        family_id: str,
        candidate_id: str,
        report: ModelCandidateEvaluationReport,
        decision: ModelActivationDecision,
        *,
        activation_campaign_id: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> ModelVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_record(
                    connection,
                    family_id,
                    candidate_id,
                )
                self._validate_report_decision(record, report, decision)
                if record.status != ModelVersionStatus.CANDIDATE:
                    if (
                        record.evaluation == report
                        and record.activation_decision == decision
                        and record.activation_campaign_id == activation_campaign_id
                    ):
                        connection.commit()
                        return record
                    raise ValueError(
                        "Only an unevaluated candidate may receive an evaluation."
                    )
                status = (
                    ModelVersionStatus.EVALUATED
                    if decision.activate
                    else ModelVersionStatus.REJECTED
                )
                connection.execute(
                    "UPDATE model_versions SET status = ?, evaluation_json = ?, "
                    "activation_decision_json = ?, activation_campaign_id = ? "
                    "WHERE family_id = ? AND model_id = ?",
                    (
                        status.value,
                        self._json(report.model_dump(mode="json")),
                        self._json(decision.model_dump(mode="json")),
                        activation_campaign_id,
                        family_id,
                        candidate_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=(
                        ModelEventType.EVALUATED
                        if decision.activate
                        else ModelEventType.REJECTED
                    ),
                    family_id=family_id,
                    model_id=candidate_id,
                    reason=decision.reason,
                    actor_id=actor_id,
                    created_at=now,
                    metadata={
                        "report_hash": report.report_hash,
                        "decision_hash": decision.decision_hash,
                        "activation_campaign_id": activation_campaign_id,
                        "activate": decision.activate,
                    },
                )
                connection.commit()
                return self._require_record(
                    connection,
                    family_id,
                    candidate_id,
                )
            except Exception:
                connection.rollback()
                raise

    def mark_authorized(
        self,
        family_id: str,
        candidate_id: str,
        campaign: CampaignRecord,
        *,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ModelVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                record = self._require_record(
                    connection,
                    family_id,
                    candidate_id,
                )
                self._validate_campaign(
                    record,
                    campaign,
                    require_authorized=True,
                )
                if record.status == ModelVersionStatus.AUTHORIZED:
                    connection.commit()
                    return record
                if record.status != ModelVersionStatus.EVALUATED:
                    raise ValueError(
                        "Only a passing evaluated candidate may be authorized."
                    )
                connection.execute(
                    "UPDATE model_versions SET status = ? "
                    "WHERE family_id = ? AND model_id = ?",
                    (
                        ModelVersionStatus.AUTHORIZED.value,
                        family_id,
                        candidate_id,
                    ),
                )
                self._append_event(
                    connection,
                    event_type=ModelEventType.AUTHORIZED,
                    family_id=family_id,
                    model_id=candidate_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    metadata={
                        "campaign_id": campaign.campaign_id,
                        "campaign_revision": campaign.revision,
                        "report_hash": record.evaluation.report_hash,
                        "decision_hash": record.activation_decision.decision_hash,
                    },
                )
                connection.commit()
                return self._require_record(
                    connection,
                    family_id,
                    candidate_id,
                )
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
        reason: str,
        now: datetime | None = None,
    ) -> ModelVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, family_id)
                record = self._require_record(
                    connection,
                    family_id,
                    candidate_id,
                )
                if (
                    head["active_model_id"] == candidate_id
                    and record.status == ModelVersionStatus.ACTIVE
                ):
                    connection.commit()
                    return record
                self._check_revision(head, expected_active_revision)
                if record.status != ModelVersionStatus.AUTHORIZED:
                    raise ValueError(
                        "Only an authorized candidate may become active."
                    )
                if (
                    record.activation_decision is None
                    or not record.activation_decision.activate
                ):
                    raise ValueError(
                        "Candidate lacks a passing activation decision."
                    )
                if head["active_model_id"] != record.parent_model_id:
                    raise StaleModelRevision(
                        "Candidate parent is no longer the active model."
                    )
                self._validate_campaign(
                    record,
                    campaign,
                    require_authorized=True,
                )
                previous_model_id = head["active_model_id"]
                connection.execute(
                    "UPDATE model_versions SET status = ? "
                    "WHERE family_id = ? AND model_id = ?",
                    (
                        ModelVersionStatus.SUPERSEDED.value,
                        family_id,
                        previous_model_id,
                    ),
                )
                connection.execute(
                    "UPDATE model_versions SET status = ? "
                    "WHERE family_id = ? AND model_id = ?",
                    (
                        ModelVersionStatus.ACTIVE.value,
                        family_id,
                        candidate_id,
                    ),
                )
                changed = connection.execute(
                    "UPDATE model_heads SET active_model_id = ?, "
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
                    raise StaleModelRevision(
                        "Active model revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    event_type=ModelEventType.ACTIVATED,
                    family_id=family_id,
                    model_id=candidate_id,
                    from_model_id=previous_model_id,
                    to_model_id=candidate_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    metadata={
                        "campaign_id": campaign.campaign_id,
                        "report_hash": record.evaluation.report_hash,
                        "decision_hash": record.activation_decision.decision_hash,
                        "active_revision_before": expected_active_revision,
                    },
                )
                connection.commit()
                return self._require_record(
                    connection,
                    family_id,
                    candidate_id,
                )
            except Exception:
                connection.rollback()
                raise

    def rollback(
        self,
        family_id: str,
        *,
        from_model_id: str,
        to_model_id: str,
        expected_active_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> ModelVersionRecord:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, family_id)
                current = self._require_record(
                    connection,
                    family_id,
                    from_model_id,
                )
                target = self._require_record(
                    connection,
                    family_id,
                    to_model_id,
                )
                if (
                    head["active_model_id"] == to_model_id
                    and current.status == ModelVersionStatus.ROLLED_BACK
                    and target.status == ModelVersionStatus.ACTIVE
                ):
                    connection.commit()
                    return target
                self._check_revision(head, expected_active_revision)
                if head["active_model_id"] != from_model_id:
                    raise StaleModelRevision(
                        "Rollback source is no longer the active model."
                    )
                if current.status != ModelVersionStatus.ACTIVE:
                    raise ValueError("Rollback source is not active.")
                if target.status != ModelVersionStatus.SUPERSEDED:
                    raise ValueError(
                        "Rollback target must be a previously active superseded model."
                    )
                if current.parent_model_id != to_model_id:
                    raise ValueError(
                        "Rollback target is not the active candidate parent."
                    )
                connection.execute(
                    "UPDATE model_versions SET status = ? "
                    "WHERE family_id = ? AND model_id = ?",
                    (
                        ModelVersionStatus.ROLLED_BACK.value,
                        family_id,
                        from_model_id,
                    ),
                )
                connection.execute(
                    "UPDATE model_versions SET status = ? "
                    "WHERE family_id = ? AND model_id = ?",
                    (
                        ModelVersionStatus.ACTIVE.value,
                        family_id,
                        to_model_id,
                    ),
                )
                changed = connection.execute(
                    "UPDATE model_heads SET active_model_id = ?, "
                    "revision = revision + 1, updated_at = ? "
                    "WHERE family_id = ? AND revision = ?",
                    (
                        to_model_id,
                        now.isoformat(),
                        family_id,
                        expected_active_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleModelRevision(
                        "Active model revision changed concurrently."
                    )
                self._append_event(
                    connection,
                    event_type=ModelEventType.ROLLED_BACK,
                    family_id=family_id,
                    model_id=from_model_id,
                    from_model_id=from_model_id,
                    to_model_id=to_model_id,
                    reason=reason,
                    actor_id=actor_id,
                    created_at=now,
                    metadata={
                        "rolled_back_candidate": from_model_id,
                        "active_revision_before": expected_active_revision,
                    },
                )
                connection.commit()
                return self._require_record(
                    connection,
                    family_id,
                    to_model_id,
                )
            except Exception:
                connection.rollback()
                raise

    def active(self, family_id: str) -> ModelVersionRecord:
        with self._connection() as connection:
            head = self._require_head(connection, family_id)
            return self._require_record(
                connection,
                family_id,
                head["active_model_id"],
            )

    def active_revision(self, family_id: str) -> int:
        with self._connection() as connection:
            return int(self._require_head(connection, family_id)["revision"])

    def get(self, family_id: str, model_id: str) -> ModelVersionRecord:
        with self._connection() as connection:
            return self._require_record(connection, family_id, model_id)

    def list_versions(self, family_id: str) -> list[ModelVersionRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM model_versions WHERE family_id = ? "
                "ORDER BY created_at, model_id",
                (family_id,),
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

    def events(
        self,
        family_id: str | None = None,
    ) -> list[PersistentModelEvent]:
        with self._connection() as connection:
            if family_id is None:
                rows = connection.execute(
                    "SELECT * FROM model_audit_events ORDER BY sequence"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM model_audit_events "
                    "WHERE family_id = ? ORDER BY sequence",
                    (family_id,),
                ).fetchall()
            return [self._row_to_event(row) for row in rows]

    def checkpoint(self) -> ModelRegistryCheckpoint:
        events = self.events()
        return ModelRegistryCheckpoint(
            event_count=len(events),
            head_hash=events[-1].event_hash if events else _GENESIS_HASH,
        )

    def verify_state(self, family_id: str) -> bool:
        records = self.list_versions(family_id)
        if not records:
            raise ModelAuditIntegrityError(
                f"Model family has no immutable records: {family_id}"
            )
        active_records = [
            item for item in records if item.status == ModelVersionStatus.ACTIVE
        ]
        if len(active_records) != 1:
            raise ModelAuditIntegrityError(
                "Model family must contain exactly one ACTIVE record."
            )
        if self.active(family_id) != active_records[0]:
            raise ModelAuditIntegrityError(
                "Model active pointer differs from the ACTIVE immutable record."
            )
        by_id = {item.model_id: item for item in records}
        for record in records:
            if record.parent_model_id is not None:
                parent = by_id.get(record.parent_model_id)
                if parent is None:
                    raise ModelAuditIntegrityError(
                        "Model Registry contains an unknown parent reference."
                    )
                if parent.created_at > record.created_at:
                    raise ModelAuditIntegrityError(
                        "Candidate parent was created after its child."
                    )
        self.verify_audit()
        return True

    def verify_audit(
        self,
        checkpoint: ModelRegistryCheckpoint | None = None,
    ) -> bool:
        events = self.events()
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
            ):
                raise ModelAuditIntegrityError(
                    "Model audit sequence or hash chain is broken."
                )
            expected_hash = self._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                family_id=event.family_id,
                model_id=event.model_id,
                from_model_id=event.from_model_id,
                to_model_id=event.to_model_id,
                reason=event.reason,
                metadata=event.metadata,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise ModelAuditIntegrityError(
                    "Model audit event content was modified."
                )
            previous_hash = event.event_hash
        current = ModelRegistryCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint is not None and current != checkpoint:
            raise ModelAuditIntegrityError(
                "Model audit does not match the external checkpoint."
            )
        return True

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        record: ModelVersionRecord,
    ) -> None:
        connection.execute(
            "INSERT INTO model_versions "
            "(family_id, model_id, manifest_json, parent_model_id, status, "
            "training_receipt_json, training_package_hash, evaluation_json, "
            "activation_decision_json, activation_campaign_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.family_id,
                record.model_id,
                self._json(record.manifest.model_dump(mode="json")),
                record.parent_model_id,
                record.status.value,
                (
                    self._json(
                        record.training_receipt.model_dump(mode="json")
                    )
                    if record.training_receipt
                    else None
                ),
                record.training_package_hash,
                (
                    self._json(record.evaluation.model_dump(mode="json"))
                    if record.evaluation
                    else None
                ),
                (
                    self._json(
                        record.activation_decision.model_dump(mode="json")
                    )
                    if record.activation_decision
                    else None
                ),
                record.activation_campaign_id,
                record.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _validate_report_decision(
        record: ModelVersionRecord,
        report: ModelCandidateEvaluationReport,
        decision: ModelActivationDecision,
    ) -> None:
        if not isinstance(record.manifest, ExternalModelCandidateManifest):
            raise ValueError(
                "Initial models cannot receive candidate evaluation."
            )
        if (
            report.family_id != record.family_id
            or report.candidate_id != record.model_id
            or report.base_model_id != record.parent_model_id
            or report.candidate_manifest_hash != record.manifest.manifest_hash
        ):
            raise ValueError(
                "Evaluation report does not match the candidate record."
            )
        if record.training_receipt is None:
            raise ValueError("Candidate training receipt is missing.")
        if report.trainer_id != record.training_receipt.trainer_id:
            raise ValueError(
                "Evaluation report trainer differs from the admission receipt."
            )
        if (
            decision.family_id != record.family_id
            or decision.candidate_id != record.model_id
            or decision.base_model_id != record.parent_model_id
            or decision.evaluation_report_hash != report.report_hash
        ):
            raise ValueError(
                "Activation decision does not match the evaluation report."
            )

    @staticmethod
    def _validate_campaign(
        record: ModelVersionRecord,
        campaign: CampaignRecord,
        *,
        require_authorized: bool,
    ) -> None:
        if campaign.campaign_type != CampaignType.MODEL_ACTIVATION:
            raise ValueError(
                "Candidate requires a Model Activation Campaign."
            )
        if require_authorized and campaign.state != CampaignState.AUTHORIZED:
            raise ValueError(
                "Model Activation Campaign is not AUTHORIZED."
            )
        if record.activation_campaign_id != campaign.campaign_id:
            raise ValueError(
                "Candidate is bound to another activation Campaign."
            )
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "model_activation_candidate":
            raise ValueError(
                "Campaign does not contain a model activation candidate."
            )
        stored_manifest = ExternalModelCandidateManifest.model_validate(
            payload.get("candidate_manifest")
        )
        stored_receipt = ExternalTrainingReceipt.model_validate(
            payload.get("training_receipt")
        )
        stored_report = ModelCandidateEvaluationReport.model_validate(
            payload.get("evaluation_report")
        )
        stored_decision = ModelActivationDecision.model_validate(
            payload.get("activation_decision")
        )
        if (
            stored_manifest != record.manifest
            or stored_receipt != record.training_receipt
            or payload.get("training_package_hash")
            != record.training_package_hash
            or stored_report != record.evaluation
            or stored_decision != record.activation_decision
        ):
            raise ValueError(
                "Activation Campaign evidence differs from the registry candidate."
            )

    @staticmethod
    def _check_revision(
        head: sqlite3.Row,
        expected_revision: int,
    ) -> None:
        if int(head["revision"]) != expected_revision:
            raise StaleModelRevision(
                f"Expected active revision {expected_revision}, "
                f"found {head['revision']}."
            )

    @staticmethod
    def _head_row(
        connection: sqlite3.Connection,
        family_id: str,
    ):
        return connection.execute(
            "SELECT * FROM model_heads WHERE family_id = ?",
            (family_id,),
        ).fetchone()

    def _require_head(
        self,
        connection: sqlite3.Connection,
        family_id: str,
    ):
        row = self._head_row(connection, family_id)
        if row is None:
            raise KeyError(f"Unknown model family: {family_id}")
        return row

    @staticmethod
    def _version_row(
        connection: sqlite3.Connection,
        family_id: str,
        model_id: str,
    ):
        return connection.execute(
            "SELECT * FROM model_versions "
            "WHERE family_id = ? AND model_id = ?",
            (family_id, model_id),
        ).fetchone()

    def _require_record(
        self,
        connection: sqlite3.Connection,
        family_id: str,
        model_id: str,
    ) -> ModelVersionRecord:
        row = self._version_row(connection, family_id, model_id)
        if row is None:
            raise KeyError(
                f"Unknown model version: {family_id}/{model_id}"
            )
        return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ModelVersionRecord:
        manifest = _MANIFEST_ADAPTER.validate_python(
            json.loads(row["manifest_json"])
        )
        receipt = (
            ExternalTrainingReceipt.model_validate(
                json.loads(row["training_receipt_json"])
            )
            if row["training_receipt_json"]
            else None
        )
        evaluation = (
            ModelCandidateEvaluationReport.model_validate(
                json.loads(row["evaluation_json"])
            )
            if row["evaluation_json"]
            else None
        )
        decision = (
            ModelActivationDecision.model_validate(
                json.loads(row["activation_decision_json"])
            )
            if row["activation_decision_json"]
            else None
        )
        return ModelVersionRecord(
            family_id=row["family_id"],
            model_id=row["model_id"],
            manifest=manifest,
            parent_model_id=row["parent_model_id"],
            status=ModelVersionStatus(row["status"]),
            training_receipt=receipt,
            training_package_hash=row["training_package_hash"],
            evaluation=evaluation,
            activation_decision=decision,
            activation_campaign_id=row["activation_campaign_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: ModelEventType,
        family_id: str,
        model_id: str,
        reason: str,
        actor_id: str,
        created_at: datetime,
        from_model_id: str | None = None,
        to_model_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT sequence, event_hash FROM model_audit_events "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(last["sequence"]) + 1 if last else 1
        previous_hash = last["event_hash"] if last else _GENESIS_HASH
        event_id = f"model-event:{uuid.uuid4()}"
        metadata = metadata or {}
        event_hash = self._event_hash(
            sequence=sequence,
            event_id=event_id,
            event_type=event_type,
            family_id=family_id,
            model_id=model_id,
            from_model_id=from_model_id,
            to_model_id=to_model_id,
            reason=reason,
            metadata=metadata,
            actor_id=actor_id,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        connection.execute(
            "INSERT INTO model_audit_events "
            "(sequence, event_id, event_type, family_id, model_id, "
            "from_model_id, to_model_id, reason, metadata_json, actor_id, "
            "created_at, previous_hash, event_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                event_id,
                event_type.value,
                family_id,
                model_id,
                from_model_id,
                to_model_id,
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
        event_type: ModelEventType,
        family_id: str,
        model_id: str,
        from_model_id: str | None,
        to_model_id: str | None,
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
                "model_id": model_id,
                "from_model_id": from_model_id,
                "to_model_id": to_model_id,
                "reason": reason,
                "metadata": metadata,
                "actor_id": actor_id,
                "created_at": created_at.isoformat(),
                "previous_hash": previous_hash,
            }
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> PersistentModelEvent:
        return PersistentModelEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            event_type=ModelEventType(row["event_type"]),
            family_id=row["family_id"],
            model_id=row["model_id"],
            from_model_id=row["from_model_id"],
            to_model_id=row["to_model_id"],
            reason=row["reason"],
            metadata=json.loads(row["metadata_json"]),
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
    "ModelAuditIntegrityError",
    "ModelRegistryConflictError",
    "SQLiteModelRegistry",
    "StaleModelRevision",
]
