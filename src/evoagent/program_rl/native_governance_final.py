from __future__ import annotations

from datetime import datetime

from evoagent.local_rl.package import (
    LocalRLPackageManager,
    LocalRLPackageManifest,
)
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attestation import NativeLocalRLPackageAttestation
from evoagent.program_rl.native_contract import (
    EvoagentLocalRLPackageProjector as _BaseProjector,
)


_EXPECTED_AUDIT_REASONS = (
    "Frozen local RL run manifest registered.",
    "Bounded local rollout optimization completed.",
    "Independent frozen held-out evaluations stored.",
    "Best safe improving local policy checkpoint selected.",
)


class EvoagentLocalRLPackageAttestationError(ValueError):
    pass


def _verify_native_governance(package: LocalRLPackageManifest) -> None:
    if len(package.audit_events) != 4:
        raise ValueError(
            "Native Local RL governance requires four lifecycle audit events."
        )
    evaluator_ids = {
        package.baseline_evaluation.evaluator_id,
        *(item.evaluator_id for item in package.candidate_evaluations),
    }
    if len(evaluator_ids) != 1:
        raise ValueError(
            "Native Local RL evaluations use multiple evaluator identities."
        )
    evaluator_id = next(iter(evaluator_ids))
    registrar, trained, evaluated, selected = package.audit_events
    expected_actors = (
        registrar.actor_id,
        package.trainer_id,
        evaluator_id,
        package.decision.decision_actor_id,
    )
    if len(set(expected_actors)) != 4:
        raise ValueError(
            "Native Local RL registrar, trainer, evaluator and selector "
            "must be independent."
        )
    if tuple(item.actor_id for item in package.audit_events) != expected_actors:
        raise ValueError(
            "Native Local RL audit actors differ from immutable evidence roles."
        )
    if tuple(item.reason for item in package.audit_events) != _EXPECTED_AUDIT_REASONS:
        raise ValueError(
            "Native Local RL audit reasons differ from the governed lifecycle."
        )
    timestamps = tuple(item.created_at for item in package.audit_events)
    if timestamps != tuple(sorted(timestamps)):
        raise ValueError("Native Local RL audit timestamps are not monotonic.")
    if (
        package.manifest.created_at > registrar.created_at
        or package.decision.decided_at != selected.created_at
        or selected.created_at > package.created_at
    ):
        raise ValueError(
            "Native Local RL package chronology differs from its audit evidence."
        )


class EvoagentLocalRLPackageProjector(_BaseProjector):
    """Final concrete Projector with native role and chronology governance."""

    def project(self, package: LocalRLPackageManifest):
        projection = super().project(package)
        _verify_native_governance(package)
        return projection


class EvoagentLocalRLPackageAttestor:
    """Attest exact verified native evidence under independent governance."""

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
                "selection, registration, or audit role."
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
