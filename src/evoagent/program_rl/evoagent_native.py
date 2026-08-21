from __future__ import annotations

from datetime import datetime

from evoagent.local_rl.package import (
    LocalRLPackageManager,
    LocalRLPackageManifest,
)
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attestation import NativeLocalRLPackageAttestation
from evoagent.program_rl.native_contract import EvoagentLocalRLPackageProjector
from evoagent.program_rl.native_governance_final import _verify_native_governance


if not getattr(EvoagentLocalRLPackageProjector, "_governance_installed", False):
    _ungoverned_project = EvoagentLocalRLPackageProjector.project

    def _governed_project(self, package):
        projection = _ungoverned_project(self, package)
        _verify_native_governance(package)
        return projection

    EvoagentLocalRLPackageProjector.project = _governed_project
    EvoagentLocalRLPackageProjector._governance_installed = True


class EvoagentLocalRLPackageAttestationError(ValueError):
    pass


class EvoagentLocalRLPackageAttestor:
    """Attest one exact, reproducibly verified and governed native package."""

    @staticmethod
    def _forbidden_verifiers(
        package: LocalRLPackageManifest,
    ) -> set[str]:
        return {
            package.trainer_id,
            package.decision.decision_actor_id,
            package.baseline_evaluation.evaluator_id,
            *(item.evaluator_id for item in package.candidate_evaluations),
            *(item.actor_id for item in package.audit_events),
        }

    @staticmethod
    def _evidence_ready_at(package: LocalRLPackageManifest) -> datetime:
        return max(
            package.created_at,
            package.decision.decided_at,
            *(item.created_at for item in package.audit_events),
        )

    def attest(
        self,
        package: LocalRLPackageManifest,
        *,
        manager: LocalRLPackageManager,
        verified_by: str,
        verified_at: datetime,
        attestation_id: str,
    ) -> NativeLocalRLPackageAttestation:
        projection = EvoagentLocalRLPackageProjector(manager).project(package)
        if verified_by in self._forbidden_verifiers(package):
            raise EvoagentLocalRLPackageAttestationError(
                "Native Local RL verifier overlaps a training, evaluation, "
                "selection, registration, or audit evidence role."
            )
        if verified_at < self._evidence_ready_at(package):
            raise EvoagentLocalRLPackageAttestationError(
                "Native Local RL verification predates complete package evidence."
            )
        payload = {
            "attestation_id": attestation_id,
            "projection": projection,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "native_package_verified": True,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        attestation = NativeLocalRLPackageAttestation(
            **payload,
            attestation_hash=program_payload_hash(payload),
        )
        self.verify(package, manager=manager, attestation=attestation)
        return attestation

    @classmethod
    def verify(
        cls,
        package: LocalRLPackageManifest,
        *,
        manager: LocalRLPackageManager,
        attestation: NativeLocalRLPackageAttestation,
    ) -> bool:
        expected_projection = EvoagentLocalRLPackageProjector(manager).project(
            package
        )
        if attestation.projection != expected_projection:
            raise EvoagentLocalRLPackageAttestationError(
                "Native Local RL attestation projection differs from verified evidence."
            )
        if attestation.verified_by in cls._forbidden_verifiers(package):
            raise EvoagentLocalRLPackageAttestationError(
                "Native Local RL verifier overlaps a governed native evidence role."
            )
        if attestation.verified_at < cls._evidence_ready_at(package):
            raise EvoagentLocalRLPackageAttestationError(
                "Native Local RL attestation predates complete package evidence."
            )
        if (
            attestation.native_package_verified is not True
            or attestation.checkpoint_promotion_authorized
            or attestation.production_activation_authorized
        ):
            raise EvoagentLocalRLPackageAttestationError(
                "Native Local RL attestation widens its verification-only authority."
            )
        expected_hash = program_payload_hash(
            attestation.model_dump(
                mode="json",
                exclude={"attestation_hash"},
            )
        )
        if attestation.attestation_hash != expected_hash:
            raise EvoagentLocalRLPackageAttestationError(
                "Native Local RL attestation hash mismatch."
            )
        return True


__all__ = [
    "EvoagentLocalRLPackageAttestationError",
    "EvoagentLocalRLPackageAttestor",
    "EvoagentLocalRLPackageProjector",
]
