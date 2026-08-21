from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.native_contract import (
    RuntimeBoundNativeLocalRLPackageAttestation,
)
from evoagent.program_rl.schema_attested_package import (
    SchemaAttestedProgramLocalRLBindingPackage,
    SchemaAttestedProgramLocalRLPackageManager,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class RuntimeAttestedProgramLocalRLBindingPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    schema_attested_package: SchemaAttestedProgramLocalRLBindingPackage
    runtime_attestation: RuntimeBoundNativeLocalRLPackageAttestation
    accepted_by: str = Field(pattern=_SAFE_ID_PATTERN)
    accepted_at: datetime
    checkpoint_promotion_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    upload_performed: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("accepted_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Runtime-attested local-RL package time needs a timezone.")
        return value

    @model_validator(mode="after")
    def validate_package_hash(self):
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != canonical_sha256(payload):
            raise ValueError("Runtime-attested Program local-RL package hash mismatch.")
        return self


class RuntimeAttestedProgramLocalRLPackageError(ValueError):
    pass


class RuntimeAttestedProgramLocalRLPackageManager:
    """Final native boundary: package type, manager type, schema and evidence."""

    def build(
        self,
        *,
        package_id: str,
        schema_attested_package: SchemaAttestedProgramLocalRLBindingPackage,
        runtime_attestation: RuntimeBoundNativeLocalRLPackageAttestation,
        accepted_by: str,
        accepted_at: datetime,
    ) -> RuntimeAttestedProgramLocalRLBindingPackage:
        payload = {
            "package_id": package_id,
            "schema_attested_package": schema_attested_package,
            "runtime_attestation": runtime_attestation,
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
            "checkpoint_promotion_performed": False,
            "production_activation_performed": False,
            "external_rollout_performed_by_evoagent": False,
            "upload_performed": False,
        }
        package = RuntimeAttestedProgramLocalRLBindingPackage(
            **payload,
            package_hash=program_payload_hash(payload),
        )
        self.verify(package)
        return package

    @staticmethod
    def verify(package: RuntimeAttestedProgramLocalRLBindingPackage) -> bool:
        SchemaAttestedProgramLocalRLPackageManager.verify(
            package.schema_attested_package
        )
        schema_package = package.schema_attested_package
        runtime = package.runtime_attestation
        schema = schema_package.schema_attestation
        attested = schema_package.attested_package
        base = attested.base_package
        intent = base.intent

        if runtime.schema_attestation != schema:
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Runtime identity attestation differs from accepted schema attestation."
            )
        if (
            runtime.runtime_contract.projection_spec_id
            != schema.projection_spec.spec_id
            or runtime.runtime_contract.projection_spec_hash
            != schema.projection_spec.spec_hash
        ):
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Native runtime contract differs from reviewed projection schema."
            )
        if runtime.runtime_contract.reviewed_at > runtime.runtime_receipt.verified_at:
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Native runtime contract was reviewed after package verification."
            )
        forbidden = {
            *intent.governed_actor_ids,
            intent.created_by,
            base.authorization.authorized_by,
            base.result.executed_by,
            schema.base_attestation.verified_by,
            attested.attested_result.bound_by,
            schema.projection_spec.created_by,
            runtime.runtime_contract.reviewed_by,
        }
        if package.accepted_by in forbidden:
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Runtime-attested package acceptor overlaps a governed role."
            )
        if package.accepted_at < max(
            schema_package.created_at,
            runtime.runtime_receipt.verified_at,
        ):
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Runtime-attested package acceptance predates verified inputs."
            )
        if (
            package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
        ):
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Runtime-attested package widens its offline non-promotion boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Runtime-attested Program local-RL package hash mismatch."
            )
        return True


__all__ = [
    "RuntimeAttestedProgramLocalRLBindingPackage",
    "RuntimeAttestedProgramLocalRLPackageError",
    "RuntimeAttestedProgramLocalRLPackageManager",
]
