from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
)
from evoagent.program.controller_evidence_hardened_final import (
    RetryHardenedEvolutionProgramController as UniqueEvidenceController,
)
from evoagent.program.controller_final_hardened import (
    RetryHardenedEvolutionProgramController as ReleaseIngressController,
)
from evoagent.program.controller_program_attestation_final import (
    RetryHardenedEvolutionProgramController as AttestedController,
)
from evoagent.program.controller_program_scope_final import (
    RetryHardenedEvolutionProgramController as ProgramScopedController,
)
from evoagent.program.controller_public_final import (
    RetryHardenedEvolutionProgramController as AuditBoundController,
)
from evoagent.program.controller_public_hardened import (
    RetryHardenedEvolutionProgramController as RetryRevalidationController,
)
from evoagent.program.package_provenance_hardened import (
    AuditHardenedEvolutionProgramPackageManager as ProvenancePackageManager,
)


def test_public_controller_uses_program_scoped_final_module():
    assert EvolutionProgramController is AttestedController
    assert EvolutionProgramController.__name__ == (
        "RetryHardenedEvolutionProgramController"
    )
    assert EvolutionProgramController.__module__.endswith(
        "controller_program_attestation_final"
    )
    assert issubclass(EvolutionProgramController, ProgramScopedController)
    assert issubclass(EvolutionProgramController, AuditBoundController)
    assert issubclass(EvolutionProgramController, RetryRevalidationController)
    assert issubclass(EvolutionProgramController, UniqueEvidenceController)
    assert issubclass(EvolutionProgramController, ReleaseIngressController)


def test_public_package_manager_uses_recovery_aware_provenance_module():
    assert EvolutionProgramPackageManager.__name__ == (
        "AuditHardenedEvolutionProgramPackageManager"
    )
    assert EvolutionProgramPackageManager.__module__.endswith(
        "package_provenance_hardened_final"
    )
    assert issubclass(EvolutionProgramPackageManager, ProvenancePackageManager)
