from __future__ import annotations

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.intent_binding import (
    RunningAttestedProgramLocalRLBindingPackage,
    RunningAttestedProgramLocalRLPackageError,
    RunningAttestedProgramLocalRLPackageManager as _RunningPackageManager,
    RunningGenerationIntentBinding,
    RunningGenerationIntentBindingManager as _BindingManager,
)
from evoagent.program_rl.package import ProgramLocalRLPackageManager


class RunningGenerationIntentBindingManager(_BindingManager):
    """Build and explicitly reverify every nested intent-binding hash."""

    @staticmethod
    def verify(binding: RunningGenerationIntentBinding) -> bool:
        expected_intent_hash = program_payload_hash(
            binding.intent.model_dump(mode="json", exclude={"intent_hash"})
        )
        if binding.intent.intent_hash != expected_intent_hash:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested local-RL intent hash mismatch."
            )
        if binding.bound_by != binding.intent.created_by:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested binding actor differs from intent author."
            )
        if binding.bound_at != binding.intent.created_at:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested binding time differs from intent creation."
            )
        if binding.bound_at < binding.running_attested_at:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested binding predates its Generation attestation."
            )
        if binding.running_attestor_id not in set(
            binding.intent.governed_actor_ids
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running Generation attestor is absent from governed intent actors."
            )
        if (
            binding.optimizer_execution_authorized
            or binding.checkpoint_promotion_authorized
            or binding.production_activation_authorized
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested intent binding widens execution authority."
            )
        expected_binding_hash = program_payload_hash(
            binding.model_dump(mode="json", exclude={"binding_hash"})
        )
        if binding.binding_hash != expected_binding_hash:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running Generation intent binding hash mismatch."
            )
        return True

    def build(self, *args, **kwargs) -> RunningGenerationIntentBinding:
        binding = super().build(*args, **kwargs)
        self.verify(binding)
        return binding


class RunningAttestedProgramLocalRLPackageManager(_RunningPackageManager):
    """Final running-attested package manager with nested revalidation."""

    @staticmethod
    def verify(package: RunningAttestedProgramLocalRLBindingPackage) -> bool:
        ProgramLocalRLPackageManager.verify(package.base_package)
        RunningGenerationIntentBindingManager.verify(package.intent_binding)
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

    def build(self, **kwargs) -> RunningAttestedProgramLocalRLBindingPackage:
        package = super().build(**kwargs)
        self.verify(package)
        return package


__all__ = [
    "RunningAttestedProgramLocalRLBindingPackage",
    "RunningAttestedProgramLocalRLPackageError",
    "RunningAttestedProgramLocalRLPackageManager",
    "RunningGenerationIntentBinding",
    "RunningGenerationIntentBindingManager",
]
