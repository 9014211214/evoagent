from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attested_package import (
    AttestedProgramLocalRLBindingPackage,
    AttestedProgramLocalRLPackageManager,
)
from evoagent.program_rl.schema_attestation import (
    SchemaBoundNativeLocalRLPackageAttestation,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class SchemaAttestedProgramLocalRLBindingPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attested_package: AttestedProgramLocalRLBindingPackage
    schema_attestation: SchemaBoundNativeLocalRLPackageAttestation
    created_at: datetime
    checkpoint_promotion_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    upload_performed: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Schema-attested local-RL package time needs a timezone.")
        return value

    @model_validator(mode="after")
    def validate_package_hash(self):
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != canonical_sha256(payload):
            raise ValueError("Schema-attested Program local-RL package hash mismatch.")
        return self


class SchemaAttestedProgramLocalRLPackageError(ValueError):
    pass


class SchemaAttestedProgramLocalRLPackageManager:
    """Final evidence wrapper: native verification plus reviewed field schema."""

    def build(
        self,
        *,
        package_id: str,
        attested_package: AttestedProgramLocalRLBindingPackage,
        schema_attestation: SchemaBoundNativeLocalRLPackageAttestation,
        created_at: datetime,
    ) -> SchemaAttestedProgramLocalRLBindingPackage:
        payload = {
            "package_id": package_id,
            "attested_package": attested_package,
            "schema_attestation": schema_attestation,
            "created_at": created_at,
            "checkpoint_promotion_performed": False,
            "production_activation_performed": False,
            "upload_performed": False,
        }
        package = SchemaAttestedProgramLocalRLBindingPackage(
            **payload,
            package_hash=program_payload_hash(payload),
        )
        self.verify(package)
        return package

    @staticmethod
    def verify(package: SchemaAttestedProgramLocalRLBindingPackage) -> bool:
        AttestedProgramLocalRLPackageManager.verify(package.attested_package)
        attested = package.attested_package
        schema = package.schema_attestation
        intent = attested.base_package.intent
        if schema.base_attestation != attested.native_attestation:
            raise SchemaAttestedProgramLocalRLPackageError(
                "Reviewed projection schema does not bind the accepted native attestation."
            )
        if schema.projection_receipt.projection != schema.base_attestation.projection:
            raise SchemaAttestedProgramLocalRLPackageError(
                "Reviewed projection receipt differs from native attestation evidence."
            )
        if schema.projection_spec.created_at > schema.projection_receipt.projected_at:
            raise SchemaAttestedProgramLocalRLPackageError(
                "Native projection used a schema created after verification."
            )
        forbidden_spec_authors = {
            *intent.governed_actor_ids,
            intent.created_by,
            attested.base_package.authorization.authorized_by,
            attested.base_package.result.executed_by,
            schema.base_attestation.verified_by,
            attested.attested_result.bound_by,
        }
        if schema.projection_spec.created_by in forbidden_spec_authors:
            raise SchemaAttestedProgramLocalRLPackageError(
                "Projection schema author overlaps a governed execution or review role."
            )
        if package.created_at < max(
            attested.created_at,
            schema.base_attestation.verified_at,
            schema.projection_receipt.projected_at,
        ):
            raise SchemaAttestedProgramLocalRLPackageError(
                "Schema-attested package predates its verified inputs."
            )
        if (
            package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.upload_performed
        ):
            raise SchemaAttestedProgramLocalRLPackageError(
                "Schema-attested package widens its non-promotion boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise SchemaAttestedProgramLocalRLPackageError(
                "Schema-attested Program local-RL package hash mismatch."
            )
        return True


__all__ = [
    "SchemaAttestedProgramLocalRLBindingPackage",
    "SchemaAttestedProgramLocalRLPackageError",
    "SchemaAttestedProgramLocalRLPackageManager",
]
