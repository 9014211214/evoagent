from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attestation import NativeLocalRLPackageAttestation
from evoagent.program_rl.models import (
    ProgramLocalRLBindingPackage,
    ProgramLocalRLResultBinding,
)
from evoagent.program_rl.package import ProgramLocalRLPackageManager


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class AttestedProgramLocalRLResultBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding_id: str = Field(pattern=_SAFE_ID_PATTERN)
    result: ProgramLocalRLResultBinding
    native_attestation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    native_attestation_hash: str = Field(pattern=_SHA256_PATTERN)
    bound_by: str = Field(pattern=_SAFE_ID_PATTERN)
    bound_at: datetime
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    binding_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("bound_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Attested local-RL binding time must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_binding(self):
        payload = self.model_dump(mode="json", exclude={"binding_hash"})
        validate_safe_content(payload)
        if self.binding_hash != canonical_sha256(payload):
            raise ValueError("Attested Program local-RL result binding hash mismatch.")
        return self


class AttestedProgramLocalRLBindingPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_package: ProgramLocalRLBindingPackage
    native_attestation: NativeLocalRLPackageAttestation
    attested_result: AttestedProgramLocalRLResultBinding
    created_at: datetime
    checkpoint_promotion_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    upload_performed: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Attested local-RL package time must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_package_hash(self):
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != canonical_sha256(payload):
            raise ValueError("Attested Program local-RL package hash mismatch.")
        return self


class AttestedProgramLocalRLPackageError(ValueError):
    pass


class AttestedProgramLocalRLPackageManager:
    """Require native package verification before Program result acceptance."""

    def build(
        self,
        *,
        package_id: str,
        base_package: ProgramLocalRLBindingPackage,
        native_attestation: NativeLocalRLPackageAttestation,
        bound_by: str,
        bound_at: datetime,
        created_at: datetime,
        binding_id: str | None = None,
    ) -> AttestedProgramLocalRLBindingPackage:
        binding_payload = {
            "binding_id": binding_id
            or f"attested-program-local-rl-result:{base_package.result.result_id}",
            "result": base_package.result,
            "native_attestation_id": native_attestation.attestation_id,
            "native_attestation_hash": native_attestation.attestation_hash,
            "bound_by": bound_by,
            "bound_at": bound_at,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        binding = AttestedProgramLocalRLResultBinding(
            **binding_payload,
            binding_hash=program_payload_hash(binding_payload),
        )
        package_payload = {
            "package_id": package_id,
            "base_package": base_package,
            "native_attestation": native_attestation,
            "attested_result": binding,
            "created_at": created_at,
            "checkpoint_promotion_performed": False,
            "production_activation_performed": False,
            "external_rollout_performed_by_evoagent": False,
            "upload_performed": False,
        }
        package = AttestedProgramLocalRLBindingPackage(
            **package_payload,
            package_hash=program_payload_hash(package_payload),
        )
        self.verify(package)
        return package

    @staticmethod
    def verify(package: AttestedProgramLocalRLBindingPackage) -> bool:
        ProgramLocalRLPackageManager.verify(package.base_package)
        base = package.base_package
        attestation = package.native_attestation
        projection = attestation.projection
        binding = package.attested_result
        result = base.result
        intent = base.intent
        authorization = base.authorization

        if (
            projection.local_rl_run_id != intent.local_rl_run_id
            or projection.optimizer_config_hash != intent.optimizer_config_hash
            or projection.training_task_set_hash != intent.training_task_set_hash
            or projection.heldout_task_set_hash != intent.heldout_task_set_hash
        ):
            raise AttestedProgramLocalRLPackageError(
                "Native local-RL attestation differs from the Program optimization intent."
            )
        if (
            projection.local_rl_package_id != result.local_rl_package_id
            or projection.local_rl_package_hash != result.local_rl_package_hash
            or projection.initial_checkpoint_hash != result.initial_checkpoint_hash
            or projection.selected_checkpoint_hash != result.selected_checkpoint_hash
            or projection.optimizer_evidence_hash != result.optimizer_evidence_hash
            or projection.heldout_evaluation_hash != result.heldout_evaluation_hash
            or projection.usage != result.usage
            or projection.heldout_reward_delta != result.heldout_reward_delta
            or projection.heldout_success_delta != result.heldout_success_delta
            or projection.unsafe_action_count != result.unsafe_action_count
            or projection.regression_count != result.regression_count
        ):
            raise AttestedProgramLocalRLPackageError(
                "Native local-RL attestation differs from the bound result evidence."
            )
        if (
            binding.result != result
            or binding.native_attestation_id != attestation.attestation_id
            or binding.native_attestation_hash != attestation.attestation_hash
        ):
            raise AttestedProgramLocalRLPackageError(
                "Attested result does not bind the exact native verification receipt."
            )
        if attestation.verified_at < result.completed_at:
            raise AttestedProgramLocalRLPackageError(
                "Native local-RL attestation predates optimizer completion."
            )
        if binding.bound_at < attestation.verified_at:
            raise AttestedProgramLocalRLPackageError(
                "Program result was bound before native package verification."
            )
        forbidden = {
            *intent.governed_actor_ids,
            intent.created_by,
            authorization.authorized_by,
            result.executed_by,
            attestation.verified_by,
        }
        if binding.bound_by in forbidden:
            raise AttestedProgramLocalRLPackageError(
                "Program result binder overlaps a governed production or verification role."
            )
        if package.created_at < binding.bound_at:
            raise AttestedProgramLocalRLPackageError(
                "Attested Program local-RL package predates its binding."
            )
        if (
            package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
        ):
            raise AttestedProgramLocalRLPackageError(
                "Attested local-RL package widens its non-promotion boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise AttestedProgramLocalRLPackageError(
                "Attested Program local-RL package hash mismatch."
            )
        return True


__all__ = [
    "AttestedProgramLocalRLBindingPackage",
    "AttestedProgramLocalRLPackageError",
    "AttestedProgramLocalRLPackageManager",
    "AttestedProgramLocalRLResultBinding",
]
