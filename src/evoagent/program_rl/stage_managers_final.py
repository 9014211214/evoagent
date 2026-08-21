from __future__ import annotations

from evoagent.program_rl.attested_package import (
    AttestedProgramLocalRLBindingPackage,
    AttestedProgramLocalRLPackageError,
    AttestedProgramLocalRLPackageManager as _AttestedManager,
    AttestedProgramLocalRLResultBinding,
)
from evoagent.program_rl.package_verified_public_final import (
    ProgramLocalRLPackageManager,
)
from evoagent.program_rl.runtime_attested_package import (
    RuntimeAttestedProgramLocalRLBindingPackage,
    RuntimeAttestedProgramLocalRLPackageError,
)
from evoagent.program_rl.runtime_attested_package_final import (
    RuntimeAttestedProgramLocalRLPackageManager as _RuntimeManager,
)
from evoagent.program_rl.schema_attested_package import (
    SchemaAttestedProgramLocalRLBindingPackage,
    SchemaAttestedProgramLocalRLPackageError,
    SchemaAttestedProgramLocalRLPackageManager as _SchemaManager,
)


class AttestedProgramLocalRLPackageManager(_AttestedManager):
    """Public attested stage with recursive base-package verification."""

    @staticmethod
    def verify(package: AttestedProgramLocalRLBindingPackage) -> bool:
        ProgramLocalRLPackageManager.verify(package.base_package)
        return _AttestedManager.verify(package)


class SchemaAttestedProgramLocalRLPackageManager(_SchemaManager):
    """Public schema stage with recursive attested-package verification."""

    @staticmethod
    def verify(package: SchemaAttestedProgramLocalRLBindingPackage) -> bool:
        AttestedProgramLocalRLPackageManager.verify(package.attested_package)
        return _SchemaManager.verify(package)


class RuntimeAttestedProgramLocalRLPackageManager(_RuntimeManager):
    """Public runtime stage with recursive schema-package verification."""

    @staticmethod
    def verify(package: RuntimeAttestedProgramLocalRLBindingPackage) -> bool:
        SchemaAttestedProgramLocalRLPackageManager.verify(
            package.schema_attested_package
        )
        return _RuntimeManager.verify(package)


__all__ = [
    "AttestedProgramLocalRLBindingPackage",
    "AttestedProgramLocalRLPackageError",
    "AttestedProgramLocalRLPackageManager",
    "AttestedProgramLocalRLResultBinding",
    "RuntimeAttestedProgramLocalRLBindingPackage",
    "RuntimeAttestedProgramLocalRLPackageError",
    "RuntimeAttestedProgramLocalRLPackageManager",
    "SchemaAttestedProgramLocalRLBindingPackage",
    "SchemaAttestedProgramLocalRLPackageError",
    "SchemaAttestedProgramLocalRLPackageManager",
]
