from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.models import LocalRLExecutionUsage


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class NativeLocalRLProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    local_rl_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    optimizer_config_hash: str = Field(pattern=_SHA256_PATTERN)
    training_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    initial_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    usage: LocalRLExecutionUsage
    heldout_reward_delta: float
    heldout_success_delta: float
    unsafe_action_count: int = Field(ge=0)
    regression_count: int = Field(ge=0)
    native_package_hash_recomputed: Literal[True] = True
    optimizer_recomputed: Literal[True] = True
    heldout_evaluation_recomputed: Literal[True] = True
    training_heldout_disjoint: Literal[True] = True
    checkpoint_selection_recomputed: Literal[True] = True

    @model_validator(mode="after")
    def validate_projection(self):
        if self.training_task_set_hash == self.heldout_task_set_hash:
            raise ValueError("Native local-RL training and held-out sets overlap.")
        if self.initial_checkpoint_hash == self.selected_checkpoint_hash:
            raise ValueError("Native local-RL projection contains no policy update.")
        if (
            self.heldout_reward_delta <= 0.0
            or self.heldout_success_delta <= 0.0
            or self.unsafe_action_count != 0
            or self.regression_count != 0
        ):
            raise ValueError(
                "Native local-RL projection lacks strict safe held-out improvement."
            )
        validate_safe_content(self.model_dump(mode="json"))
        return self


class NativeLocalRLPackageVerifier(Protocol):
    def verify(self, package: Any) -> bool:
        ...


class NativeLocalRLPackageProjector(Protocol):
    def project(self, package: Any) -> NativeLocalRLProjection:
        ...


class NativeLocalRLPackageAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    attestation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    projection: NativeLocalRLProjection
    verified_by: str = Field(pattern=_SAFE_ID_PATTERN)
    verified_at: datetime
    native_package_verified: Literal[True] = True
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    attestation_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("verified_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Native local-RL attestation time must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_attestation(self):
        payload = self.model_dump(mode="json", exclude={"attestation_hash"})
        validate_safe_content(payload)
        if self.attestation_hash != canonical_sha256(payload):
            raise ValueError("Native local-RL package attestation hash mismatch.")
        return self


class NativeLocalRLAttestor:
    """Invoke a native package verifier before projecting Program evidence."""

    def attest(
        self,
        package: Any,
        *,
        verifier: NativeLocalRLPackageVerifier,
        projector: NativeLocalRLPackageProjector,
        verified_by: str,
        verified_at: datetime,
        attestation_id: str,
    ) -> NativeLocalRLPackageAttestation:
        verified = verifier.verify(package)
        if verified is not True:
            raise ValueError("Native local-RL package verification did not pass.")
        projection = projector.project(package)
        payload = {
            "attestation_id": attestation_id,
            "projection": projection,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "native_package_verified": True,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return NativeLocalRLPackageAttestation(
            **payload,
            attestation_hash=program_payload_hash(payload),
        )


__all__ = [
    "NativeLocalRLAttestor",
    "NativeLocalRLPackageAttestation",
    "NativeLocalRLPackageProjector",
    "NativeLocalRLPackageVerifier",
    "NativeLocalRLProjection",
]
