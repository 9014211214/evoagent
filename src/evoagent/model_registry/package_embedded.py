from __future__ import annotations

from pathlib import Path

from evoagent.model_registry.builders import (
    build_training_authorization_scope,
    resolve_training_intent_package,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.model_registry.package import (
    ModelAdmissionPackageError,
    ModelAdmissionPackageManager as _BasePackageManager,
    ModelAdmissionPackageManifest as _BasePackageManifest,
)
from evoagent.training.package import (
    ModelEvolutionPackageManager,
    ModelEvolutionPackageManifest,
)


class ModelAdmissionPackageManifest(_BasePackageManifest):
    """Self-contained v1 admission evidence including its full training intent."""

    training_intent_package: ModelEvolutionPackageManifest


class ModelAdmissionPackageManager(_BasePackageManager):
    """Build and verify a self-contained admission/activation/rollback package."""

    def build(
        self,
        *,
        training_intent_package: ModelEvolutionPackageManifest | None = None,
        **fields,
    ) -> ModelAdmissionPackageManifest:
        declared_hash = fields.get("training_intent_package_hash")
        if not isinstance(declared_hash, str):
            raise ModelAdmissionPackageError(
                "A training-intent package hash is required."
            )
        if training_intent_package is None:
            try:
                training_intent_package = resolve_training_intent_package(
                    declared_hash
                )
            except ValueError as exc:
                raise ModelAdmissionPackageError(str(exc)) from exc
        if declared_hash != training_intent_package.package_hash:
            raise ModelAdmissionPackageError(
                "Declared training-intent hash differs from the embedded package."
            )
        provisional = ModelAdmissionPackageManifest(
            training_intent_package=training_intent_package,
            package_hash="0" * 64,
            **fields,
        )
        payload = provisional.model_dump(
            mode="json",
            exclude={"package_hash"},
        )
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: ModelAdmissionPackageManifest) -> bool:
        # The original verifier validates the outer hash, exact Campaign,
        # approvals, Registry records, audit chains, evaluation, activation,
        # and rollback. A Pydantic subclass includes the embedded package in
        # the same outer manifest hash.
        super().verify(manifest)

        intent = manifest.training_intent_package
        ModelEvolutionPackageManager().verify(intent)
        if intent.package_hash != manifest.training_intent_package_hash:
            raise ModelAdmissionPackageError(
                "Embedded training-intent package hash mismatch."
            )
        if (
            intent.source_repository != manifest.source_repository
            or intent.source_commit != manifest.source_commit
            or intent.framework_version != manifest.framework_version
            or intent.third_party_lock_hash
            != manifest.third_party_lock_hash
        ):
            raise ModelAdmissionPackageError(
                "Embedded training intent provenance differs from the outer package."
            )

        candidate = manifest.candidate_manifest
        receipt = manifest.training_receipt
        held_out_ids = tuple(task.task_id for task in intent.held_out_tasks)
        if (
            manifest.initial_manifest.model_id != intent.ticket.base_model_id
            or candidate.base_model_id != intent.ticket.base_model_id
            or candidate.training_method != intent.candidate.method
            or candidate.evidence_manifest_hash
            != intent.dataset.manifest_hash
            or candidate.held_out_task_ids != held_out_ids
            or candidate.training_intent_campaign_id
            != intent.campaign.campaign_id
        ):
            raise ModelAdmissionPackageError(
                "Candidate differs from the embedded governed training intent."
            )
        if (
            receipt.base_model_id != intent.ticket.base_model_id
            or receipt.training_method != intent.candidate.method
            or receipt.evidence_manifest_hash != intent.dataset.manifest_hash
            or receipt.held_out_task_ids != held_out_ids
            or receipt.training_intent_campaign_id
            != intent.campaign.campaign_id
        ):
            raise ModelAdmissionPackageError(
                "Training receipt differs from the embedded governed intent."
            )
        expected_authorization_hash = canonical_sha256(
            build_training_authorization_scope(
                intent,
                candidate_id=candidate.candidate_id,
            )
        )
        if (
            candidate.training_authorization.authorization_hash
            != expected_authorization_hash
        ):
            raise ModelAdmissionPackageError(
                "Training authorization scope differs from the embedded intent."
            )
        if receipt.started_at < intent.created_at:
            raise ModelAdmissionPackageError(
                "External training predates the embedded governed intent."
            )
        if not self._budget_within(
            receipt.budget_used,
            intent.ticket.budget,
        ):
            raise ModelAdmissionPackageError(
                "External training receipt exceeds the embedded Ticket budget."
            )
        return True

    def load_file(self, path: str | Path) -> ModelAdmissionPackageManifest:
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

    @staticmethod
    def _budget_within(used, allowed) -> bool:
        return (
            used.max_gpu_hours <= allowed.max_gpu_hours
            and used.max_rollouts <= allowed.max_rollouts
            and used.max_training_tokens <= allowed.max_training_tokens
            and used.max_cost_usd <= allowed.max_cost_usd
        )


__all__ = [
    "ModelAdmissionPackageError",
    "ModelAdmissionPackageManager",
    "ModelAdmissionPackageManifest",
]
