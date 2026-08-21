from evoagent.program import EvolutionProgramController
from evoagent.program.controller_evidence_hardened_final import (
    RetryHardenedEvolutionProgramController as UniqueEvidenceController,
)


def test_public_controller_enforces_unique_generation_evidence():
    assert EvolutionProgramController.__name__ == (
        "RetryHardenedEvolutionProgramController"
    )
    assert EvolutionProgramController.__module__.endswith(
        "controller_program_attestation_final"
    )
    assert issubclass(EvolutionProgramController, UniqueEvidenceController)
