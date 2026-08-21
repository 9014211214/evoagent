from evoagent.integrated import (
    ControlledCompositeRuntimeEvaluator,
    GovernedLocalPolicyEvolutionExecutor,
    GovernedSkillEvolutionExecutor,
    IntegratedCaseFactoryError,
    PreviewingLocalPolicyRegistry,
    build_integrated_cases_from_initial_evaluation,
    prepare_controlled_initial_skill,
)
from evoagent.lab import IntegratedMultiTrackEvolutionLab
from evoagent.local_rl import ProgramLocalRLProjectionPackageManager
from evoagent.program import EvolutionProgramController


def test_integrated_runtime_public_api_is_final_and_cycle_safe():
    assert GovernedSkillEvolutionExecutor.__module__ == (
        "evoagent.integrated.executors"
    )
    assert GovernedLocalPolicyEvolutionExecutor.__module__ == (
        "evoagent.integrated.executors"
    )
    assert ControlledCompositeRuntimeEvaluator.__module__ == (
        "evoagent.integrated.controlled_runtime"
    )
    assert PreviewingLocalPolicyRegistry.__module__ == (
        "evoagent.integrated.initial_state"
    )
    assert prepare_controlled_initial_skill.__module__ == (
        "evoagent.integrated.initial_state"
    )
    assert build_integrated_cases_from_initial_evaluation.__module__ == (
        "evoagent.integrated.case_factory"
    )
    assert IntegratedCaseFactoryError.__module__ == (
        "evoagent.integrated.case_factory"
    )


def test_final_lab_and_prerequisite_public_contracts_are_installed():
    assert IntegratedMultiTrackEvolutionLab.__module__ == (
        "evoagent.lab.integrated_multitrack_final"
    )
    assert ProgramLocalRLProjectionPackageManager.__module__ == (
        "evoagent.local_rl.program_projection"
    )
    assert EvolutionProgramController.__module__ == (
        "evoagent.program.controller_program_attestation_final"
    )
