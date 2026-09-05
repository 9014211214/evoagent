from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from evoagent.lab import DEFAULT_THIRD_PARTY_LOCK_HASH
import verify_seagym_terminalbench_pilot as pilot_verifier


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
require(".github/workflows/ci.yml", "python -m pip install -e ./adapters/seagym_evoagent")
require(".github/workflows/ci.yml", "adapters/seagym_evoagent/tests")
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

# The SEAGym + Terminal-Bench pilot remains final-comparison-blind, non-promoting,
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
        "d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87",
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
    "seagym_job_isolation_patch": (
        "experiments/seagym_terminalbench/patches/seagym-one-task-per-harbor-job.patch",
        "3e72ac2e9d2979d6a595a9ebc8fc5135c036e52e0bfe7f31db43d5be2a93f02f",
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
if protocol["artifacts"]["seagym_redaction_patch"] != {
    "path": "experiments/seagym_terminalbench/patches/seagym-token-count-redaction.patch",
    "sha256": "0c5302339bdcbeec076796b38f6ffd81803ce7f40cec1922c410294e8472018c",
    "target_blob_sha": "daa4fe84a28c63b68aaaffa6318e82a54b7be2df",
    "target_path": "seagym/logging/redaction.py",
}:
    raise SystemExit("SEAGym redaction patch target lock changed")
if protocol["artifacts"]["seagym_job_isolation_patch"]["targets"] != [
    {
        "blob_sha": "a63073693ae1d39da914f251518e68615991916c",
        "path": "seagym/envs/harbor_env/env.py",
    },
    {
        "blob_sha": "2edf535f3d06210f35002ca27f883175bb547a6f",
        "path": "seagym/trainers/builder.py",
    },
    {
        "blob_sha": "7f5743412a8a4c60fe723ccc9eba6c05c8b2658b",
        "path": "tests/test_harbor_results.py",
    },
    {
        "blob_sha": "0a75953a67c0f34237005c0fa35632dbbf45ced8",
        "path": "tests/test_trainer_reports.py",
    },
]:
    raise SystemExit("SEAGym one-task Harbor patch target locks changed")
try:
    pilot_verifier._validate_protocol(pilot_root / "protocol.json")
except pilot_verifier.VerificationError as exc:
    raise SystemExit(f"SEAGym pilot verifier preflight failed: {exc}") from exc
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
    "provider_update_sampling_determinism_claimed": False,
    "reasoning_semantics": {
        "absence_of_internal_reasoning_claimed": False,
        "reasoning_content_persisted": False,
        "reasoning_request_enabled": False,
        "safe_provider_usage_count_may_be_reported": True,
    },
    "router_audit": {
        "accepted_strategies": ["alias", "direct"],
        "cache_enabled": False,
        "material_pipeline_stages_allowed": False,
        "metadata_required": True,
        "successful_attempt": 1,
    },
    "same_model_for_update_and_rollout": True,
    "update_model_seed_parameter_sent": False,
}:
    raise SystemExit("SEAGym pilot model route changed")
if (
    protocol.get("protocol_id") != pilot_verifier.EXPECTED_PROTOCOL_ID
    or protocol.get("amendment") != pilot_verifier.EXPECTED_AMENDMENT
):
    raise SystemExit("SEAGym pilot final-comparison-blind v14 amendment changed")
if protocol.get("prior_amendment_v13") != pilot_verifier.EXPECTED_PRIOR_AMENDMENT_V13:
    raise SystemExit("SEAGym pilot preserved v13 amendment changed")
if protocol.get("prior_amendment_v12") != pilot_verifier.EXPECTED_PRIOR_AMENDMENT_V12:
    raise SystemExit("SEAGym pilot preserved v12 amendment changed")
if protocol.get("prior_amendment_v11") != pilot_verifier.EXPECTED_PRIOR_AMENDMENT_V11:
    raise SystemExit("SEAGym pilot preserved v11 amendment changed")
if protocol.get("prior_amendment_v10") != pilot_verifier.EXPECTED_PRIOR_AMENDMENT_V10:
    raise SystemExit("SEAGym pilot preserved v10 amendment changed")
if protocol.get("prior_amendment_v9") != pilot_verifier.EXPECTED_PRIOR_AMENDMENT_V9:
    raise SystemExit("SEAGym pilot preserved v9 amendment changed")
if protocol.get("prior_amendment_v8") != pilot_verifier.EXPECTED_PRIOR_AMENDMENT_V8:
    raise SystemExit("SEAGym pilot preserved v8 amendment changed")
if protocol.get("prior_amendment_v7") != {
    "adapter_evidence_change": "accept_only_bounded_numeric_reasoning_token_usage_telemetry_while_rejecting_reasoning_content_and_require_hash_bound_failure_receipts_for_errored_trials",
    "amended_at": "2026-08-30T06:39:29Z",
    "compatibility_normalization": {
        "applies_only_when": "inbound_tool_choice_none_with_nonempty_local_function_tools",
        "benchmark_effect_claimed": False,
        "inbound_semantics": "tool_calls_disabled_for_the_final_text_response",
        "model_provider_tasks_seed_or_config_changed": False,
        "outbound_change": "delete_tool_choice_and_tools_before_forwarding",
        "outbound_semantics": "no_local_function_tools_are_offered_to_the_model",
        "retry_identity": "byte_identical_normalized_outbound_body",
        "unexpected_outbound_tool_call_policy": "reject_response_and_make_pilot_incomplete",
    },
    "config_sha256_unchanged": "28f4c9078b36c78abdb72e31014629f47943f1bee1c2f94168004d62d8b0b195",
    "diagnostic_artifact_digest_kind": "github_actions_artifact_zip_sha256",
    "diagnostic_artifact_id": 9727486245,
    "diagnostic_artifact_sha256": "84dcd2eb4a08a24144e50290afc5aacb373ce4f1adb30703d5cd7e3ea79a53c9",
    "diagnostic_controller_run_id": 33295415122,
    "diagnostic_job_log": {
        "digest_kind": "github_actions_job_log_download_raw_bytes_sha256",
        "job_id": 99214123678,
        "safe_fixed_phrase": "train batch contains no usable Harbor ATIF evidence",
        "safe_fixed_phrase_occurrences": 2,
        "sha256": "b02d75dfe3e7591af55577c917185b55823eedca6590f52de7eda9911310f181",
    },
    "diagnostic_observation": {
        "completed_requests": 111,
        "final_upstream_http_404_errors": 0,
        "forwarded_requests": 111,
        "rejected_requests": 0,
        "tool_choice_none_normalizations": 7,
        "train_batches_without_usable_atif": 1,
        "upstream_attempts": 111,
        "upstream_http_404_attempts": 0,
        "upstream_other_attempt_errors": 0,
        "upstream_retries": 0,
    },
    "execution_resilience_change": "classify_mimocode_and_sanitizer_failures_before_harbor_post_run_recovery_and_skip_an_update_only_when_every_missing_atif_has_a_valid_content_free_hash_bound_failure_receipt",
    "leaf_cause_status": {
        "exact_failed_run_reasoning_token_value_available": False,
        "highest_probability_hypothesis": "provider_reported_reasoning_token_usage_was_rejected_as_reasoning_content_by_the_frozen_runtime_sanitizer",
        "hypothesis_confirmed_for_run_33295415122": False,
        "raw_event_or_response_content_persisted": False,
    },
    "prior_complete_comparisons": 0,
    "prior_controller_attempts_total": 12,
    "prior_pre_v2_controller_attempts": 4,
    "prior_v2_controller_attempts": 4,
    "prior_v3_controller_attempts": 1,
    "prior_v4_controller_attempts": 1,
    "prior_v5_controller_attempts": 1,
    "prior_v6_controller_attempts": 1,
    "prior_model_inference_completed": True,
    "prior_observed_usage_delta_usd": 0.26740132,
    "prior_protocol_id": "evoagent-seagym-terminalbench2-mimo-v2.5-seed42-v6",
    "prior_v2_run_evidence": [
        {
            "artifact_id": 9710915384,
            "artifact_sha256": "911253517539b6bfb0d851c0362d8de070eef21655e3d09e4da96991295f157c",
            "controller_commit": "c79bc1eae30c30c462cf034a8b355199ddb5f31f",
            "observed_usage_delta_usd": 0.000395987,
            "run_id": 33239134974,
            "score_produced": False,
        },
        {
            "artifact_id": 9715191473,
            "artifact_sha256": "a251cbde6595868e94e113d9bf1a3867ec0b3251e07806cc3a37bfefffebeb15",
            "controller_commit": "7f0088cfea552cbd09fc0c124b1d3d01acf36feb",
            "observed_usage_delta_usd": 0.002472451,
            "run_id": 33253565374,
            "score_produced": False,
        },
        {
            "artifact_id": 9716114523,
            "artifact_sha256": "47545121eacca279e25ff890a6dfc3d33832490955abb1ac0853daddddd93e9f",
            "controller_commit": "a6039f08e8931ee6e51b56e21e1146a623e834df",
            "observed_usage_delta_usd": 0.043002461,
            "run_id": 33256278875,
            "score_produced": False,
        },
        {
            "artifact_id": 9716899456,
            "artifact_sha256": "512cdedd6a0b4100e6fd2bf20bd1414c8cfeca451988e919d7149d2db28a5145",
            "controller_commit": "d896cae72135b43ff204b2fb29914deede8b9d0b",
            "observed_usage_delta_usd": 0.0446062,
            "run_id": 33259140059,
            "score_produced": False,
        },
    ],
    "prior_v3_run_evidence": [
        {
            "artifact_id": 9718565501,
            "artifact_sha256": "25d933194a193b5b2c0a6f35049089c5dd7f70abeb9581600368ab9c960fe4ec",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "9524dfbf786492d3fbeed27791bff4cbac280112",
            "observed_usage_delta_usd": 0.041496039,
            "run_id": 33265128690,
            "score_produced": False,
        }
    ],
    "prior_v4_run_evidence": [
        {
            "artifact_id": 9724445260,
            "artifact_sha256": "9b4d9465991ed5f9ef0bb5db5a3d56253751289917878b0a39a18f8e2359caee",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "9c25f473e8054bac76d7128f0ac025dd3e154080",
            "evoagent_public_commit": "09018d7b4bdfcdc11f61f8c302c857d7f5dfd7f7",
            "observed_usage_delta_usd": 0.042464641,
            "run_id": 33285475794,
            "score_produced": False,
        }
    ],
    "prior_v5_run_evidence": [
        {
            "artifact_id": 9725879182,
            "artifact_sha256": "d89cee0d82b57881d721179decf4bc06282adf6d77a511b3fd085469c0f3fa54",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "8c2015febaabca2710e90805bb9ee95e0ec5a83c",
            "evoagent_public_commit": "092ba665fd01450ee70446fe3f8e30cce4775c08",
            "observed_usage_delta_usd": 0.042668012,
            "run_id": 33289924348,
            "score_produced": False,
        }
    ],
    "prior_v6_run_evidence": [
        {
            "artifact_id": 9727486245,
            "artifact_sha256": "84dcd2eb4a08a24144e50290afc5aacb373ce4f1adb30703d5cd7e3ea79a53c9",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "2a44abedde490fc3d6d602a372284db030357eb4",
            "evoagent_public_commit": "9889fee8888baca681311a3c10880a7144f5736d",
            "job_id": 99214123678,
            "job_log_sha256": "b02d75dfe3e7591af55577c917185b55823eedca6590f52de7eda9911310f181",
            "observed_usage_delta_usd": 0.050295529,
            "run_id": 33295415122,
            "score_produced": False,
        }
    ],
    "prior_score_produced": False,
    "reason_code": "harbor_post_run_missing_atif_masked_an_inner_mimocode_or_runtime_sanitizer_failure",
    "root_cause_evidence": {
        "benchmark_result_claimed": False,
        "confidence": "high_for_two_layer_failure_and_unconfirmed_for_exact_leaf_cause",
        "frozen_mimocode_local_capture": {
            "capture_content_persisted": False,
            "observable_contract": "completion_tokens_details_reasoning_tokens_maps_to_step_finish_part_tokens_reasoning",
            "runtime": "mimocode-v0.1.13",
        },
        "harbor_recovery_contract": {
            "classified_nonzero_agent_failure_is_contained": True,
            "generic_runtime_error_can_be_masked_by_a_second_missing_atif_error": True,
        },
        "run_observation": {
            "completed_requests": 111,
            "proxy_or_upstream_errors": 0,
            "train_batch_without_usable_atif": True,
        },
    },
    "score_blind": True,
    "transport_only_change": False,
}:
    raise SystemExit("SEAGym pilot preserved v7 amendment changed")
if protocol["runtime"]["mimocode"] != {
    "asset": "mimocode-linux-x64.tar.gz",
    "asset_bytes": 46489490,
    "asset_sha256": "0997a43647a99969d0194fad71af1fd6112aa8220e24a4562aea63953b1e1ada",
    "asset_url": (
        "https://github.com/XiaomiMiMo/MiMo-Code/releases/download/"
        "v0.1.13/mimocode-linux-x64.tar.gz"
    ),
    "auxiliary_model_calls": {
        "actor_subsessions_enabled": False,
        "automatic_checkpoint_enabled": False,
        "automatic_cron_enabled": False,
        "automatic_distill_enabled": False,
        "automatic_dream_enabled": False,
        "mcp_sampling_enabled": False,
        "next_prompt_prediction_enabled": False,
        "title_agent_enabled": False,
        "unattested_model_calls_allowed": False,
    },
    "commit": "67c9cf1e26288d03c65fb844be71f39581ffc1de",
    "execution_isolation": {
        "build_tool_allowlist": ["bash", "read", "write", "edit", "glob", "grep"],
        "compaction_auto_enabled": True,
        "config_content_overlay": "{}",
        "disposable_home_environment": ["HOME", "MIMOCODE_HOME", "USERPROFILE"],
        "fixed_session_title": "evoagent-seagym-trial",
        "mcp_servers_configured": False,
        "proxy_session_affinity_header": "x-session-affinity",
        "proxy_session_affinity_required": True,
        "pure_mode_enabled": True,
        "root_session_only": True,
    },
    "version": "0.1.13",
}:
    raise SystemExit("SEAGym pilot MiMoCode runtime changed")
mimocode_adapter_source = require_file(
    "adapters/seagym_evoagent/src/seagym_evoagent/mimocode.py"
).read_text(encoding="utf-8")
for required_fragment in (
    '"permission": {"actor": "deny"}',
    '"title": {"disable": True}',
    '"experimental": {"predict_next_prompt": False}',
    '"tool_allowlist": ["bash", "read", "write", "edit", "glob", "grep"]',
    '"compaction": {"auto": True, "prune": True}',
    '"checkpoint-writer": {"disable": True}',
    '"max": {"disable": True}',
    '"mcp_sampling": "deny"',
    '"MIMOCODE_CONFIG_CONTENT": "{}"',
    '"MIMOCODE_EXPERIMENTAL_CRON": "0"',
    '"MIMOCODE_DISABLE_CRON": "1"',
    '"MIMOCODE_DISABLE_CHECKPOINT": "1"',
    '"MIMOCODE_EXPERIMENTAL_ORCHESTRATOR": "0"',
    '"MIMOCODE_EXPERIMENTAL_WORKFLOW_TOOL": "0"',
    '"MIMOCODE_EXPERIMENTAL_MCP_TOOL_SEARCH": "0"',
    '"MIMOCODE_ENABLE_EXEC_TOOL": "0"',
    '"MIMOCODE_PURE": "1"',
    '"HOME": home_path',
    '"USERPROFILE": home_path',
):
    if required_fragment not in mimocode_adapter_source:
        raise SystemExit("SEAGym pilot hidden MiMoCode model-call guard changed")
harbor_adapter_source = require_file(
    "adapters/seagym_evoagent/src/seagym_evoagent/harbor_agent.py"
).read_text(encoding="utf-8")
if 'f"--title {shlex.quote(MIMOCODE_SESSION_TITLE)} "' not in harbor_adapter_source:
    raise SystemExit("SEAGym pilot fixed MiMoCode session-title guard changed")
if protocol["runtime"]["secret_environment_variable"] != "OPENROUTER_API_KEY":
    raise SystemExit("SEAGym pilot credential boundary changed")
if protocol["runtime"]["credential_transport"] != {
    "account_key_in_task_container": False,
    "container_credential_kind": "ephemeral_proxy_capability",
    "kind": "host_guard_proxy",
    "proxy_base_url": "http://evoagent-openrouter-proxy:18765/api/v1",
}:
    raise SystemExit("SEAGym pilot credential transport changed")
if protocol["runtime"]["guard_proxy"] != {
    "health_schema_version": "openrouter-guard-proxy-health-v5",
    "limits": {
        "client_timeout_seconds": 30.0,
        "max_concurrency": 2,
        "max_output_tokens": 16_000,
        "max_request_bytes": 2 * 1024 * 1024,
        "max_requests": 768,
        "max_response_bytes": 16 * 1024 * 1024,
        "upstream_timeout_seconds": 300.0,
    },
    "root_session_binding": {
        "enabled": True,
        "full_pilot_limit": 24,
        "header": "x-session-affinity",
        "health_schema_version": "openrouter-guard-proxy-health-v5",
        "lifecycle_canary_limit": 1,
        "parent_header_forbidden": "x-parent-session-id",
        "payload_binding": "prompt_cache_key",
        "route_canary_limit": 1,
    },
    "source_sha256": "e2cea221758f09c8658a65e120be3056d4dc5948eccb93668c3e3561d363fe29",
    "telemetry": {
        "normalization_counters": ["tool_choice_none_to_no_tools"],
        "raw_request_content_persisted": False,
        "request_profile_buckets": ["absent", "auto", "required", "none", "named"],
        "request_profile_fields": [
            "inbound_tool_choice",
            "outbound_tool_choice",
            "final_upstream_errors_by_outbound_tool_choice",
        ],
    },
}:
    raise SystemExit("SEAGym pilot guard-proxy identity changed")
if protocol["runtime"]["openrouter_retry_policy"] != {
    "ambiguous_transport_failures_retried": False,
    "backoff_seconds": [5.0, 10.0, 20.0, 40.0],
    "fallbacks_enabled": False,
    "max_retries_per_client_request": 4,
    "request_body_changed_between_attempts": False,
    "retryable_http_statuses": [404, 408, 409, 425, 429, 500, 502, 503, 504, 524, 529],
    "same_model_provider_endpoint": True,
}:
    raise SystemExit("SEAGym pilot retry policy changed")
if protocol["runtime"]["privacy_sanitizer"] != {
    "raw_jsonl_max_bytes": 64 * 1024 * 1024,
    "raw_persisted": False,
    "raw_record_max_bytes": 16 * 1024 * 1024,
    "raw_string_max_chars": 16 * 1024 * 1024,
    "reasoning_content_persisted": False,
    "reasoning_token_count_telemetry_allowed": True,
}:
    raise SystemExit("SEAGym pilot privacy sanitizer bounds changed")
if protocol["runtime"]["token_semantics"] != {
    "harbor_cached_tokens": "cache_read_subset_of_harbor_input_tokens",
    "harbor_input_tokens": "non_cached_input_plus_cache_read",
    "harbor_reasoning_tokens": "provider_reported_usage_count_only_not_reasoning_content",
    "reported_attested_total_tokens": "harbor_input_tokens_plus_visible_output_tokens_plus_reasoning_tokens",
    "seagym_total_tokens": "harbor_input_tokens_plus_harbor_cached_tokens_plus_output_tokens",
}:
    raise SystemExit("SEAGym pilot token semantics changed")
if protocol["schedule"]["seed_semantics"] != {
    "controls": [
        "task_split",
        "frozen_batch_order",
        "update_attempt_record",
        "checkpoint_and_trial_attestation",
    ],
    "provider_update_sampling_determinism_claimed": False,
    "provider_rollout_sampling_determinism_claimed": False,
}:
    raise SystemExit("SEAGym pilot seed semantics changed")
if protocol["claim_boundary"] != {
    "automatic_promotion": False,
    "causal_attribution_claimed": False,
    "leaderboard_submission": False,
    "paper_scale_reproduction": False,
    "pilot_kind": "real_external_scientific_pilot",
    "results_status": "preregistered_incomplete_attempts_no_score",
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
    or pilot_config["backend"]["n_concurrent"] != 1
    or pilot_config["backend"].get("one_task_per_harbor_job") is not True
    or protocol["resources"]["harbor_concurrency"] != 1
    or protocol["resources"].get("mimocode_force_kill_grace_seconds") != 15
    or protocol["resources"].get("mimocode_route_canary_timeout_seconds") != 600
    or protocol["resources"].get("mimocode_sanitization_margin_seconds") != 120
    or pilot_config["backend"]["agent_override_timeout_sec"] != 1800
    or pilot_config["backend"]["verifier_override_timeout_sec"] != 600
):
    raise SystemExit("SEAGym pilot Harbor resource identity changed")
if protocol["resources"].get("harbor_job_isolation") != {
    "expected_unique_jobs": 24,
    "one_task_per_job": True,
    "retries": 0,
    "synthetic_failure_receipts": False,
}:
    raise SystemExit("SEAGym pilot Harbor job-isolation contract changed")
if protocol["resources"].get("lifecycle_canary") != {
    "budget_stop_threshold_usd": 0.15,
    "command_timeout_seconds": 2400,
    "must_complete_before_full_pilot": True,
    "purpose": "integration_only_no_benchmark_score",
    "seed": 42,
    "task_id": "terminal-bench/fix-git",
}:
    raise SystemExit("SEAGym lifecycle canary resource identity changed")
if (
    protocol["resources"].get("budget_guard", {}).get("command_timeout_seconds")
    != 13200
    or protocol["resources"].get("non_full_pilot_workflow_reserve_seconds")
    != 5100
):
    raise SystemExit("SEAGym bounded workflow timing identity changed")
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
require(
    "docs/38-seagym-terminalbench-pilot.md",
    "e2cea221758f09c8658a65e120be3056d4dc5948eccb93668c3e3561d363fe29",
)
require(
    "docs/38-seagym-terminalbench-pilot.md",
    "health schema v5",
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
