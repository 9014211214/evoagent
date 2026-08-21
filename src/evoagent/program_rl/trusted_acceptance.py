from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import ProgramCheckpoint
from evoagent.program_rl.evidence_verified_public_final import (
    FullyAttestedProgramLocalRLBindingPackage,
    FullyAttestedProgramLocalRLPackageManager,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


class ProgramLocalRLTrustedAnchors(BaseModel):
    """A separately stored trust input; it is not derived from the package under review."""

    model_config = ConfigDict(frozen=True)

    anchors_id: str = Field(pattern=_SAFE_ID_PATTERN)
    running_attestation_hash: str = Field(pattern=_SHA256_PATTERN)
    program_checkpoint: ProgramCheckpoint
    campaign_checkpoint: ProgramCheckpoint
    native_runtime_contract_hash: str = Field(pattern=_SHA256_PATTERN)
    native_projection_spec_hash: str = Field(pattern=_SHA256_PATTERN)
    native_local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    anchored_by: str = Field(pattern=_SAFE_ID_PATTERN)
    anchored_at: datetime
    anchors_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("anchored_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Program local-RL trusted-anchor time")

    @model_validator(mode="after")
    def validate_anchors(self):
        payload = self.model_dump(mode="json", exclude={"anchors_hash"})
        validate_safe_content(payload)
        if self.anchors_hash != canonical_sha256(payload):
            raise ValueError("Program local-RL trusted-anchor hash mismatch.")
        return self


class ProgramLocalRLAcceptanceReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    fully_attested_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    fully_attested_package_hash: str = Field(pattern=_SHA256_PATTERN)
    anchors_id: str = Field(pattern=_SAFE_ID_PATTERN)
    anchors_hash: str = Field(pattern=_SHA256_PATTERN)
    accepted_by: str = Field(pattern=_SAFE_ID_PATTERN)
    accepted_at: datetime
    evidence_accepted: Literal[True] = True
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    receipt_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("accepted_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Program local-RL acceptance time")

    @model_validator(mode="after")
    def validate_receipt(self):
        payload = self.model_dump(mode="json", exclude={"receipt_hash"})
        validate_safe_content(payload)
        if self.receipt_hash != canonical_sha256(payload):
            raise ValueError("Program local-RL acceptance receipt hash mismatch.")
        return self


class ProgramLocalRLAcceptanceError(ValueError):
    pass


class ProgramLocalRLAcceptanceManager:
    """Final trust boundary: recursive verification plus independent anchors."""

    @staticmethod
    def _verify_anchor_match(
        package: FullyAttestedProgramLocalRLBindingPackage,
        anchors: ProgramLocalRLTrustedAnchors,
    ) -> None:
        FullyAttestedProgramLocalRLPackageManager.verify(package)
        running = package.running_attested_package.intent_binding
        runtime_package = package.runtime_attested_package
        runtime = runtime_package.runtime_attestation
        schema = runtime.schema_attestation
        base = runtime_package.schema_attested_package.attested_package.base_package
        result = base.result
        embedded = running.running_attestation_payload

        if (
            running.running_attestation_hash
            != anchors.running_attestation_hash
            or embedded.get("attestation_hash")
            != anchors.running_attestation_hash
            or running.intent.program_checkpoint != anchors.program_checkpoint
            or running.campaign_checkpoint != anchors.campaign_checkpoint
            or runtime.runtime_contract.contract_hash
            != anchors.native_runtime_contract_hash
            or schema.projection_spec.spec_hash
            != anchors.native_projection_spec_hash
            or result.local_rl_package_hash
            != anchors.native_local_rl_package_hash
            or result.optimizer_evidence_hash
            != anchors.optimizer_evidence_hash
            or result.heldout_evaluation_hash
            != anchors.heldout_evaluation_hash
        ):
            raise ProgramLocalRLAcceptanceError(
                "Fully attested local-RL package differs from independent external anchors."
            )

    def accept(
        self,
        package: FullyAttestedProgramLocalRLBindingPackage,
        anchors: ProgramLocalRLTrustedAnchors,
        *,
        accepted_by: str,
        accepted_at: datetime,
        receipt_id: str,
    ) -> ProgramLocalRLAcceptanceReceipt:
        self._verify_anchor_match(package, anchors)
        runtime_package = package.runtime_attested_package
        base = runtime_package.schema_attested_package.attested_package.base_package
        runtime = runtime_package.runtime_attestation
        running = package.running_attested_package.intent_binding
        intent = base.intent
        forbidden = {
            *intent.governed_actor_ids,
            intent.created_by,
            base.authorization.authorized_by,
            base.result.executed_by,
            runtime.runtime_contract.reviewed_by,
            runtime.runtime_receipt.verified_by,
            runtime.schema_attestation.projection_spec.created_by,
            runtime_package.schema_attested_package.attested_package.attested_result.bound_by,
            runtime_package.accepted_by,
            running.running_attestor_id,
            running.bound_by,
            package.accepted_by,
            anchors.anchored_by,
        }
        if accepted_by in forbidden:
            raise ProgramLocalRLAcceptanceError(
                "Final local-RL acceptance actor overlaps a governed evidence role."
            )
        if accepted_at < max(package.accepted_at, anchors.anchored_at):
            raise ProgramLocalRLAcceptanceError(
                "Final local-RL acceptance predates package or external anchors."
            )
        payload = {
            "receipt_id": receipt_id,
            "fully_attested_package_id": package.package_id,
            "fully_attested_package_hash": package.package_hash,
            "anchors_id": anchors.anchors_id,
            "anchors_hash": anchors.anchors_hash,
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
            "evidence_accepted": True,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        receipt = ProgramLocalRLAcceptanceReceipt(
            **payload,
            receipt_hash=program_payload_hash(payload),
        )
        self.verify(package, anchors, receipt)
        return receipt

    @classmethod
    def verify(
        cls,
        package: FullyAttestedProgramLocalRLBindingPackage,
        anchors: ProgramLocalRLTrustedAnchors,
        receipt: ProgramLocalRLAcceptanceReceipt,
    ) -> bool:
        cls._verify_anchor_match(package, anchors)
        expected_anchors_hash = program_payload_hash(
            anchors.model_dump(mode="json", exclude={"anchors_hash"})
        )
        if anchors.anchors_hash != expected_anchors_hash:
            raise ProgramLocalRLAcceptanceError(
                "Program local-RL external anchors hash mismatch."
            )
        if (
            receipt.fully_attested_package_id != package.package_id
            or receipt.fully_attested_package_hash != package.package_hash
            or receipt.anchors_id != anchors.anchors_id
            or receipt.anchors_hash != anchors.anchors_hash
            or receipt.accepted_at < max(package.accepted_at, anchors.anchored_at)
            or receipt.evidence_accepted is not True
            or receipt.checkpoint_promotion_authorized
            or receipt.production_activation_authorized
        ):
            raise ProgramLocalRLAcceptanceError(
                "Program local-RL acceptance receipt lineage or authority differs."
            )
        expected_receipt_hash = program_payload_hash(
            receipt.model_dump(mode="json", exclude={"receipt_hash"})
        )
        if receipt.receipt_hash != expected_receipt_hash:
            raise ProgramLocalRLAcceptanceError(
                "Program local-RL acceptance receipt hash mismatch."
            )
        return True


def build_trusted_anchors(**payload) -> ProgramLocalRLTrustedAnchors:
    return ProgramLocalRLTrustedAnchors(
        **payload,
        anchors_hash=program_payload_hash(payload),
    )


__all__ = [
    "ProgramLocalRLAcceptanceError",
    "ProgramLocalRLAcceptanceManager",
    "ProgramLocalRLAcceptanceReceipt",
    "ProgramLocalRLTrustedAnchors",
    "build_trusted_anchors",
]
