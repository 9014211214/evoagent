from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.campaigns.models import (
    ApprovalDecision,
    CampaignApproval,
    CampaignAuditEvent,
    CampaignCheckpoint,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
)
from evoagent.campaigns.repository import (
    SQLiteCampaignRepository,
    fingerprint_payload,
)
from evoagent.model_registry.models import (
    ExternalModelCandidateManifest,
    ExternalTrainingReceipt,
    InitialModelManifest,
    ModelActivationDecision,
    ModelArtifactFormat,
    ModelCandidateEvaluationReport,
    ModelEvaluationSuite,
    ModelEventType,
    ModelRegistryCheckpoint,
    ModelVersionRecord,
    ModelVersionStatus,
    PersistentModelEvent,
    canonical_sha256,
    validate_safe_content,
)
from evoagent.model_registry.sqlite_registry import SQLiteModelRegistry


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENESIS_HASH = "0" * 64


class ModelAdmissionPackageError(ValueError):
    pass


class ModelAdmissionPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-model-admission-package-v1"] = (
        "evoagent-model-admission-package-v1"
    )
    run_id: str
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    training_intent_package_hash: str = Field(pattern=_SHA256_PATTERN)
    initial_manifest: InitialModelManifest
    candidate_manifest: ExternalModelCandidateManifest
    training_receipt: ExternalTrainingReceipt
    evaluation_suite: ModelEvaluationSuite
    evaluation_report: ModelCandidateEvaluationReport
    activation_decision: ModelActivationDecision
    activation_campaign: CampaignRecord
    approvals: tuple[CampaignApproval, ...]
    campaign_events: tuple[CampaignAuditEvent, ...]
    campaign_checkpoint: CampaignCheckpoint
    model_records: tuple[ModelVersionRecord, ...]
    model_events: tuple[PersistentModelEvent, ...]
    model_registry_checkpoint: ModelRegistryCheckpoint
    active_model_after_activation: str
    active_revision_after_activation: int = Field(ge=1)
    active_model_after_rollback: str
    active_revision_after_rollback: int = Field(ge=2)
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    checkpoint_downloaded: Literal[False] = False
    candidate_weights_loaded: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    external_execution_performed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "Model admission package time must include a timezone."
            )
        return value


class ModelAdmissionPackageManager:
    def build(
        self,
        *,
        run_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        training_intent_package_hash: str,
        initial_manifest: InitialModelManifest,
        candidate_manifest: ExternalModelCandidateManifest,
        training_receipt: ExternalTrainingReceipt,
        evaluation_suite: ModelEvaluationSuite,
        evaluation_report: ModelCandidateEvaluationReport,
        activation_decision: ModelActivationDecision,
        activation_campaign: CampaignRecord,
        approvals: tuple[CampaignApproval, ...],
        campaign_events: tuple[CampaignAuditEvent, ...],
        campaign_checkpoint: CampaignCheckpoint,
        model_records: tuple[ModelVersionRecord, ...],
        model_events: tuple[PersistentModelEvent, ...],
        model_registry_checkpoint: ModelRegistryCheckpoint,
        active_model_after_activation: str,
        active_revision_after_activation: int,
        active_model_after_rollback: str,
        active_revision_after_rollback: int,
    ) -> ModelAdmissionPackageManifest:
        provisional = ModelAdmissionPackageManifest(
            run_id=run_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            training_intent_package_hash=training_intent_package_hash,
            initial_manifest=initial_manifest,
            candidate_manifest=candidate_manifest,
            training_receipt=training_receipt,
            evaluation_suite=evaluation_suite,
            evaluation_report=evaluation_report,
            activation_decision=activation_decision,
            activation_campaign=activation_campaign,
            approvals=approvals,
            campaign_events=campaign_events,
            campaign_checkpoint=campaign_checkpoint,
            model_records=model_records,
            model_events=model_events,
            model_registry_checkpoint=model_registry_checkpoint,
            active_model_after_activation=active_model_after_activation,
            active_revision_after_activation=active_revision_after_activation,
            active_model_after_rollback=active_model_after_rollback,
            active_revision_after_rollback=active_revision_after_rollback,
            package_hash="0" * 64,
        )
        payload = provisional.model_dump(
            mode="json",
            exclude={"package_hash"},
        )
        validate_safe_content(payload)
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: ModelAdmissionPackageManifest) -> bool:
        payload = manifest.model_dump(
            mode="json",
            exclude={"package_hash"},
        )
        validate_safe_content(payload)
        if manifest.package_hash != canonical_sha256(payload):
            raise ModelAdmissionPackageError(
                "Model admission package hash mismatch."
            )

        initial = manifest.initial_manifest
        candidate = manifest.candidate_manifest
        receipt = manifest.training_receipt
        suite = manifest.evaluation_suite
        report = manifest.evaluation_report
        decision = manifest.activation_decision
        campaign = manifest.activation_campaign

        if initial.family_id != candidate.family_id:
            raise ModelAdmissionPackageError(
                "Initial and candidate manifests use different Model families."
            )
        if initial.model_id != candidate.base_model_id:
            raise ModelAdmissionPackageError(
                "Candidate base model differs from the initial manifest."
            )
        if (
            initial.source_commit != manifest.source_commit
            or candidate.source_commit != manifest.source_commit
        ):
            raise ModelAdmissionPackageError(
                "Model manifest source commit differs from the package."
            )
        if receipt.candidate_id != candidate.candidate_id:
            raise ModelAdmissionPackageError(
                "Training receipt does not match the candidate manifest."
            )
        if receipt.artifact_sha256 != candidate.artifact_sha256:
            raise ModelAdmissionPackageError(
                "Training receipt artifact hash differs from the candidate."
            )
        if (
            receipt.authorization_reference_hash
            != candidate.training_authorization.reference_hash
        ):
            raise ModelAdmissionPackageError(
                "Training receipt is bound to another authorization reference."
            )
        if (
            receipt.training_intent_campaign_id
            != candidate.training_intent_campaign_id
            or receipt.base_model_id != candidate.base_model_id
            or receipt.training_method != candidate.training_method
            or receipt.evidence_manifest_hash
            != candidate.evidence_manifest_hash
            or receipt.held_out_task_ids != candidate.held_out_task_ids
        ):
            raise ModelAdmissionPackageError(
                "Training receipt differs from the candidate training contract."
            )
        if candidate.created_at < receipt.completed_at:
            raise ModelAdmissionPackageError(
                "Candidate manifest predates receipt completion."
            )
        if report.family_id != candidate.family_id:
            raise ModelAdmissionPackageError(
                "Evaluation report Model family differs from the candidate."
            )
        if report.candidate_id != candidate.candidate_id:
            raise ModelAdmissionPackageError(
                "Evaluation report does not match the candidate."
            )
        if report.base_model_id != candidate.base_model_id:
            raise ModelAdmissionPackageError(
                "Evaluation report base model differs from the candidate."
            )
        if report.candidate_manifest_hash != candidate.manifest_hash:
            raise ModelAdmissionPackageError(
                "Evaluation report is bound to another candidate manifest."
            )
        if report.trainer_id != receipt.trainer_id:
            raise ModelAdmissionPackageError(
                "Evaluation report trainer differs from the receipt."
            )
        if report.suite_hash != suite.suite_hash:
            raise ModelAdmissionPackageError(
                "Evaluation report is bound to another frozen suite."
            )
        held_out_ids = tuple(task.task_id for task in suite.held_out_tasks)
        if held_out_ids != candidate.held_out_task_ids:
            raise ModelAdmissionPackageError(
                "Evaluation suite held-out Tasks differ from the candidate."
            )
        self._verify_report_suite_binding(suite, report)
        if decision.evaluation_report_hash != report.report_hash:
            raise ModelAdmissionPackageError(
                "Activation decision is bound to another evaluation report."
            )
        if (
            decision.family_id != candidate.family_id
            or decision.base_model_id != candidate.base_model_id
            or decision.candidate_id != candidate.candidate_id
        ):
            raise ModelAdmissionPackageError(
                "Activation decision target differs from the candidate."
            )
        from evoagent.model_registry.decision import ModelActivationPolicy

        expected_decision = ModelActivationPolicy().decide(
            report,
            thresholds=decision.thresholds,
            decided_by=decision.decided_by,
            decided_at=decision.decided_at,
        )
        if expected_decision != decision:
            raise ModelAdmissionPackageError(
                "Activation decision does not follow the declared thresholds."
            )
        if not decision.activate:
            raise ModelAdmissionPackageError(
                "Admission package requires a passing activation decision."
            )
        if decision.decided_by in {
            receipt.trainer_id,
            report.evaluator_id,
        }:
            raise ModelAdmissionPackageError(
                "Trainer or evaluator cannot issue the activation decision."
            )
        self._verify_adapter_binding(candidate, report)
        self._verify_campaign(
            manifest,
            campaign,
            candidate,
            receipt,
            report,
            decision,
        )
        self._verify_approvals(manifest)
        self._verify_records(manifest)
        self._verify_campaign_events(manifest)
        self._verify_model_events(manifest)

        if (
            manifest.active_model_after_activation
            != candidate.candidate_id
        ):
            raise ModelAdmissionPackageError(
                "Activation checkpoint does not identify the candidate."
            )
        if manifest.active_revision_after_activation != 1:
            raise ModelAdmissionPackageError(
                "First explicit activation must advance revision from 0 to 1."
            )
        if (
            manifest.active_model_after_rollback
            != initial.model_id
        ):
            raise ModelAdmissionPackageError(
                "Rollback did not restore the initial active model."
            )
        if manifest.active_revision_after_rollback != 2:
            raise ModelAdmissionPackageError(
                "First rollback must advance active revision from 1 to 2."
            )
        return True

    @staticmethod
    def _verify_report_suite_binding(
        suite: ModelEvaluationSuite,
        report: ModelCandidateEvaluationReport,
    ) -> None:
        expected = {}
        for suite_name, tasks in (
            ("held_out", suite.held_out_tasks),
            ("replay", suite.replay_tasks),
            ("retention", suite.retention_tasks),
            ("safety", suite.safety_tasks),
        ):
            for task in tasks:
                expected[task.task_id] = (
                    suite_name,
                    canonical_sha256(task.model_dump(mode="json")),
                )
        actual = {
            item.task_id: (item.suite, item.task_hash)
            for item in report.task_results
        }
        if actual != expected:
            raise ModelAdmissionPackageError(
                "Evaluation results differ from the frozen suite Tasks."
            )

    @staticmethod
    def _verify_adapter_binding(
        candidate: ExternalModelCandidateManifest,
        report: ModelCandidateEvaluationReport,
    ) -> None:
        synthetic = (
            candidate.artifact_format
            == ModelArtifactFormat.SYNTHETIC_POLICY
        )
        expected_hash = canonical_sha256(
            {
                "adapter_id": report.adapter_id,
                "candidate_id": candidate.candidate_id,
                "candidate_manifest_hash": candidate.manifest_hash,
                "profile": (
                    candidate.synthetic_profile.value
                    if candidate.synthetic_profile is not None
                    else None
                ),
                "synthetic": synthetic,
            }
        )
        if report.adapter_hash != expected_hash:
            raise ModelAdmissionPackageError(
                "Evaluation adapter hash does not match the candidate."
            )

    @staticmethod
    def _verify_campaign(
        manifest: ModelAdmissionPackageManifest,
        campaign: CampaignRecord,
        candidate: ExternalModelCandidateManifest,
        receipt: ExternalTrainingReceipt,
        report: ModelCandidateEvaluationReport,
        decision: ModelActivationDecision,
    ) -> None:
        if campaign.campaign_type != CampaignType.MODEL_ACTIVATION:
            raise ModelAdmissionPackageError(
                "Admission package contains the wrong Campaign type."
            )
        if campaign.risk != CampaignRisk.HIGH:
            raise ModelAdmissionPackageError(
                "Model activation Campaign must remain HIGH risk."
            )
        if campaign.state != CampaignState.COMPLETED:
            raise ModelAdmissionPackageError(
                "Admission package requires a completed activation Campaign."
            )
        if campaign.generated_by != receipt.trainer_id:
            raise ModelAdmissionPackageError(
                "Activation Campaign generator differs from the trainer."
            )
        expected_target = (
            f"model-activation:{candidate.family_id}:{candidate.candidate_id}"
        )
        if campaign.target_key != expected_target:
            raise ModelAdmissionPackageError(
                "Activation Campaign target differs from the candidate."
            )
        expected_fingerprint = fingerprint_payload(
            {
                "candidate_manifest_hash": candidate.manifest_hash,
                "training_receipt_hash": receipt.receipt_hash,
                "training_package_hash": (
                    manifest.training_intent_package_hash
                ),
                "evaluation_report_hash": report.report_hash,
                "activation_decision_hash": decision.decision_hash,
            }
        )
        if campaign.fingerprint != expected_fingerprint:
            raise ModelAdmissionPackageError(
                "Activation Campaign fingerprint differs from exact evidence."
            )
        if (
            campaign.candidate_ref
            != f"model-activation:{candidate.candidate_id}"
        ):
            raise ModelAdmissionPackageError(
                "Activation Campaign candidate reference differs."
            )
        expected_metadata = {
            "family_id": candidate.family_id,
            "candidate_id": candidate.candidate_id,
            "base_model_id": candidate.base_model_id,
            "adapter_id": report.adapter_id,
            "adapter_hash": report.adapter_hash,
            "evaluator_id": report.evaluator_id,
            "decision_actor_id": decision.decided_by,
            "training_executed_by_evoagent": False,
        }
        if campaign.metadata != expected_metadata:
            raise ModelAdmissionPackageError(
                "Activation Campaign metadata differs from exact evidence."
            )
        if campaign.required_approvals != 2:
            raise ModelAdmissionPackageError(
                "High-risk model activation requires exactly two approvals."
            )
        campaign_payload = campaign.artifact_payload or {}
        if campaign_payload.get("kind") != "model_activation_candidate":
            raise ModelAdmissionPackageError(
                "Activation Campaign lacks exact candidate evidence."
            )
        if (
            ExternalModelCandidateManifest.model_validate(
                campaign_payload.get("candidate_manifest")
            )
            != candidate
            or ExternalTrainingReceipt.model_validate(
                campaign_payload.get("training_receipt")
            )
            != receipt
            or ModelCandidateEvaluationReport.model_validate(
                campaign_payload.get("evaluation_report")
            )
            != report
            or ModelActivationDecision.model_validate(
                campaign_payload.get("activation_decision")
            )
            != decision
            or campaign_payload.get("training_package_hash")
            != manifest.training_intent_package_hash
        ):
            raise ModelAdmissionPackageError(
                "Activation Campaign evidence differs from packaged evidence."
            )

    @staticmethod
    def _verify_approvals(
        manifest: ModelAdmissionPackageManifest,
    ) -> None:
        approvals = [
            item
            for item in manifest.approvals
            if item.decision == ApprovalDecision.APPROVE
        ]
        if len(approvals) != len(manifest.approvals):
            raise ModelAdmissionPackageError(
                "Completed activation package cannot contain rejected approvals."
            )
        approvers = [item.actor_id for item in approvals]
        if len(approvals) != manifest.activation_campaign.required_approvals:
            raise ModelAdmissionPackageError(
                "Packaged approvals do not match the Campaign threshold."
            )
        if len(set(approvers)) != len(approvers):
            raise ModelAdmissionPackageError(
                "Model activation approvals must use distinct actors."
            )
        prohibited = {
            manifest.training_receipt.trainer_id,
            manifest.evaluation_report.evaluator_id,
            manifest.activation_decision.decided_by,
        }
        if prohibited & set(approvers):
            raise ModelAdmissionPackageError(
                "Trainer, evaluator, or decision actor cannot approve activation."
            )
        if any(
            item.campaign_id
            != manifest.activation_campaign.campaign_id
            for item in approvals
        ):
            raise ModelAdmissionPackageError(
                "Approval is bound to another Campaign."
            )

    @staticmethod
    def _verify_records(
        manifest: ModelAdmissionPackageManifest,
    ) -> None:
        candidate = manifest.candidate_manifest
        receipt = manifest.training_receipt
        report = manifest.evaluation_report
        decision = manifest.activation_decision
        campaign = manifest.activation_campaign
        by_model = {
            record.model_id: record
            for record in manifest.model_records
        }
        if len(by_model) != len(manifest.model_records):
            raise ModelAdmissionPackageError(
                "Model Registry package contains duplicate model IDs."
            )
        if set(by_model) != {
            manifest.initial_manifest.model_id,
            candidate.candidate_id,
        }:
            raise ModelAdmissionPackageError(
                "Admission package must contain exactly initial and candidate models."
            )
        initial_record = by_model[manifest.initial_manifest.model_id]
        candidate_record = by_model[candidate.candidate_id]
        if (
            initial_record.manifest != manifest.initial_manifest
            or initial_record.status != ModelVersionStatus.ACTIVE
            or candidate_record.manifest != candidate
            or candidate_record.training_receipt != receipt
            or candidate_record.training_package_hash
            != manifest.training_intent_package_hash
            or candidate_record.evaluation != report
            or candidate_record.activation_decision != decision
            or candidate_record.activation_campaign_id
            != campaign.campaign_id
            or candidate_record.status
            != ModelVersionStatus.ROLLED_BACK
        ):
            raise ModelAdmissionPackageError(
                "Final Registry records differ from activation/rollback lifecycle."
            )

    @staticmethod
    def _verify_campaign_events(
        manifest: ModelAdmissionPackageManifest,
    ) -> None:
        events = manifest.campaign_events
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
            ):
                raise ModelAdmissionPackageError(
                    "Packaged Campaign audit chain is broken."
                )
            expected_hash = SQLiteCampaignRepository._event_hash(
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
                raise ModelAdmissionPackageError(
                    "Packaged Campaign event was modified."
                )
            previous_hash = event.event_hash
        checkpoint = CampaignCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint != manifest.campaign_checkpoint:
            raise ModelAdmissionPackageError(
                "Packaged Campaign events do not match the checkpoint."
            )
        event_types = tuple(event.event_type for event in events)
        required = (
            "campaign_created",
            "candidate_attached",
            "campaign_transitioned",
            "campaign_transitioned",
            "approval_recorded",
            "approval_recorded",
            "campaign_transitioned",
        )
        if event_types != required:
            raise ModelAdmissionPackageError(
                "Activation Campaign lifecycle events are missing or reordered."
            )
        if any(
            event.campaign_id
            != manifest.activation_campaign.campaign_id
            for event in events
        ):
            raise ModelAdmissionPackageError(
                "Campaign audit contains another Campaign."
            )
        transitions = (
            events[2].payload.get("from_state"),
            events[2].payload.get("to_state"),
            events[3].payload.get("from_state"),
            events[3].payload.get("to_state"),
            events[6].payload.get("from_state"),
            events[6].payload.get("to_state"),
        )
        if transitions != (
            CampaignState.CANDIDATE_READY.value,
            CampaignState.EVALUATION_PENDING.value,
            CampaignState.EVALUATION_PENDING.value,
            CampaignState.APPROVAL_PENDING.value,
            CampaignState.AUTHORIZED.value,
            CampaignState.COMPLETED.value,
        ):
            raise ModelAdmissionPackageError(
                "Activation Campaign transition evidence differs."
            )
        approval_actors = {
            event.actor_id
            for event in events
            if event.event_type == "approval_recorded"
        }
        if approval_actors != {
            item.actor_id for item in manifest.approvals
        }:
            raise ModelAdmissionPackageError(
                "Approval audit actors differ from packaged approvals."
            )

    @staticmethod
    def _verify_model_events(
        manifest: ModelAdmissionPackageManifest,
    ) -> None:
        events = manifest.model_events
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
            ):
                raise ModelAdmissionPackageError(
                    "Packaged Model Registry event chain is broken."
                )
            expected_hash = SQLiteModelRegistry._event_hash(
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
            if event.event_hash != expected_hash:
                raise ModelAdmissionPackageError(
                    "Packaged Model Registry event was modified."
                )
            previous_hash = event.event_hash
        checkpoint = ModelRegistryCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint != manifest.model_registry_checkpoint:
            raise ModelAdmissionPackageError(
                "Packaged Model events do not match the checkpoint."
            )
        event_types = tuple(event.event_type for event in events)
        required = (
            ModelEventType.REGISTERED,
            ModelEventType.CANDIDATE_ADMITTED,
            ModelEventType.EVALUATED,
            ModelEventType.AUTHORIZED,
            ModelEventType.ACTIVATED,
            ModelEventType.ROLLED_BACK,
        )
        if event_types != required:
            raise ModelAdmissionPackageError(
                "Model lifecycle events are missing, duplicated, or reordered."
            )
        initial_id = manifest.initial_manifest.model_id
        candidate_id = manifest.candidate_manifest.candidate_id
        if tuple(event.model_id for event in events) != (
            initial_id,
            candidate_id,
            candidate_id,
            candidate_id,
            candidate_id,
            candidate_id,
        ):
            raise ModelAdmissionPackageError(
                "Model lifecycle event target IDs differ."
            )
        if tuple(
            (event.from_model_id, event.to_model_id)
            for event in events
        ) != (
            (None, None),
            (initial_id, candidate_id),
            (None, None),
            (None, None),
            (initial_id, candidate_id),
            (candidate_id, initial_id),
        ):
            raise ModelAdmissionPackageError(
                "Model lifecycle event pointer transitions differ."
            )
        if any(
            event.family_id != manifest.candidate_manifest.family_id
            for event in events
        ):
            raise ModelAdmissionPackageError(
                "Model audit contains another family."
            )
        if events[0].metadata.get("manifest_hash") != (
            manifest.initial_manifest.manifest_hash
        ):
            raise ModelAdmissionPackageError(
                "Initial registration event manifest hash differs."
            )
        if (
            events[1].metadata.get("manifest_hash")
            != manifest.candidate_manifest.manifest_hash
            or events[1].metadata.get("receipt_hash")
            != manifest.training_receipt.receipt_hash
            or events[1].metadata.get("training_package_hash")
            != manifest.training_intent_package_hash
        ):
            raise ModelAdmissionPackageError(
                "Candidate admission event hashes differ."
            )
        for index in (2, 3, 4):
            if (
                events[index].metadata.get("report_hash")
                != manifest.evaluation_report.report_hash
                or events[index].metadata.get("decision_hash")
                != manifest.activation_decision.decision_hash
            ):
                raise ModelAdmissionPackageError(
                    "Model evaluation or activation event hashes differ."
                )
        if (
            events[2].metadata.get("activation_campaign_id")
            != manifest.activation_campaign.campaign_id
            or events[3].metadata.get("campaign_id")
            != manifest.activation_campaign.campaign_id
            or events[4].metadata.get("campaign_id")
            != manifest.activation_campaign.campaign_id
            or events[2].metadata.get("activate") is not True
        ):
            raise ModelAdmissionPackageError(
                "Model activation Campaign event binding differs."
            )
        if events[4].metadata.get("active_revision_before") != 0:
            raise ModelAdmissionPackageError(
                "Activation event did not begin at active revision 0."
            )
        if (
            events[5].metadata.get("rolled_back_candidate")
            != candidate_id
            or events[5].metadata.get("active_revision_before") != 1
        ):
            raise ModelAdmissionPackageError(
                "Rollback event revision or candidate differs."
            )

    def export_file(
        self,
        manifest: ModelAdmissionPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ModelAdmissionPackageError(
                "Model admission package output must not be a symlink."
            )
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(manifest.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def load_file(
        self,
        path: str | Path,
    ) -> ModelAdmissionPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ModelAdmissionPackageError(
                "Model admission package must be a regular non-symlink file."
            )
        try:
            manifest = ModelAdmissionPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ModelAdmissionPackageError(
                "Model admission package is invalid."
            ) from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "ModelAdmissionPackageError",
    "ModelAdmissionPackageManager",
    "ModelAdmissionPackageManifest",
]
