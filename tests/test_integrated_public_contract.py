from evoagent.integrated import (
    ControlledCompositeRuntimeEvaluator,
    GovernedLocalPolicyEvolutionExecutor,
    GovernedSkillEvolutionExecutor,
    IntegratedDispatchPlan,
    IntegratedEvolutionPackageManager,
    IntegratedEvolutionPackageManifest,
    IntegratedRunPolicy,
    IntegratedSupervisorService,
    IntegratedTrackResult,
    SQLiteIntegratedEvolutionRepository,
    StaleIntegratedRevision,
    build_integrated_case,
    build_integrated_run_policy,
)
from evoagent.lab import (
    IntegratedMultiTrackEvolutionLab,
    IntegratedMultiTrackLabResult,
)


def test_integrated_public_api_exposes_hardened_repository_and_supervisor():
    assert SQLiteIntegratedEvolutionRepository.__module__ == (
        "evoagent.integrated.repository_hardened"
    )
    assert IntegratedSupervisorService.__module__ == (
        "evoagent.integrated.service_hardened"
    )
    assert StaleIntegratedRevision.__module__ == (
        "evoagent.integrated.repository"
    )
    assert IntegratedDispatchPlan.__module__ == (
        "evoagent.integrated.service"
    )


def test_integrated_public_api_exposes_immutable_case_and_result_contracts():
    assert IntegratedRunPolicy.__module__ == (
        "evoagent.integrated.models"
    )
    assert IntegratedTrackResult.__module__ == (
        "evoagent.integrated.models"
    )
    assert build_integrated_case.__module__ == (
        "evoagent.integrated.models"
    )
    assert build_integrated_run_policy.__module__ == (
        "evoagent.integrated.models"
    )


def test_integrated_public_api_exposes_final_real_execution_path():
    assert GovernedSkillEvolutionExecutor.__module__ == (
        "evoagent.integrated.executors"
    )
    assert GovernedLocalPolicyEvolutionExecutor.__module__ == (
        "evoagent.integrated.executors"
    )
    assert ControlledCompositeRuntimeEvaluator.__module__ == (
        "evoagent.integrated.controlled_runtime"
    )
    assert IntegratedEvolutionPackageManifest.__module__ == (
        "evoagent.integrated.package"
    )
    assert IntegratedEvolutionPackageManager.__module__ == (
        "evoagent.integrated.package_hardened"
    )


def test_lab_public_api_lazily_exposes_final_integrated_implementation():
    assert IntegratedMultiTrackEvolutionLab.__module__ == (
        "evoagent.lab.integrated_multitrack_final"
    )
    assert IntegratedMultiTrackLabResult.__module__ == (
        "evoagent.lab.integrated_multitrack"
    )
