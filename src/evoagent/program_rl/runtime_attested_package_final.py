from __future__ import annotations

from evoagent.program_rl.runtime_attested_package import (
    RuntimeAttestedProgramLocalRLBindingPackage,
    RuntimeAttestedProgramLocalRLPackageError,
    RuntimeAttestedProgramLocalRLPackageManager as _RuntimeManager,
)


class RuntimeAttestedProgramLocalRLPackageManager(_RuntimeManager):
    """Final runtime-attested manager with reviewer independence."""

    @staticmethod
    def verify(package: RuntimeAttestedProgramLocalRLBindingPackage) -> bool:
        _RuntimeManager.verify(package)
        schema_package = package.schema_attested_package
        attested = schema_package.attested_package
        runtime = package.runtime_attestation
        base = attested.base_package
        intent = base.intent
        reviewer_forbidden = {
            *intent.governed_actor_ids,
            intent.created_by,
            base.authorization.authorized_by,
            base.result.executed_by,
            runtime.schema_attestation.base_attestation.verified_by,
            attested.attested_result.bound_by,
            runtime.schema_attestation.projection_spec.created_by,
        }
        if runtime.runtime_contract.reviewed_by in reviewer_forbidden:
            raise RuntimeAttestedProgramLocalRLPackageError(
                "Native runtime contract reviewer overlaps a governed role."
            )
        return True

    def build(self, **kwargs) -> RuntimeAttestedProgramLocalRLBindingPackage:
        package = super().build(**kwargs)
        self.verify(package)
        return package


__all__ = [
    "RuntimeAttestedProgramLocalRLBindingPackage",
    "RuntimeAttestedProgramLocalRLPackageError",
    "RuntimeAttestedProgramLocalRLPackageManager",
]
