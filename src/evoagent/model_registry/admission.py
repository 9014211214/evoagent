from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from evoagent.model_registry.builders import build_training_authorization_scope
from evoagent.model_registry.models import (
    ExternalModelCandidateManifest,
    ExternalTrainingReceipt,
    ModelArtifactFormat,
    ModelCandidateValidationError,
    ModelVersionRecord,
    TrainingAuthorizationReference,
    TrainingReceiptKind,
    canonical_sha256,
)
from evoagent.model_registry.sqlite_registry import (
    ModelRegistryConflictError,
    SQLiteModelRegistry,
)
from evoagent.training.models import TrainingBudget
from evoagent.training.package import (
    ModelEvolutionPackageManager,
    ModelEvolutionPackageManifest,
)


class TrainingAuthorizationVerifier(Protocol):
    def verify(self, reference: TrainingAuthorizationReference) -> bool:
        ...


class RejectUnverifiedTrainingAuthorization:
    def verify(self, reference: TrainingAuthorizationReference) -> bool:
        del reference
        return False


class AllowlistedTrainingAuthorizationVerifier:
    """Verify only exact externally anchored reference hashes supplied by the caller."""

    def __init__(self, reference_hashes: set[str] | frozenset[str] | tuple[str, ...]):
        self.reference_hashes = frozenset(reference_hashes)

    def verify(self, reference: TrainingAuthorizationReference) -> bool:
        return reference.reference_hash in self.reference_hashes


class ModelCandidateAdmissionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: ModelVersionRecord
    reused: bool


class ModelCandidateAdmissionService:
    """Validate metadata-only external candidates without approving activation."""

    def __init__(
        self,
        *,
        registry: SQLiteModelRegistry,
        authorization_verifier: TrainingAuthorizationVerifier | None = None,
        allow_synthetic_fixture: bool = False,
    ):
        self.registry = registry
        self.authorization_verifier = (
            authorization_verifier or RejectUnverifiedTrainingAuthorization()
        )
        self.allow_synthetic_fixture = allow_synthetic_fixture

    def admit(
        self,
        *,
        package: ModelEvolutionPackageManifest,
        candidate: ExternalModelCandidateManifest,
        receipt: ExternalTrainingReceipt,
    ) -> ModelCandidateAdmissionResult:
        ModelEvolutionPackageManager().verify(package)
        active = self.registry.active(candidate.family_id)
        self._validate_binding(
            package=package,
            candidate=candidate,
            receipt=receipt,
            active_model_id=active.model_id,
        )
        try:
            record = self.registry.add_candidate(
                candidate,
                receipt,
                parent_model_id=active.model_id,
                training_package_hash=package.package_hash,
                reason=(
                    "External candidate metadata and training receipt matched the "
                    "governed training-intent package."
                ),
                actor_id="model-candidate-admission",
            )
            return ModelCandidateAdmissionResult(record=record, reused=False)
        except ModelRegistryConflictError:
            record = self.registry.get(candidate.family_id, candidate.candidate_id)
            if (
                record.manifest != candidate
                or record.training_receipt != receipt
                or record.training_package_hash != package.package_hash
            ):
                raise ModelCandidateValidationError(
                    "Existing candidate differs from the supplied admission evidence."
                )
            return ModelCandidateAdmissionResult(record=record, reused=True)

    def _validate_binding(
        self,
        *,
        package: ModelEvolutionPackageManifest,
        candidate: ExternalModelCandidateManifest,
        receipt: ExternalTrainingReceipt,
        active_model_id: str,
    ) -> None:
        ticket = package.ticket
        planned_candidate = package.candidate
        held_out_ids = tuple(task.task_id for task in package.held_out_tasks)

        if not self.authorization_verifier.verify(candidate.training_authorization):
            raise ModelCandidateValidationError(
                "Training authorization reference was not externally allowlisted."
            )
        expected_authorization_hash = canonical_sha256(
            build_training_authorization_scope(
                package,
                candidate_id=candidate.candidate_id,
            )
        )
        if (
            candidate.training_authorization.authorization_hash
            != expected_authorization_hash
        ):
            raise ModelCandidateValidationError(
                "Training authorization scope differs from the governed candidate contract."
            )
        if receipt.receipt_kind == TrainingReceiptKind.SYNTHETIC_LIFECYCLE_FIXTURE:
            if not self.allow_synthetic_fixture:
                raise ModelCandidateValidationError(
                    "Synthetic training receipts are disabled outside the controlled lab."
                )
            if (
                candidate.artifact_format != ModelArtifactFormat.SYNTHETIC_POLICY
                or candidate.synthetic_profile is None
            ):
                raise ModelCandidateValidationError(
                    "Synthetic receipts require an explicitly synthetic candidate."
                )
        elif (
            candidate.artifact_format == ModelArtifactFormat.SYNTHETIC_POLICY
            or candidate.synthetic_profile is not None
        ):
            raise ModelCandidateValidationError(
                "A real external training receipt cannot admit a synthetic candidate."
            )

        expected = {
            "base_model_id": ticket.base_model_id,
            "training_method": planned_candidate.method,
            "evidence_manifest_hash": package.dataset.manifest_hash,
            "held_out_task_ids": held_out_ids,
            "training_intent_campaign_id": package.campaign.campaign_id,
        }
        for field_name, expected_value in expected.items():
            if getattr(candidate, field_name) != expected_value:
                raise ModelCandidateValidationError(
                    f"Candidate {field_name} differs from the governed training package."
                )
            if getattr(receipt, field_name) != expected_value:
                raise ModelCandidateValidationError(
                    f"Training receipt {field_name} differs from the governed package."
                )

        if active_model_id != ticket.base_model_id:
            raise ModelCandidateValidationError(
                "The governed package base model is no longer active."
            )
        if candidate.source_commit != package.source_commit:
            raise ModelCandidateValidationError(
                "Candidate source commit differs from the governed package."
            )
        if receipt.candidate_id != candidate.candidate_id:
            raise ModelCandidateValidationError(
                "Training receipt candidate ID differs from the candidate manifest."
            )
        if receipt.trainer_id != candidate.generated_by:
            raise ModelCandidateValidationError(
                "Candidate generator differs from the external trainer identity."
            )
        if receipt.authorization_reference_hash != (
            candidate.training_authorization.reference_hash
        ):
            raise ModelCandidateValidationError(
                "Training receipt is bound to another authorization reference."
            )
        if receipt.artifact_sha256 != candidate.artifact_sha256:
            raise ModelCandidateValidationError(
                "Candidate artifact hash differs from the training receipt."
            )
        if receipt.started_at < package.created_at:
            raise ModelCandidateValidationError(
                "External training predates the governed training-intent package."
            )
        if candidate.created_at < receipt.completed_at:
            raise ModelCandidateValidationError(
                "Candidate manifest predates external training completion."
            )
        if not self._budget_within(receipt.budget_used, ticket.budget):
            raise ModelCandidateValidationError(
                "External training receipt exceeds the governed Ticket budget."
            )

    @staticmethod
    def _budget_within(used: TrainingBudget, allowed: TrainingBudget) -> bool:
        return (
            used.max_gpu_hours <= allowed.max_gpu_hours
            and used.max_rollouts <= allowed.max_rollouts
            and used.max_training_tokens <= allowed.max_training_tokens
            and used.max_cost_usd <= allowed.max_cost_usd
        )


__all__ = [
    "AllowlistedTrainingAuthorizationVerifier",
    "ModelCandidateAdmissionResult",
    "ModelCandidateAdmissionService",
    "RejectUnverifiedTrainingAuthorization",
    "TrainingAuthorizationVerifier",
]
