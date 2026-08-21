from __future__ import annotations

from evoagent.program_rl.models import ProgramLocalRLBindingPackage
from evoagent.program_rl.package import (
    ProgramLocalRLPackageManager as _BasePackageManager,
)
from evoagent.program_rl.package_verified_final import (
    ProgramLocalRLPackageManager as _RecursiveVerifier,
)


class ProgramLocalRLPackageManager(_BasePackageManager):
    """Public base-package Manager with recursive verification and full I/O API."""

    @staticmethod
    def verify(package: ProgramLocalRLBindingPackage) -> bool:
        return _RecursiveVerifier.verify(package)


__all__ = ["ProgramLocalRLPackageManager"]
