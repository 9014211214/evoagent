from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
)


def test_public_program_contracts_use_the_consolidated_final_modules():
    assert EvolutionProgramController.__module__ == (
        "evoagent.program.controller_program_attestation_final"
    )
    assert EvolutionProgramPackageManager.__module__ == (
        "evoagent.program.package_provenance_hardened_final"
    )


def test_public_program_contracts_do_not_fall_back_to_internal_layers():
    forbidden_controller_modules = {
        "evoagent.program.controller",
        "evoagent.program.controller_hardened",
        "evoagent.program.controller_retry_hardened",
        "evoagent.program.controller_evidence_hardened",
    }
    forbidden_package_modules = {
        "evoagent.program.package",
        "evoagent.program.package_hardened",
        "evoagent.program.package_policy_hardened",
        "evoagent.program.package_audit_hardened",
        "evoagent.program.package_provenance_hardened",
    }
    assert EvolutionProgramController.__module__ not in forbidden_controller_modules
    assert EvolutionProgramPackageManager.__module__ not in forbidden_package_modules
