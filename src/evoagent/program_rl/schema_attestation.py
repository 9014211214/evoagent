from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attestation import (
    NativeLocalRLPackageAttestation,
    NativeLocalRLPackageVerifier,
    NativeLocalRLProjection,
)
from evoagent.program_rl.models import LocalRLExecutionUsage


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_REQUIRED_PATHS = frozenset(
    {
        "local_rl_package_id",
        "local_rl_package_hash",
        "local_rl_run_id",
        "optimizer_config_hash",
        "training_task_set_hash",
        "heldout_task_set_hash",
        "initial_checkpoint_hash",
        "selected_checkpoint_hash",
        "optimizer_evidence_hash",
        "heldout_evaluation_hash",
        "iterations",
        "rollouts",
        "tokens",
        "cost_usd",
        "heldout_reward_delta",
        "heldout_success_delta",
        "unsafe_action_count",
        "regression_count",
    }
)


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _extract(payload: Any, path: tuple[str | int, ...]) -> Any:
    value = payload
    for component in path:
        try:
            if isinstance(component, int):
                value = value[component]
            else:
                value = value[component]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"Native local-RL projection path is missing: {path!r}."
            ) from exc
    return value


class NativeLocalRLProjectionSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    spec_id: str = Field(pattern=_SAFE_ID_PATTERN)
    schema_name: str = Field(pattern=_SAFE_ID_PATTERN)
    schema_version: str = Field(pattern=_SAFE_ID_PATTERN)
    paths: dict[str, tuple[str | int, ...]]
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    spec_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Native local-RL projection spec time")

    @field_validator("paths")
    @classmethod
    def exact_paths(
        cls,
        value: dict[str, tuple[str | int, ...]],
    ) -> dict[str, tuple[str | int, ...]]:
        if set(value) != _REQUIRED_PATHS:
            raise ValueError(
                "Native local-RL projection spec must define the exact governed field set."
            )
        if any(not path for path in value.values()):
            raise ValueError("Native local-RL projection paths must be non-empty.")
        return value

    @model_validator(mode="after")
    def validate_spec(self):
        payload = self.model_dump(mode="json", exclude={"spec_hash"})
        validate_safe_content(payload)
        if self.spec_hash != canonical_sha256(payload):
            raise ValueError("Native local-RL projection spec hash mismatch.")
        return self


class SchemaBoundNativeLocalRLProjectionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    spec_id: str = Field(pattern=_SAFE_ID_PATTERN)
    spec_hash: str = Field(pattern=_SHA256_PATTERN)
    native_package_source_hash: str = Field(pattern=_SHA256_PATTERN)
    projection: NativeLocalRLProjection
    projected_at: datetime
    projection_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("projected_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Native local-RL projection receipt time")

    @model_validator(mode="after")
    def validate_receipt(self):
        payload = self.model_dump(mode="json", exclude={"projection_hash"})
        validate_safe_content(payload)
        if self.projection_hash != canonical_sha256(payload):
            raise ValueError("Native local-RL projection receipt hash mismatch.")
        return self


class SchemaBoundNativeLocalRLPackageAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    attestation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_attestation: NativeLocalRLPackageAttestation
    projection_spec: NativeLocalRLProjectionSpec
    projection_receipt: SchemaBoundNativeLocalRLProjectionReceipt
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    attestation_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_attestation(self):
        if (
            self.base_attestation.attestation_id != self.attestation_id
            or self.base_attestation.projection
            != self.projection_receipt.projection
            or self.projection_receipt.spec_id != self.projection_spec.spec_id
            or self.projection_receipt.spec_hash != self.projection_spec.spec_hash
            or self.projection_receipt.projected_at
            > self.base_attestation.verified_at
        ):
            raise ValueError(
                "Schema-bound native local-RL attestation lineage differs."
            )
        payload = self.model_dump(mode="json", exclude={"attestation_hash"})
        validate_safe_content(payload)
        if self.attestation_hash != canonical_sha256(payload):
            raise ValueError(
                "Schema-bound native local-RL attestation hash mismatch."
            )
        return self


class PydanticNativeLocalRLProjector:
    """Project a verified native Pydantic package using a hashed field map."""

    def __init__(self, spec: NativeLocalRLProjectionSpec):
        self.spec = spec

    def project_receipt(
        self,
        package: Any,
        *,
        projected_at: datetime,
        receipt_id: str,
    ) -> SchemaBoundNativeLocalRLProjectionReceipt:
        if hasattr(package, "model_dump"):
            payload = package.model_dump(mode="json")
        elif isinstance(package, dict):
            payload = package
        else:
            raise TypeError(
                "Native local-RL package must expose model_dump(mode='json') or be a dict."
            )
        values = {
            name: _extract(payload, self.spec.paths[name])
            for name in _REQUIRED_PATHS
        }
        projection = NativeLocalRLProjection(
            local_rl_package_id=str(values["local_rl_package_id"]),
            local_rl_package_hash=str(values["local_rl_package_hash"]),
            local_rl_run_id=str(values["local_rl_run_id"]),
            optimizer_config_hash=str(values["optimizer_config_hash"]),
            training_task_set_hash=str(values["training_task_set_hash"]),
            heldout_task_set_hash=str(values["heldout_task_set_hash"]),
            initial_checkpoint_hash=str(values["initial_checkpoint_hash"]),
            selected_checkpoint_hash=str(values["selected_checkpoint_hash"]),
            optimizer_evidence_hash=str(values["optimizer_evidence_hash"]),
            heldout_evaluation_hash=str(values["heldout_evaluation_hash"]),
            usage=LocalRLExecutionUsage(
                iterations=int(values["iterations"]),
                rollouts=int(values["rollouts"]),
                tokens=int(values["tokens"]),
                cost_usd=float(values["cost_usd"]),
            ),
            heldout_reward_delta=float(values["heldout_reward_delta"]),
            heldout_success_delta=float(values["heldout_success_delta"]),
            unsafe_action_count=int(values["unsafe_action_count"]),
            regression_count=int(values["regression_count"]),
        )
        receipt_payload = {
            "receipt_id": receipt_id,
            "spec_id": self.spec.spec_id,
            "spec_hash": self.spec.spec_hash,
            "native_package_source_hash": program_payload_hash(payload),
            "projection": projection,
            "projected_at": projected_at,
        }
        return SchemaBoundNativeLocalRLProjectionReceipt(
            **receipt_payload,
            projection_hash=program_payload_hash(receipt_payload),
        )


class SchemaBoundNativeLocalRLAttestor:
    """Verify a native package and bind the exact reviewed projection schema."""

    def attest(
        self,
        package: Any,
        *,
        verifier: NativeLocalRLPackageVerifier,
        projector: PydanticNativeLocalRLProjector,
        verified_by: str,
        verified_at: datetime,
        attestation_id: str,
        projection_receipt_id: str,
    ) -> SchemaBoundNativeLocalRLPackageAttestation:
        if verifier.verify(package) is not True:
            raise ValueError("Native local-RL package verification did not pass.")
        projection_receipt = projector.project_receipt(
            package,
            projected_at=verified_at,
            receipt_id=projection_receipt_id,
        )
        base_payload = {
            "attestation_id": attestation_id,
            "projection": projection_receipt.projection,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "native_package_verified": True,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        base_attestation = NativeLocalRLPackageAttestation(
            **base_payload,
            attestation_hash=program_payload_hash(base_payload),
        )
        payload = {
            "attestation_id": attestation_id,
            "base_attestation": base_attestation,
            "projection_spec": projector.spec,
            "projection_receipt": projection_receipt,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return SchemaBoundNativeLocalRLPackageAttestation(
            **payload,
            attestation_hash=program_payload_hash(payload),
        )


__all__ = [
    "NativeLocalRLProjectionSpec",
    "PydanticNativeLocalRLProjector",
    "SchemaBoundNativeLocalRLAttestor",
    "SchemaBoundNativeLocalRLPackageAttestation",
    "SchemaBoundNativeLocalRLProjectionReceipt",
]
