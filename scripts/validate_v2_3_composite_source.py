from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required v2.3 integrated file missing: {path}")
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(
            f"required v2.3 integrated marker missing from {path}: {marker}"
        )


for path, marker in (
    (
        "src/evoagent/composite/models.py",
        'Literal["evoagent-composite-snapshot-v1"]',
    ),
    (
        "src/evoagent/composite/models.py",
        "foundation_model_weights_updated: Literal[False]",
    ),
    (
        "src/evoagent/composite/builders.py",
        "Composite child must change exactly one governed component",
    ),
    (
        "src/evoagent/composite/repository.py",
        "Composite snapshot creator cannot commit its own pointer change",
    ),
    (
        "src/evoagent/composite/repository.py",
        "Composite audit semantics differ from committed snapshot lineage",
    ),
    (
        "src/evoagent/composite/service.py",
        "Composite manifest differs from the live component pointers",
    ),
    (
        "src/evoagent/composite/evaluation.py",
        'Literal["evoagent-composite-evaluation-v1"]',
    ),
    (
        "src/evoagent/composite/evaluation.py",
        "Frozen composite Tasks passed with zero safety violations",
    ),
    (
        "src/evoagent/composite/evaluation_repository.py",
        "Composite evaluation audit omits or duplicates lifecycle events",
    ),
    (
        "src/evoagent/integrated/models.py",
        'policy_id: str = Field(pattern=_SAFE_ID_PATTERN)',
    ),
    (
        "src/evoagent/integrated/models.py",
        "foundation_model_weights_updated: Literal[False]",
    ),
    (
        "src/evoagent/integrated/case_factory.py",
        "Exactly one bounded component counterfactual repaired the Task",
    ),
    (
        "src/evoagent/integrated/controlled_runtime.py",
        "class ControlledCompositeRuntimeEvaluator",
    ),
    (
        "src/evoagent/integrated/initial_state.py",
        "no rollout, gradient, update, or selection occurs",
    ),
    (
        "src/evoagent/integrated/executors.py",
        "class GovernedSkillEvolutionExecutor",
    ),
    (
        "src/evoagent/integrated/executors.py",
        "class GovernedLocalPolicyEvolutionExecutor",
    ),
    (
        "src/evoagent/integrated/repository_hardened.py",
        "canonical, retry-safe audit semantics",
    ),
    (
        "src/evoagent/integrated/service_hardened.py",
        "Integrated execution round differs from the active composite snapshot round",
    ),
    (
        "src/evoagent/integrated/package.py",
        'Literal["evoagent-integrated-evolution-package-v1"]',
    ),
    (
        "src/evoagent/integrated/package_hardened.py",
        "canonical lineage and audit replay",
    ),
    (
        "src/evoagent/program/controller_program_attestation_final.py",
        "CampaignCheckpoint",
    ),
    (
        "src/evoagent/program/controller_program_attestation_final.py",
        "ProgramCheckpoint",
    ),
    (
        "src/evoagent/program_rl/intent_binding.py",
        "running_attestation_payload",
    ),
    (
        "src/evoagent/local_rl/program_projection.py",
        "recursively embeds the real native package",
    ),
    (
        "src/evoagent/lab/program_local_rl_acceptance_final.py",
        "verify_persistent_running_state",
    ),
    (
        "src/evoagent/lab/integrated_multitrack_final.py",
        "exact per-invocation optimizer and resume evidence",
    ),
    (
        "tests/test_program_local_rl_acceptance_lab.py",
        "test_real_local_optimizer_reaches_complete_program_acceptance",
    ),
    (
        "tests/test_integrated_real_executors.py",
        "test_real_policy_executor_runs_optimizer_acceptance_and_v2_2_promotion",
    ),
    (
        "tests/test_integrated_multitrack_lab.py",
        "test_real_integrated_lab_reaches_a0_a1_a2_and_stops",
    ),
    (
        "tests/test_integrated_multitrack_lab.py",
        "test_second_integrated_invocation_is_fully_read_only",
    ),
    (
        "tests/test_integrated_public_contract.py",
        "test_lab_public_api_lazily_exposes_final_integrated_implementation",
    ),
    (
        "tests/test_program_attestation_public_contract.py",
        "test_program_public_controller_exposes_program_scoped_anchored_attestation",
    ),
    (
        "tests/test_v2_3_exact_head_gate_contract.py",
        "test_v2_3_exact_head_gate_covers_composite_foundation",
    ),
    (
        "scripts/run_v2_3_exact_head_gate.py",
        "focused_v2_3_integrated_regression",
    ),
    (
        "scripts/run_v2_3_exact_head_gate.py",
        "IntegratedMultiTrackEvolutionLab",
    ),
    (
        ".github/workflows/ci.yml",
        "permissions:\n  contents: read",
    ),
    (
        "docs/28-composite-snapshot-registry.md",
        "component changes do not silently move the composite pointer",
    ),
):
    require(path, marker)


PYTHON_SOURCES = (
    "src/evoagent/composite/__init__.py",
    "src/evoagent/composite/models.py",
    "src/evoagent/composite/builders.py",
    "src/evoagent/composite/repository.py",
    "src/evoagent/composite/service.py",
    "src/evoagent/composite/evaluation.py",
    "src/evoagent/composite/evaluation_repository.py",
    "src/evoagent/composite/evaluation_service.py",
    "src/evoagent/integrated/__init__.py",
    "src/evoagent/integrated/models.py",
    "src/evoagent/integrated/case_factory.py",
    "src/evoagent/integrated/controlled_runtime.py",
    "src/evoagent/integrated/initial_state.py",
    "src/evoagent/integrated/executors.py",
    "src/evoagent/integrated/repository.py",
    "src/evoagent/integrated/repository_hardened.py",
    "src/evoagent/integrated/service.py",
    "src/evoagent/integrated/service_hardened.py",
    "src/evoagent/integrated/package.py",
    "src/evoagent/integrated/package_hardened.py",
    "src/evoagent/program/controller_program_attestation_final.py",
    "src/evoagent/program_rl/intent_binding.py",
    "src/evoagent/local_rl/program_projection.py",
    "src/evoagent/local_rl/program_projection_schema.py",
    "src/evoagent/lab/program_local_rl_acceptance.py",
    "src/evoagent/lab/program_local_rl_acceptance_final.py",
    "src/evoagent/lab/integrated_multitrack.py",
    "src/evoagent/lab/integrated_multitrack_hardened.py",
    "src/evoagent/lab/integrated_multitrack_final.py",
    "tests/test_composite_snapshot_registry.py",
    "tests/test_composite_snapshot_service.py",
    "tests/test_composite_evaluation.py",
    "tests/test_composite_evaluation_repository.py",
    "tests/test_integrated_case_routing.py",
    "tests/test_integrated_repository.py",
    "tests/test_integrated_supervisor_service.py",
    "tests/test_integrated_real_executors.py",
    "tests/test_integrated_multitrack_lab.py",
    "tests/test_integrated_public_contract.py",
    "tests/test_program_local_rl_projection_package.py",
    "tests/test_program_local_rl_acceptance_lab.py",
    "tests/test_program_attestation_public_contract.py",
    "tests/test_v2_3_composite_source.py",
    "tests/test_v2_3_exact_head_gate_contract.py",
    "scripts/run_v2_3_exact_head_gate.py",
)

for relative in PYTHON_SOURCES:
    target = ROOT / relative
    if not target.is_file():
        raise SystemExit(f"required Python source missing: {relative}")
    try:
        ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except SyntaxError as exc:
        raise SystemExit(
            f"Python syntax error in {target.relative_to(ROOT)}: {exc}"
        ) from exc

print("v2.3 integrated multi-track source invariants verified")
