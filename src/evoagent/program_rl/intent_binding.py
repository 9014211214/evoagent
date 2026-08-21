from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import ProgramCheckpoint
from evoagent.program_rl.models import (
    ProgramLocalRLBindingPackage,
    ProgramLocalRLIntent,
)
from evoagent.program_rl.package import ProgramLocalRLPackageManager


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


class RunningGenerationIntentBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent: ProgramLocalRLIntent
    running_attestation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    running_attestation_hash: str = Field(pattern=_SHA256_PATTERN)
    running_attestation_payload: dict[str, Any] = Field(default_factory=dict)
    campaign_checkpoint: ProgramCheckpoint
    running_attestor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    running_attested_at: datetime
    bound_by: str = Field(pattern=_SAFE_ID_PATTERN)
    bound_at: datetime
    optimizer_execution_authorized: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    binding_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("running_attested_at", "bound_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Running Generation intent binding time")

    @model_validator(mode="after")
    def validate_binding(self):
        if self.bound_at < self.running_attested_at:
            raise ValueError("Local-RL intent binding predates running attestation.")
        if self.bound_by != self.intent.created_by:
            raise ValueError("Local-RL intent binding actor differs from intent author.")
        if self.bound_at != self.intent.created_at:
            raise ValueError("Local-RL intent binding time differs from intent creation.")
        if self.running_attestor_id not in set(self.intent.governed_actor_ids):
            raise ValueError(
                "Running Generation attestor is absent from governed intent actors."
            )
        validate_safe_content(self.running_attestation_payload)
        payload = self.model_dump(mode="json", exclude={"binding_hash"})
        validate_safe_content(payload)
        if self.binding_hash != canonical_sha256(payload):
            raise ValueError("Running Generation intent binding hash mismatch.")
        return self


class RunningGenerationIntentBindingManager:
    """Cross-bind the base optimizer intent to both Registry audit anchors."""

    def build(
        self,
        intent: ProgramLocalRLIntent,
        attestation: Any,
        *,
        binding_id: str | None = None,
    ) -> RunningGenerationIntentBinding:
        if not hasattr(attestation, "model_dump"):
            raise TypeError("Running Generation attestation must be a Pydantic record.")
        attestation_payload_without_hash = attestation.model_dump(
            mode="json",
            exclude={"attestation_hash"},
        )
        if (
            getattr(attestation, "attestation_hash", None)
            != program_payload_hash(attestation_payload_without_hash)
        ):
            raise ValueError("Running Generation attestation hash mismatch.")
        expected = {
            "program_id": attestation.program_id,
            "generation_id": attestation.generation_id,
            "generation_index": attestation.generation_index,
            "program_head_revision": attestation.program_head_revision,
            "campaign_id": attestation.campaign_id,
            "plan_id": attestation.plan_id,
            "plan_hash": attestation.plan_hash,
            "source_signal_id": attestation.source_signal_id,
            "source_signal_hash": attestation.source_signal_hash,
            "attribution_receipt_id": attestation.attribution_receipt_id,
            "attribution_receipt_hash": attestation.attribution_receipt_hash,
            "intervention_layer": attestation.intervention_layer,
            "intervention_action": attestation.intervention_action,
            "parent_agent_identity_hash": (
                attestation.parent_agent_identity_hash
            ),
            "target_agent_identity_hash": (
                attestation.target_agent_identity_hash
            ),
            "expected_release_package_hash": (
                attestation.expected_release_package_hash
            ),
            "expected_release_plan_hash": (
                attestation.expected_release_plan_hash
            ),
        }
        for field, value in expected.items():
            if getattr(intent, field) != value:
                raise ValueError(
                    f"Local-RL intent {field} differs from running attestation."
                )
        if intent.program_checkpoint.model_dump(mode="json") != (
            attestation.program_checkpoint.model_dump(mode="json")
        ):
            raise ValueError(
                "Local-RL intent Program checkpoint differs from running attestation."
            )
        payload = {
            "binding_id": binding_id
            or f"running-generation-intent-binding:{intent.intent_id}",
            "intent": intent,
            "running_attestation_id": attestation.attestation_id,
            "running_attestation_hash": attestation.attestation_hash,
            "running_attestation_payload": attestation.model_dump(mode="json"),
            "campaign_checkpoint": attestation.campaign_checkpoint.model_dump(
                mode="json"
            ),
            "running_attestor_id": attestation.attested_by,
            "running_attested_at": attestation.attested_at,
            "bound_by": intent.created_by,
            "bound_at": intent.created_at,
            "optimizer_execution_authorized": False,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return RunningGenerationIntentBinding(
            **payload,
            binding_hash=program_payload_hash(payload),
        )


class RunningAttestedProgramLocalRLBindingPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_package: ProgramLocalRLBindingPackage
    intent_binding: RunningGenerationIntentBinding
    created_at: datetime
    checkpoint_promotion_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    upload_performed: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Running-attested local-RL package time")

    @model_validator(mode="after")
    def validate_package_hash(self):
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != canonical_sha256(payload):
            raise ValueError("Running-attested local-RL package hash mismatch.")
        return self


class RunningAttestedProgramLocalRLPackageError(ValueError):
    pass


class RunningAttestedProgramLocalRLPackageManager:
    """Preserve running-attestation lineage through optimizer result packaging."""

    def build(
        self,
        *,
        package_id: str,
        base_package: ProgramLocalRLBindingPackage,
        intent_binding: RunningGenerationIntentBinding,
        created_at: datetime,
    ) -> RunningAttestedProgramLocalRLBindingPackage:
        payload = {
            "package_id": package_id,
            "base_package": base_package,
            "intent_binding": intent_binding,
            "created_at": created_at,
            "checkpoint_promotion_performed": False,
            "production_activation_performed": False,
            "external_rollout_performed_by_evoagent": False,
            "upload_performed": False,
        }
        package = RunningAttestedProgramLocalRLBindingPackage(
            **payload,
            package_hash=program_payload_hash(payload),
        )
        self.verify(package)
        return package

    @staticmethod
    def verify(package: RunningAttestedProgramLocalRLBindingPackage) -> bool:
        ProgramLocalRLPackageManager.verify(package.base_package)
        if package.base_package.intent != package.intent_binding.intent:
            raise RunningAttestedProgramLocalRLPackageError(
                "Optimizer package intent differs from running-attestation binding."
            )
        if package.created_at < max(
            package.base_package.created_at,
            package.intent_binding.bound_at,
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested package predates its immutable inputs."
            )
        if (
            package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested package widens its offline non-promotion boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested local-RL package hash mismatch."
            )
        return True


__all__ = [
    "RunningAttestedProgramLocalRLBindingPackage",
    "RunningAttestedProgramLocalRLPackageError",
    "RunningAttestedProgramLocalRLPackageManager",
    "RunningGenerationIntentBinding",
    "RunningGenerationIntentBindingManager",
]
