from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "src/evoagent/integrated/models.py",
    "src/evoagent/integrated/repository_hardened.py",
    "src/evoagent/integrated/service_hardened.py",
    "src/evoagent/integrated/case_factory.py",
    "src/evoagent/integrated/initial_state.py",
    "src/evoagent/integrated/controlled_runtime.py",
    "src/evoagent/integrated/executors.py",
    "src/evoagent/integrated/package.py",
    "src/evoagent/lab/integrated_multitrack.py",
    "src/evoagent/lab/integrated_multitrack_hardened.py",
    "src/evoagent/lab/integrated_multitrack_final.py",
    "src/evoagent/lab/program_local_rl_acceptance.py",
    "src/evoagent/lab/program_local_rl_acceptance_final.py",
    "src/evoagent/local_rl/program_projection.py",
    "src/evoagent/program/controller_program_attestation_final.py",
    "tests/test_program_local_rl_projection_package.py",
    "tests/test_program_local_rl_acceptance_lab.py",
    "tests/test_integrated_real_executors.py",
    "tests/test_integrated_multitrack_lab.py",
    "tests/test_integrated_runtime_public_contract.py",
    "tests/test_integrated_repository_semantic_hardening.py",
)

REQUIRED_TOKENS = {
    "src/evoagent/integrated/executors.py": (
        "GovernedSkillEvolutionExecutor",
        "GovernedLocalPolicyEvolutionExecutor",
        "AutomaticLocalToolEvolutionLab",
        "ProgramLocalRLAcceptanceLab",
        "AcceptedLocalPolicyPromotionLab",
        "local_policy_optimized=True",
        "local_policy_promoted=True",
        "local_policy_activated=True",
        "rollback_ready=True",
        "perform_rollback=False",
    ),
    "src/evoagent/integrated/controlled_runtime.py": (
        "ToolAgentRuntime",
        "IndependentLocalPolicyEvaluator",
        "DocumentTaskVerifier",
        "CompositeTaskTrack.SKILL",
        "CompositeTaskTrack.LOCAL_POLICY",
    ),
    "src/evoagent/integrated/package.py": (
        "IntegratedEvolutionPackageManifest",
        "0.25, 0.5, 1.0",
        "CompositeStopAction.CONTINUE",
        "CompositeStopAction.STOP",
        "foundation_model_training_performed",
        "production_activation_performed",
        "production_deployment_performed",
        "external_rollout_performed",
    ),
    "src/evoagent/lab/integrated_multitrack.py": (
        "A0 -> A1 -> A2",
        "_ensure_skill_round",
        "_ensure_local_policy_round",
        "_ensure_integrated_completion",
        "IntegratedEvolutionPackageManager",
    ),
    "src/evoagent/lab/integrated_multitrack_hardened.py": (
        "if self.package_path.exists():",
        "self._verify_persistent_state(package)",
        "self._verify_child_resume(package)",
        "Persistent integrated state differs from immutable package.",
        "Persistent Skill state differs from integrated package.",
    ),
    "src/evoagent/lab/integrated_multitrack_final.py": (
        "_HardenedIntegratedMultiTrackEvolutionLab",
        "_ensure_local_policy_round",
        "_verify_child_resume",
        "optimizer_invoked = not acceptance_path.exists()",
        "did not resume read-only.",
    ),
    "src/evoagent/lab/program_local_rl_acceptance.py": (
        "ProgramLocalRLAcceptedEvidenceBundle",
        "ProgramLocalRLAcceptanceManager",
        "LocalAgenticRLTrainingLab",
        "ProgramLocalRLAcceptanceManager().accept",
        "foundation_model_training_performed: Literal[False]",
        "external_model_call_performed: Literal[False]",
        "production_activation_performed",
    ),
    "src/evoagent/local_rl/program_projection.py": (
        "manager.verify(package)",
        "EvoagentLocalRLPackageProjector",
        "recomputed native evidence",
        "checkpoint_promotion_authorized: Literal[False]",
    ),
    "src/evoagent/integrated/repository_hardened.py": (
        "expected_claim_count",
        "In-flight integrated claim differs from crash-recovery evidence.",
        "Integrated audit omits, duplicates, or misbinds case admission.",
        "Unexecuted integrated case differs from admission state.",
        "Integrated FAILED state lacks a governed failure lifecycle.",
        "Integrated run update time differs from its lifecycle audit.",
        "active_revision_before",
        "Integrated audit chronology is not monotonic.",
    ),
}

FORBIDDEN_SNIPPETS = {
    "src/evoagent/integrated/models.py": (
        "foundation_model_weights_updated: Literal[True]",
        "production_activation_performed: Literal[True]",
        "production_deployment_performed: Literal[True]",
        "external_execution_performed: Literal[True]",
    ),
    "src/evoagent/integrated/package.py": (
        "foundation_model_training_performed: Literal[True]",
        "production_activation_performed: Literal[True]",
        "production_deployment_performed: Literal[True]",
        "external_rollout_performed: Literal[True]",
        "official_benchmark_claimed: Literal[True]",
    ),
}


def _source(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"missing required v2.3 file: {path}")
    text = target.read_text(encoding="utf-8")
    try:
        ast.parse(text, filename=path)
    except SyntaxError as exc:
        raise SystemExit(f"invalid Python syntax in {path}: {exc}") from exc
    return text


def main() -> int:
    sources = {path: _source(path) for path in REQUIRED_FILES}
    for path, tokens in REQUIRED_TOKENS.items():
        text = sources.get(path) or _source(path)
        missing = tuple(token for token in tokens if token not in text)
        if missing:
            raise SystemExit(
                f"{path} lacks required v2.3 source invariants: {missing}"
            )
    for path, snippets in FORBIDDEN_SNIPPETS.items():
        text = sources.get(path) or _source(path)
        present = tuple(item for item in snippets if item in text)
        if present:
            raise SystemExit(
                f"{path} widens controlled authority: {present}"
            )

    init_source = _source("src/evoagent/integrated/__init__.py")
    for symbol in (
        "GovernedSkillEvolutionExecutor",
        "GovernedLocalPolicyEvolutionExecutor",
        "ControlledCompositeRuntimeEvaluator",
        "build_integrated_cases_from_initial_evaluation",
    ):
        if symbol not in init_source:
            raise SystemExit(
                f"integrated public API does not expose required symbol: {symbol}"
            )

    lab_init = _source("src/evoagent/lab/__init__.py")
    if "integrated_multitrack_final" not in lab_init:
        raise SystemExit(
            "Lab public API does not expose the final integrated implementation."
        )

    print("v2.3 integrated source invariants passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
