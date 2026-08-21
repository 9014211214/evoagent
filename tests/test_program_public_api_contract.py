from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramGate,
    EvolutionProgramPackageManager,
    SQLiteEvolutionProgramRepository,
)


def test_public_program_api_uses_all_hardened_boundaries():
    assert EvolutionProgramController.__name__ == (
        "RetryHardenedEvolutionProgramController"
    )
    assert EvolutionProgramGate.__name__ == "HardenedEvolutionProgramGate"
    assert SQLiteEvolutionProgramRepository.__name__ == (
        "HardenedSQLiteEvolutionProgramRepository"
    )
    assert EvolutionProgramPackageManager.__name__ == (
        "AuditHardenedEvolutionProgramPackageManager"
    )
