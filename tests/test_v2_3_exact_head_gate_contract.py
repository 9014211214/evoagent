from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2_3_exact_head_gate_covers_composite_foundation_and_integrated_multitrack():
    gate_path = ROOT / "scripts" / "run_v2_3_exact_head_gate.py"
    namespace = runpy.run_path(
        str(gate_path),
        run_name="v2_3_exact_head_gate_contract",
    )

    required_tests = {
        "tests/test_program_attestation_public_contract.py",
        "tests/test_program_running_attestation.py",
        "tests/test_program_local_rl_projection_package.py",
        "tests/test_program_local_rl_acceptance_lab.py",
        "tests/test_composite_snapshot_registry.py",
        "tests/test_composite_snapshot_service.py",
        "tests/test_composite_evaluation.py",
        "tests/test_composite_evaluation_repository.py",
        "tests/test_composite_evaluation_public_tamper.py",
        "tests/test_composite_public_contract.py",
        "tests/test_integrated_case_routing.py",
        "tests/test_integrated_model_invariants.py",
        "tests/test_integrated_repository.py",
        "tests/test_integrated_repository_semantic_hardening.py",
        "tests/test_integrated_supervisor_service.py",
        "tests/test_integrated_real_executors.py",
        "tests/test_integrated_multitrack_lab.py",
        "tests/test_integrated_public_contract.py",
        "tests/test_integrated_runtime_public_contract.py",
        "tests/test_v2_3_composite_source.py",
        "tests/test_v2_3_exact_head_gate_contract.py",
        "tests/test_v2_3_integrated_gate_contract.py",
        "tests/test_v2_3_workflow_path_contract.py",
    }
    assert required_tests.issubset(set(namespace["FOCUSED_TESTS"]))
    assert namespace["SOURCE_GATES"] == (
        "scripts/validate_local_policy_promotion_source.py",
        "scripts/validate_local_policy_promotion_final_source.py",
        "scripts/validate_v2_3_composite_source.py",
        "scripts/validate_v2_3_integrated_source.py",
    )
    assert namespace["DEFAULT_REPORT"].name == (
        "v2.3-integrated-exact-head-gate.json"
    )

    gate_source = gate_path.read_text(encoding="utf-8")
    for marker in (
        '"focused_v2_3_integrated_regression"',
        '"full_regression"',
        '"build_wheel"',
        '"install_wheel"',
        '"pip_check"',
        '"installed_public_api"',
        '"mixed_case_supervisor_completed": passed',
        '"composite_a0_a1_a2_completed": passed',
        '"local_policy_optimization_performed": passed',
        '"second_run_read_only_verified": passed',
        '"foundation_model_training_performed": False',
        '"production_deployment_performed": False',
    ):
        assert marker in gate_source

    installed_code = namespace["_installed_api_code"]()
    for marker in (
        "evoagent.composite.repository",
        "evoagent.composite.evaluation_service",
        "evoagent.integrated.repository_hardened",
        "evoagent.integrated.service_hardened",
        "evoagent.integrated.executors",
        "evoagent.integrated.package_hardened",
        "evoagent.lab.integrated_multitrack_final",
        "evoagent.program.controller_program_attestation_final",
        "lifecycle_recovery_final",
        "package_semantic_final",
        "repository_chronology_final",
        "Installed v2.3 integrated multi-track public API verified",
    ):
        assert marker in installed_code
