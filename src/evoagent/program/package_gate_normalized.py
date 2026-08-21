from __future__ import annotations

import evoagent.program.package as base_package
from evoagent.program.gate_final_hardened import HardenedEvolutionProgramGate
from evoagent.program.package import EvolutionProgramPackageManifest
from evoagent.program.package_hardened import (
    HardenedEvolutionProgramPackageManager,
)


# Package verification performs a module-global lookup at call time. Pin that
# lookup once during import rather than temporarily swapping it per request.
base_package.EvolutionProgramGate = HardenedEvolutionProgramGate


class GateNormalizedEvolutionProgramPackageManager(
    HardenedEvolutionProgramPackageManager
):
    """Run legacy base verification through the canonical hardened decision gate."""

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        return super().verify(manifest)


__all__ = ["GateNormalizedEvolutionProgramPackageManager"]
