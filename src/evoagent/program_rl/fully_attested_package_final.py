from __future__ import annotations

from evoagent.program_rl.fully_attested_package import (
    FullyAttestedProgramLocalRLBindingPackage,
    FullyAttestedProgramLocalRLPackageError,
    FullyAttestedProgramLocalRLPackageManager as _FullyManager,
)
from evoagent.program_rl.intent_binding_final import (
    RunningAttestedProgramLocalRLPackageManager,
)
from evoagent.program_rl.runtime_attested_package_final import (
    RuntimeAttestedProgramLocalRLPackageManager,
)


class FullyAttestedProgramLocalRLPackageManager(_FullyManager):
    """Final evidence manager with nested running/runtime revalidation."""

    @staticmethod
    def verify(package: FullyAttestedProgramLocalRLBindingPackage) -> bool:
        RunningAttestedProgramLocalRLPackageManager.verify(
            package.running_attested_package
        )
        RuntimeAttestedProgramLocalRLPackageManager.verify(
            package.runtime_attested_package
        )
        return _FullyManager.verify(package)

    def build(self, **kwargs) -> FullyAttestedProgramLocalRLBindingPackage:
        package = super().build(**kwargs)
        self.verify(package)
        return package


__all__ = [
    "FullyAttestedProgramLocalRLBindingPackage",
    "FullyAttestedProgramLocalRLPackageError",
    "FullyAttestedProgramLocalRLPackageManager",
]
