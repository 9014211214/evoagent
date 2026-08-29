from __future__ import annotations

import ast
import hashlib
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
require("README.md", "**v2.0 Research Preview source snapshot.**")
require("README.md", "## Publication and performance-claim gates")
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
    "Harbor (SEAGym runtime)",
    "MiMoCode",
    "SEAGym",
    "ml-intern",
    "Resource2Skill",
    "Skill Recorder",
    "Terminal-Bench 2.0",
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
expected_pilot_components = {
    "SEAGym": "9e61e14db1f1355de944cd7c5b10c244fc74e82d",
    "Harbor (SEAGym runtime)": "f7110f1a240c6a50589b90c4d69714763946d088",
    "Terminal-Bench 2.0": "2fd12b88aafdd04a52c298e3940bcb189f9766d6",
    "MiMoCode": "67c9cf1e26288d03c65fb844be71f39581ffc1de",
}
for component_name, reviewed_commit in expected_pilot_components.items():
    if components[component_name]["reviewed_commit"] != reviewed_commit:
        raise SystemExit(f"{component_name} reviewed commit changed")
if DEFAULT_THIRD_PARTY_LOCK_HASH != (
    "ec92454da839390ec656765730f575b88984f71682a1c05ed111cae9fb57227f"
):
    raise SystemExit("runtime third-party lock constant changed")

# The SEAGym + Terminal-Bench pilot remains a score-blind, non-promoting,
# hash-bound preregistration rather than a mutable benchmark recipe.
pilot_root = ROOT / "experiments" / "seagym_terminalbench"
protocol = json.loads(
    require_file("experiments/seagym_terminalbench/protocol.json").read_text(
        encoding="utf-8"
    )
)
expected_pilot_artifacts = {
    "config": (
        "experiments/seagym_terminalbench/configs/evoagent_mimo_v2_5_seed42.json",
        "b298c9ab579616554d5b22d6077b1eb8fe86ff490ac21df175f729b01647fb17",
    ),
    "task_index": (
        "experiments/seagym_terminalbench/tasks/task_index.json",
        "bf604dc369ec25fd14d89aa3b2f7994f42e68093d671075306ba9312bdd1f6f9",
    ),
    "split": (
        "experiments/seagym_terminalbench/splits/seed42.json",
        "354e1677862ff598d751f4622d2804232ae40e2fb2be92eee689f3578dcfc6ec",
    ),
    "seagym_redaction_patch": (
        "experiments/seagym_terminalbench/patches/seagym-token-count-redaction.patch",
        "0c5302339bdcbeec076796b38f6ffd81803ce7f40cec1922c410294e8472018c",
    ),
}
for artifact_name, (artifact_path, expected_sha256) in (
    expected_pilot_artifacts.items()
):
    artifact = protocol["artifacts"][artifact_name]
    if artifact["path"] != artifact_path or artifact["sha256"] != expected_sha256:
        raise SystemExit(f"SEAGym pilot {artifact_name} lock changed")
    actual_sha256 = hashlib.sha256(require_file(artifact_path).read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise SystemExit(f"SEAGym pilot {artifact_name} bytes changed")
expected_third_party_lock_file_sha256 = (
    "0fae2820ba1056f4812a25a085162fdb7b3c75a9351f2a03e1886a06887ce849"
)
actual_third_party_lock_file_sha256 = hashlib.sha256(
    (ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8").encode("utf-8")
).hexdigest()
if (
    protocol["artifacts"]["third_party_lock_sha256"]
    != expected_third_party_lock_file_sha256
    or actual_third_party_lock_file_sha256
    != expected_third_party_lock_file_sha256
):
    raise SystemExit("SEAGym pilot third-party lock binding changed")
if protocol["upstream"] != {
    "harbor_seagym_gitlink": "f7110f1a240c6a50589b90c4d69714763946d088",
    "seagym": "9e61e14db1f1355de944cd7c5b10c244fc74e82d",
    "terminal_bench_2": "2fd12b88aafdd04a52c298e3940bcb189f9766d6",
}:
    raise SystemExit("SEAGym pilot upstream pins changed")

expected_route_contract = {
    "accepted_response_models": [
        "xiaomi/mimo-v2.5",
        "xiaomi/mimo-v2.5-20260422",
    ],
    "provider": {
        "allow_fallbacks": False,
        "only": ["xiaomi/fp8"],
        "require_parameters": True,
    },
    "reasoning": {"enabled": False},
    "response_provider": "Xiaomi",
}
model_route = protocol["model_route"]
if model_route != {
    "harbor_model": "openrouter/xiaomi/mimo-v2.5",
    "request_model": "xiaomi/mimo-v2.5",
    "route_contract": expected_route_contract,
    "provider_rollout_sampling_determinism_claimed": False,
    "router_audit": {
        "accepted_strategies": ["alias", "direct"],
        "cache_enabled": False,
        "material_pipeline_stages_allowed": False,
        "metadata_required": True,
        "successful_attempt": 1,
    },
    "same_model_for_update_and_rollout": True,
}:
    raise SystemExit("SEAGym pilot model route changed")
if protocol["runtime"]["mimocode"] != {
    "asset": "mimocode-linux-x64.tar.gz",
    "asset_bytes": 46489490,
    "asset_sha256": "0997a43647a99969d0194fad71af1fd6112aa8220e24a4562aea63953b1e1ada",
    "asset_url": (
        "https://github.com/XiaomiMiMo/MiMo-Code/releases/download/"
        "v0.1.13/mimocode-linux-x64.tar.gz"
    ),
    "commit": "67c9cf1e26288d03c65fb844be71f39581ffc1de",
    "version": "0.1.13",
}:
    raise SystemExit("SEAGym pilot MiMoCode runtime changed")
if protocol["runtime"]["secret_environment_variable"] != "OPENROUTER_API_KEY":
    raise SystemExit("SEAGym pilot credential boundary changed")
if protocol["runtime"]["credential_transport"] != {
    "account_key_in_task_container": False,
    "container_credential_kind": "ephemeral_proxy_capability",
    "kind": "host_guard_proxy",
    "proxy_base_url": "http://evoagent-openrouter-proxy:18765/api/v1",
}:
    raise SystemExit("SEAGym pilot credential transport changed")
if protocol["runtime"]["privacy_sanitizer"] != {
    "raw_jsonl_max_bytes": 64 * 1024 * 1024,
    "raw_persisted": False,
    "raw_record_max_bytes": 16 * 1024 * 1024,
    "raw_string_max_chars": 16 * 1024 * 1024,
}:
    raise SystemExit("SEAGym pilot privacy sanitizer bounds changed")
if protocol["runtime"]["token_semantics"] != {
    "harbor_cached_tokens": "cache_read_subset_of_harbor_input_tokens",
    "harbor_input_tokens": "non_cached_input_plus_cache_read",
    "reported_attested_total_tokens": "harbor_input_tokens_plus_output_tokens",
    "seagym_total_tokens": "harbor_input_tokens_plus_harbor_cached_tokens_plus_output_tokens",
}:
    raise SystemExit("SEAGym pilot token semantics changed")
if protocol["schedule"]["seed_semantics"] != {
    "controls": [
        "task_split",
        "frozen_batch_order",
        "update_request",
        "checkpoint_and_trial_attestation",
    ],
    "provider_rollout_sampling_determinism_claimed": False,
}:
    raise SystemExit("SEAGym pilot seed semantics changed")
if protocol["claim_boundary"] != {
    "automatic_promotion": False,
    "causal_attribution_claimed": False,
    "leaderboard_submission": False,
    "paper_scale_reproduction": False,
    "pilot_kind": "real_external_scientific_pilot",
    "results_status": "preregistered_not_yet_executed",
}:
    raise SystemExit("SEAGym pilot claim boundary changed")

pilot_config = json.loads(
    require_file(expected_pilot_artifacts["config"][0]).read_text(encoding="utf-8")
)
if pilot_config["seed"] != 42 or pilot_config["dataloader"] != {
    "batching_strategy": "shuffle",
    "drop_last": False,
    "seed": 42,
    "shuffle_train": False,
}:
    raise SystemExit("SEAGym pilot seed or frozen order changed")
if pilot_config["schedule"] != {
    "batch_size": 3,
    "num_epochs": 1,
    "num_updates_per_batch": 1,
    "test_size": 3,
    "train_size": 6,
    "val_size": 3,
}:
    raise SystemExit("SEAGym pilot schedule changed")
if (
    pilot_config["backend"]["env"] != "docker"
    or pilot_config["backend"]["n_concurrent"] != 2
    or pilot_config["backend"]["agent_override_timeout_sec"] != 1800
    or pilot_config["backend"]["verifier_override_timeout_sec"] != 600
):
    raise SystemExit("SEAGym pilot Harbor resource identity changed")
baseline = pilot_config["baseline"]
rollout = pilot_config["rollout_agent"]
if baseline["class_path"] != "seagym_evoagent.baseline:EvoAgentSEAGymBaseline":
    raise SystemExit("SEAGym EvoAgent baseline class changed")
if baseline["config"]["route_contract"] != expected_route_contract:
    raise SystemExit("SEAGym update route contract changed")
if baseline["config"].get("seed") != 42:
    raise SystemExit("SEAGym update seed changed")
if baseline["config"].get("fail_on_update_error") is not True:
    raise SystemExit("SEAGym update failure must stop the paid pilot")
if baseline["models"]["update_model"]["model"] != "xiaomi/mimo-v2.5":
    raise SystemExit("SEAGym update model changed")
if (
    rollout["class_path"]
    != "seagym.rollout_agents.harbor:HarborRolloutAgent"
    or rollout["config"]["agent"] != "evoagent-mimo"
    or rollout["config"]["import_path"]
    != "seagym_evoagent.harbor_agent:EvoAgentMiMo"
):
    raise SystemExit("SEAGym rollout Agent boundary changed")
if rollout["config"]["kwargs"]["route_contract"] != expected_route_contract:
    raise SystemExit("SEAGym rollout route contract changed")
if rollout["config"]["kwargs"].get("seed") != 42:
    raise SystemExit("SEAGym rollout seed changed")
rollout_model = rollout["models"]["rollout_model"]
if (
    rollout_model["model"] != "openrouter/xiaomi/mimo-v2.5"
    or rollout_model["exports"]["LLM_MODEL"]
    != "openrouter/xiaomi/mimo-v2.5"
    or rollout_model["api_key_env"] != "EVOAGENT_MIMOCODE_PROXY_TOKEN"
    or rollout_model["api_base"]
    != "http://evoagent-openrouter-proxy:18765/api/v1"
    or rollout_model["exports"]["OPENROUTER_API_KEY"] != "{api_key}"
):
    raise SystemExit("SEAGym Harbor MiMo model binding changed")

expected_split = {
    "train": [
        "terminal-bench/fix-git",
        "terminal-bench/log-summary-date-ranges",
        "terminal-bench/vulnerable-secret",
        "terminal-bench/pytorch-model-recovery",
        "terminal-bench/polyglot-c-py",
        "terminal-bench/configure-git-webserver",
    ],
    "val": [
        "terminal-bench/cancel-async-tasks",
        "terminal-bench/openssl-selfsigned-cert",
        "terminal-bench/multi-source-data-merger",
    ],
    "test": [
        "terminal-bench/regex-log",
        "terminal-bench/sanitize-git-repo",
        "terminal-bench/build-cython-ext",
    ],
}
pilot_split = json.loads(
    require_file(expected_pilot_artifacts["split"][0]).read_text(encoding="utf-8")
)
if pilot_split["seed"] != 42 or pilot_split["splits"] != expected_split:
    raise SystemExit("SEAGym pilot split changed")
all_split_ids = [
    task_id
    for split_name in ("train", "val", "test")
    for task_id in expected_split[split_name]
]
if len(set(all_split_ids)) != 12:
    raise SystemExit("SEAGym pilot split is not disjoint")

pilot_tasks = json.loads(
    require_file(expected_pilot_artifacts["task_index"][0]).read_text(
        encoding="utf-8"
    )
)
if [item["task_id"] for item in pilot_tasks["tasks"]] != all_split_ids:
    raise SystemExit("SEAGym pilot task index order changed")
allowed_task_keys = {
    "attributes",
    "fixtures",
    "scoring",
    "source",
    "task_id",
    "visibility",
}
for item in pilot_tasks["tasks"]:
    if set(item) != allowed_task_keys:
        raise SystemExit("SEAGym task index copied unsupported task content")
    source = item["source"]
    task_name = item["task_id"].split("/", 1)[1]
    if (
        source["dataset"] != "terminal-bench-2"
        or source["dataset_path"] != "data://terminal-bench-2"
        or source["dataset_version"]
        != "2fd12b88aafdd04a52c298e3940bcb189f9766d6"
        or source["local_path"] != f"data://terminal-bench-2/{task_name}"
        or source["registry_task_name"] != item["task_id"]
        or source["task_name"] != task_name
        or source["type"] != "harbor"
    ):
        raise SystemExit(f"SEAGym task reference changed: {item['task_id']}")
    forbidden_task_keys = {"instruction", "solution", "verifier", "environment"}
    if forbidden_task_keys & {str(key).casefold() for key in item}:
        raise SystemExit("SEAGym task index contains copied task payload")

expected_trial_counts = {
    "final_A_0": 3,
    "final_A_T": 3,
    "frozen_validation_initial": 3,
    "frozen_validation_post_epoch": 3,
    "replay_after_two_batches": 6,
    "total": 24,
    "train_rollouts": 6,
}
if protocol["schedule"]["expected_task_trials"] != expected_trial_counts:
    raise SystemExit("SEAGym pilot 24-trial accounting changed")
if sum(
    value
    for name, value in expected_trial_counts.items()
    if name != "total"
) != expected_trial_counts["total"]:
    raise SystemExit("SEAGym pilot trial total is internally inconsistent")
require(
    "docs/38-seagym-terminalbench-pilot.md",
    "It does not authorize automatic",
)
require(
    "docs/38-seagym-terminalbench-pilot.md",
    "No task score or solution was consulted.",
)
require(
    "docs/38-seagym-terminalbench-pilot.md",
    "total | 24",
)

# Mergeable source contains no temporary probes, one-time gates, write-enabled
# workflows, privileged pull-request triggers, or mutable action references.
for path in (ROOT / "src").rglob("*.tmp*"):
    raise SystemExit(f"temporary source probe remains: {path.relative_to(ROOT)}")
workflow_root = ROOT / ".github" / "workflows"
allowed_workflows = {
    "ci.yml",
    "full-agent-external-dry-run.yml",
    "minimal-scientific-seed-dry-run.yml",
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
