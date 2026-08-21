from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from evoagent.lab import DEFAULT_THIRD_PARTY_LOCK_HASH


ROOT = Path(__file__).resolve().parents[1]


def require_file(path: str) -> Path:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required release file missing: {path}")
    return target


def require(path: str, needle: str) -> None:
    text = require_file(path).read_text(encoding="utf-8")
    if needle not in text:
        raise SystemExit(f"required release marker missing from {path}: {needle}")


def forbid(path: str, needle: str) -> None:
    text = require_file(path).read_text(encoding="utf-8")
    if needle in text:
        raise SystemExit(f"forbidden release marker remains in {path}: {needle}")


# Release alignment.
require("pyproject.toml", 'version = "2.0.0"')
require("pyproject.toml", '"pytest-xdist>=3.8,<3.9"')
require("README.md", "# Auto-Evolving Agent")
require("README.md", "**v2.0 Research Preview candidate.**")
require("README.md", "## Open-source release gates")
require("README.md", "## License")
require("CHANGELOG.md", "## 2.0.0 release candidate")
require("RELEASE_CHECKLIST.md", "## v2.0 persistent multi-generation Evolution Program")
require("AGENTS.md", "### Persistent multi-generation Evolution Program")
require("docs/23-multi-generation-evolution-program.md", "Release metrics identify where")
require(".github/workflows/ci.yml", "evoagent 2.0.0")
require(
    ".github/workflows/ci.yml",
    "pytest -q -n 2 --dist load --max-worker-restart=0 --durations=30",
)
require(".github/workflows/ci.yml", "examples/multi_generation_evolution_program.py")
require(".github/workflows/ci.yml", "evoagent.program")
require(".github/workflows/ci.yml", "MultiGenerationEvolutionProgramLab")
require("tests/test_package_release.py", '"2.0.0"')
forbid(".github/workflows/ci.yml", "evoagent 1.9.0")
forbid(".github/workflows/ci.yml", "1.0.0rc")

# v2.0 Program model, normalization, feedback, policy, persistence and governance.
require("src/evoagent/campaigns/models.py", 'EVOLUTION_GENERATION = "evolution_generation"')
require("src/evoagent/program/hashing.py", "def program_payload_hash")
require("src/evoagent/program/hashing.py", "to_jsonable_python")
require("src/evoagent/program/constraints.py", "BOUNDED_AUTOMATIC_INTERVENTION_LAYERS")
require("src/evoagent/program/constraints.py", "validate_governed_attribution_layer")
require("src/evoagent/program/constraints.py", "validate_single_release_package_budget")
require("src/evoagent/program/models.py", "class EvolutionProgramPolicy")
require("src/evoagent/program/models.py", "class ProgramLearningSignal")
require("src/evoagent/program/models.py", "causal_attribution_claimed")
require("src/evoagent/program/models.py", "class AttributionReceipt")
require("src/evoagent/program/models.py", "class GenerationPlan")
require("src/evoagent/program/models.py", "class GenerationOutcome")
require("src/evoagent/program/models.py", "class ProgramDecision")
require("src/evoagent/program/models.py", "class ProgramHead")
require("src/evoagent/program/feedback.py", "class ReleaseFeedbackExtractor")
require("src/evoagent/program/feedback.py", "target_agent_identity_hash")
require("src/evoagent/program/builders.py", "build_attribution_receipt")
require("src/evoagent/program/builders.py", "validate_bounded_automatic_layers")
require("src/evoagent/program/builders.py", "validate_governed_attribution_layer")
require("src/evoagent/program/builders.py", "validate_single_release_package_budget")
require("src/evoagent/program/builders.py", "program_payload_hash")
require("src/evoagent/program/repository.py", "class SQLiteEvolutionProgramRepository")
require("src/evoagent/program/repository_hardened.py", "remaining cumulative Program budget")
require("src/evoagent/program/repository_hardened.py", "Generation already completed with different immutable evidence")
require("src/evoagent/program/repository_hardened.py", "Campaign count differs from generation bindings")
require("src/evoagent/program/repository_hardened.py", "release-package budget is not representable")
require("src/evoagent/program/controller.py", "class EvolutionProgramController")
require("src/evoagent/program/controller_hardened.py", "class HardenedEvolutionProgramGate")
require("src/evoagent/program/controller_hardened.py", "cannot disable independent approvals")
require("src/evoagent/program/controller_hardened.py", "Verified single-layer attribution")
require("src/evoagent/program/controller_hardened.py", "actor cannot approve.")
require("src/evoagent/program/controller_hardened.py", "immutable bounded Program")
require("src/evoagent/program/controller_retry_hardened.py", "class RetryHardenedEvolutionProgramController")
require("src/evoagent/program/controller_retry_hardened.py", "cross-registry commit.")
require("src/evoagent/program/controller_retry_hardened.py", "partially created Generation Campaign")
require("src/evoagent/program/package.py", "class EvolutionProgramPackageManager")
require("src/evoagent/program/package_hardened.py", "Program audit event differs")
require("src/evoagent/program/package_hardened.py", "verified drift release package")
require("src/evoagent/program/package_hardened.py", "Program control head differs")
require("src/evoagent/program/package_policy_hardened.py", "hardened Program policy")
require("src/evoagent/program/package_policy_hardened.py", "widens automatic intervention authority")
require("src/evoagent/program/package_policy_hardened.py", "Attribution cardinality differs")
require("src/evoagent/program/package_policy_hardened.py", "unrepresentable package count")
require("src/evoagent/program/package_audit_hardened.py", "complete Program and Campaign audit lifecycle semantics")
require("src/evoagent/program/package_audit_hardened.py", "Program audit reason differs")
require("src/evoagent/program/package_audit_hardened.py", "evaluation-start event was substituted")
require("src/evoagent/program/package_audit_hardened.py", "approval identity, reason, time, or state")
require("src/evoagent/program/package_gate_normalized.py", "lookup once during import")
require("src/evoagent/program/__init__.py", "RetryHardenedEvolutionProgramController")
require("src/evoagent/program/__init__.py", "EvolutionProgramPackageManager")
require("src/evoagent/lab/evolution_program.py", "class MultiGenerationEvolutionProgramLab")
require("src/evoagent/lab/evolution_program.py", "Campaign authorization silently started")
require("src/evoagent/lab/evolution_program_hardened.py", "PLANNER")
require("src/evoagent/lab/__init__.py", "MultiGenerationEvolutionProgramLab")
require("examples/multi_generation_evolution_program.py", "authorization_started_generation")

# v2.0 tests exercise lifecycle, policy, identity, normalization and tamper boundaries.
for path, marker in (
    ("tests/test_multi_generation_program_lab.py", "stop_success"),
    ("tests/test_program_feedback_and_controls.py", "causal_attribution_claimed"),
    ("tests/test_program_registry_controls.py", "reuses_exact_decision"),
    ("tests/test_program_registry_controls.py", "campaign_count_tamper"),
    ("tests/test_program_controller_exact_retries.py", "partial_submission_recovers"),
    ("tests/test_program_controller_exact_retries.py", "partial_campaign_completion"),
    ("tests/test_program_package_tamper.py", "audit_tail_truncation"),
    ("tests/test_program_package_tamper.py", "approval_identity_reason_and_time"),
    ("tests/test_program_package_tamper.py", "child_target_identity"),
    ("tests/test_program_package_tamper.py", "budget_control_outcome"),
    ("tests/test_program_package_tamper.py", "ambiguous_control_head"),
    ("tests/test_program_package_tamper.py", "unbound_extra_control_attribution"),
    ("tests/test_program_package_tamper.py", "rehashed_program_reason"),
    ("tests/test_program_package_tamper.py", "rehashed_program_actor_substitution"),
    ("tests/test_program_package_tamper.py", "rehashed_campaign_transition_payload"),
    ("tests/test_program_package_tamper.py", "rehashed_campaign_actor_substitution"),
    ("tests/test_program_identity_governance.py", "decision_planning_actor"),
    (
        "tests/test_program_builder_hash_normalization.py",
        "test_attribution_and_generation_plan_hashes_include_normalized_defaults",
    ),
    (
        "tests/test_program_builder_hash_normalization.py",
        "generation_plan_builder_requires_one_release_package",
    ),
    ("tests/test_program_hardened_policy.py", "stop_on_ready"),
    ("tests/test_program_layer_constraints.py", "non_bounded_automatic_layers"),
    ("tests/test_program_layer_constraints.py", "forged_safety_attribution"),
    ("tests/test_program_layer_constraints.py", "unrepresentable_release_package_count"),
    ("tests/test_program_public_api_contract.py", "RetryHardenedEvolutionProgramController"),
):
    require(path, marker)

# Existing governed layers remain present.
for path, marker in (
    ("src/evoagent/runtime/tool_agent.py", "class ToolAgentRuntime"),
    ("src/evoagent/runtime/fault_matrix.py", "FailureLayer.MODEL"),
    ("src/evoagent/skills/sqlite_registry.py", "class SQLiteSkillRegistry"),
    ("src/evoagent/model_registry/sqlite_registry.py", "class SQLiteModelRegistry"),
    ("src/evoagent/supervisor/repository.py", "class SQLiteSupervisorRepository"),
    ("src/evoagent/benchmark_evidence/repository.py", "class SQLiteBenchmarkEvidenceRepository"),
    ("src/evoagent/champion/repository.py", "class SQLiteChampionRegistry"),
    ("src/evoagent/release/repository.py", "class SQLiteReleaseRegistry"),
    ("src/evoagent/lab/release_control.py", "class ShadowCanaryReleaseLab"),
    ("src/evoagent/lab/champion_promotion.py", "class BenchmarkGatedChampionLab"),
    ("src/evoagent/lab/benchmark_evidence.py", "class AuthoritativeBenchmarkEvidenceLab"),
    ("src/evoagent/lab/closed_loop_supervisor.py", "class ClosedLoopEvolutionSupervisorLab"),
    ("src/evoagent/lab/model_candidate_admission.py", "class ModelCandidateAdmissionLab"),
    ("src/evoagent/lab/model_evolution.py", "class GovernedModelEvolutionLab"),
    ("src/evoagent/lab/cross_layer.py", "class ExecutableCrossLayerAttributionLab"),
    ("src/evoagent/lab/automatic_local_tool.py", "class AutomaticLocalToolEvolutionLab"),
    ("src/evoagent/lab/service.py", "class ReferenceEvolutionLab"),
):
    require(path, marker)

# Every Python file in the Program package, labs, tests, examples and validator parses.
for root in (
    ROOT / "src" / "evoagent" / "program",
    ROOT / "src" / "evoagent" / "lab",
    ROOT / "tests",
    ROOT / "examples",
    ROOT / "scripts",
):
    for target in root.rglob("*.py"):
        try:
            ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        except SyntaxError as exc:
            raise SystemExit(f"Python syntax error in {target.relative_to(ROOT)}: {exc}") from exc

# Core must not import concrete Harbor, training, cloud, or serving implementations.
for target in (ROOT / "src" / "evoagent").rglob("*.py"):
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            modules = ((node.module or ""),)
        else:
            continue
        forbidden_roots = (
            "harbor",
            "ml_intern",
            "kubernetes",
            "boto3",
        )
        if any(
            name == prefix or name.startswith(prefix + ".")
            for name in modules
            for prefix in forbidden_roots
        ):
            raise SystemExit(
                f"forbidden concrete external implementation import in {target.relative_to(ROOT)}: {modules}"
            )

# Pinned third-party compliance remains exact.
lock = json.loads((ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"))
components = {item["name"]: item for item in lock["components"]}
required_components = {
    "Harbor",
    "ml-intern",
    "Resource2Skill",
    "Skill Recorder",
    "Terminal-Bench 2.1",
}
if set(components) != required_components:
    raise SystemExit("third-party lock component set changed")
if components["Harbor"]["reviewed_commit"] != (
    "0348989adffbb43bf0b410fd36197333239633f1"
):
    raise SystemExit("Harbor reviewed commit changed")
if components["Terminal-Bench 2.1"]["reviewed_commit"] != (
    "ffccbe05ee73a9d59518217f294ad711bda39304"
):
    raise SystemExit("Terminal-Bench reviewed commit changed")
if DEFAULT_THIRD_PARTY_LOCK_HASH != (
    "38d9b1efad86df11a45c201d23299a819ec2494592e93da6b660b03dd24f33bb"
):
    raise SystemExit("runtime third-party lock constant changed")

# Mergeable source contains no temporary probes, one-time gates, write-enabled
# workflows, privileged pull-request triggers, or mutable action references.
for path in (ROOT / "src").rglob("*.tmp*"):
    raise SystemExit(f"temporary source probe remains: {path.relative_to(ROOT)}")
workflow_root = ROOT / ".github" / "workflows"
allowed_workflows = {
    "ci.yml",
    "release-readiness.yml",
    "skillevolbench-benchmark.yml",
}
workflow_names = {path.name for path in workflow_root.glob("*.yml")}
if workflow_names != allowed_workflows:
    raise SystemExit(
        "mergeable workflow set changed: "
        f"expected {sorted(allowed_workflows)}, found {sorted(workflow_names)}"
    )
for workflow in workflow_root.glob("*.yml"):
    text = workflow.read_text(encoding="utf-8")
    if "contents: write" in text:
        raise SystemExit(f"write-enabled workflow remains: {workflow.name}")
    if "pull_request_target:" in text:
        raise SystemExit(f"privileged pull-request workflow remains: {workflow.name}")
    for line in text.splitlines():
        match = re.search(r"uses:\s+actions/[^@\s]+@([^\s#]+)", line)
        if match and not re.fullmatch(r"[0-9a-f]{40}", match.group(1)):
            raise SystemExit(
                f"mutable official action reference remains in {workflow.name}: "
                f"{match.group(1)}"
            )

# Core licensing is explicit; repository visibility remains a separate owner decision.
require("LICENSE", "Apache License")
require("LICENSE", "Version 2.0, January 2004")

print("v2.0.0 core and v2.0 Research Preview source invariants verified")
