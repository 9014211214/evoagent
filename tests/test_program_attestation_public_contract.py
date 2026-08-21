from evoagent.program import (
    EvolutionProgramController,
    ProgramExecutionCheckpoint,
    RunningGenerationAttestation,
    RunningGenerationRoles,
    build_running_generation_attestation,
)


def test_program_public_controller_exposes_program_scoped_anchored_attestation():
    assert EvolutionProgramController.__module__ == (
        "evoagent.program.controller_program_attestation_final"
    )
    assert callable(
        getattr(EvolutionProgramController, "attest_running_generation")
    )


def test_running_generation_public_models_are_immutable_attestation_contracts():
    assert ProgramExecutionCheckpoint.__module__ == (
        "evoagent.program.execution_attestation"
    )
    assert RunningGenerationAttestation.__module__ == (
        "evoagent.program.execution_attestation"
    )
    assert RunningGenerationRoles.__module__ == (
        "evoagent.program.execution_attestation"
    )
    assert build_running_generation_attestation.__module__ == (
        "evoagent.program.execution_attestation"
    )
