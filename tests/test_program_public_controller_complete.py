from evoagent.program import EvolutionProgramController
from evoagent.program.controller_public_final import (
    RetryHardenedEvolutionProgramController as AuditBoundController,
)


def test_public_controller_uses_complete_retry_revalidation_layer():
    assert EvolutionProgramController.__name__ == (
        "RetryHardenedEvolutionProgramController"
    )
    assert EvolutionProgramController.__module__.endswith(
        "controller_program_attestation_final"
    )
    assert issubclass(EvolutionProgramController, AuditBoundController)
