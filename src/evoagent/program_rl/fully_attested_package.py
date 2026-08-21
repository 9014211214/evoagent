from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.intent_binding import (
    RunningAttestedProgramLocalRLBindingPackage,
    RunningAttestedProgramLocalRLPackageManager,
)
from evoagent.program_rl.runtime_attested_package import (
    RuntimeAttestedProgramLocalRLBindingPackage,
)
from evoagent.program_rl.runtime_attested_package_final import (
    RuntimeAttestedProgramLocalRLPackageManager,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class FullyAttestedProgramLocalRLBindingPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    running_attested_package: RunningAttestedProgramLocalRLBindingPackage
    runtime_attested_package: RuntimeAttestedProgramLocalRLBindingPackage
    accepted_by: str = Field(pattern=_SAFE_ID_PATTERN)
    accepted_at: datetime
    checkpoint_promotion_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("accepted_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Fully attested local-RL package time needs a timezone.")
        return value

    @model_validator(mode="after")
    def validate_package_hash(self):
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != canonical_sha256(payload):
            raise ValueError("Fully attested Program local-RL package hash mismatch.")
        return self


class FullyAttestedProgramLocalRLPackageError(ValueError):
    pass


class FullyAttestedProgramLocalRLPackageManager:
    """Final evidence boundary before any future checkpoint-promotion process."""

    def build(
        self,
        *,
        package_id: str,
        running_attested_package: RunningAttestedProgramLocalRLBindingPackage,
        runtime_attested_package: RuntimeAttestedProgramLocalRLBindingPackage,
        accepted_by: str,
        accepted_at: datetime,
    ) -> FullyAttestedProgramLocalRLBindingPackage:
        payload = {
            "package_id": package_id,
            "running_attested_package": running_attested_package,
            "runtime_attested_package": runtime_attested_package,
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
            "checkpoint_promotion_performed": False,
            "production_activation_performed": False,
            "external_rollout_performed_by_evoagent": False,
            "upload_performed": False,
            "official_benchmark_claimed": False,
        }
        package = FullyAttestedProgramLocalRLBindingPackage(
            **payload,
            package_hash=program_payload_hash(payload),
        )
        self.verify(package)
        return package

    @staticmethod
    def verify(package: FullyAttestedProgramLocalRLBindingPackage) -> bool:
        RunningAttestedProgramLocalRLPackageManager.verify(
            package.running_attested_package
        )
        RuntimeAttestedProgramLocalRLPackageManager.verify(
            package.runtime_attested_package
        )
        running = package.running_attested_package
        runtime = package.runtime_attested_package
        runtime_base = (
            runtime.schema_attested_package.attested_package.base_package
        )
        if running.base_package != runtime_base:
            raise FullyAttestedProgramLocalRLPackageError(
                "Running Program and native runtime attestations bind different optimizer packages."
            )
        if (
            running.intent_binding.intent.intent_hash
            != runtime_base.intent.intent_hash
        ):
            raise FullyAttestedProgramLocalRLPackageError(
                "Running Generation intent differs from native optimizer evidence."
            )
        intent = runtime_base.intent
        runtime_attestation = runtime.runtime_attestation
        governed = {
            *intent.governed_actor_ids,
            intent.created_by,
            runtime_base.authorization.authorized_by,
            runtime_base.result.executed_by,
            runtime_attestation.runtime_contract.reviewed_by,
            runtime_attestation.runtime_receipt.verified_by,
            runtime.schema_attested_package.attested_package.attested_result.bound_by,
            runtime_attestation.schema_attestation.projection_spec.created_by,
            runtime.accepted_by,
        }
        if package.accepted_by in governed:
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested package acceptor overlaps a governed role."
            )
        if package.accepted_at < max(
            running.created_at,
            runtime.accepted_at,
        ):
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested package acceptance predates its evidence chains."
            )
        if (
            package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
            or package.official_benchmark_claimed
        ):
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested package widens its evidence-only boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested Program local-RL package hash mismatch."
            )
        return True


__all__ = [
    "FullyAttestedProgramLocalRLBindingPackage",
    "FullyAttestedProgramLocalRLPackageError",
    "FullyAttestedProgramLocalRLPackageManager",
]
