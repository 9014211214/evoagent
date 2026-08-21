from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required final v2.2 file missing: {path}")
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(
            f"required final v2.2 marker missing from {path}: {marker}"
        )


for path, marker in (
    ("src/evoagent/local_policy/__init__.py", "lifecycle_recovery_final"),
    ("src/evoagent/local_policy/__init__.py", "package_semantic_final"),
    ("src/evoagent/local_policy/__init__.py", "repository_chronology_final"),
    (
        "src/evoagent/local_policy/lifecycle_hardened.py",
        "Local-policy Campaign must remain HIGH risk with exactly two approvals",
    ),
    (
        "src/evoagent/local_policy/lifecycle_hardened.py",
        "Local-policy operation arguments differ from the Campaign-bound record",
    ),
    (
        "src/evoagent/local_policy/lifecycle_hardened.py",
        "Promotion authorization retry used another actor",
    ),
    (
        "src/evoagent/local_policy/lifecycle_hardened.py",
        "Rollback authorization retry used another actor",
    ),
    (
        "src/evoagent/local_policy/lifecycle_hardened.py",
        "candidate.governed_actor_ids",
    ),
    (
        "src/evoagent/local_policy/lifecycle_hardened.py",
        'campaign.metadata.get("from_policy_id"',
    ),
    (
        "src/evoagent/local_policy/lifecycle_hardened.py",
        "_campaign_prohibited(campaign)",
    ),
    (
        "src/evoagent/local_policy/lifecycle_recovery_final.py",
        "Final lifecycle with stage-aware recovery and monotonic evidence",
    ),
    (
        "src/evoagent/local_policy/lifecycle_recovery_final.py",
        "class _LocalPolicyCampaignGovernanceAdapter",
    ),
    (
        "src/evoagent/local_policy/lifecycle_recovery_final.py",
        "class _LocalPolicyCampaignRepositoryAdapter",
    ),
    (
        "src/evoagent/local_policy/lifecycle_recovery_final.py",
        "Promotion retry differs from immutable evaluation evidence",
    ),
    (
        "src/evoagent/local_policy/lifecycle_recovery_final.py",
        "Rollback retry differs from immutable assessment evidence",
    ),
    (
        "src/evoagent/local_policy/lifecycle_recovery_final.py",
        "passed=report.safe_to_rollback",
    ),
    (
        "src/evoagent/local_policy/package_hardened.py",
        "provenance differs from accepted v2.1 evidence",
    ),
    (
        "src/evoagent/local_policy/package_hardened.py",
        "Campaign governance is not HIGH risk with two approvals",
    ),
    (
        "src/evoagent/local_policy/package_semantic_final.py",
        "class _CampaignAuditReadAdapter",
    ),
    (
        "src/evoagent/local_policy/package_semantic_final.py",
        "another Campaign audit event",
    ),
    (
        "src/evoagent/local_policy/package_semantic_final.py",
        "Campaign completion audit semantics differ",
    ),
    (
        "src/evoagent/local_policy/package_semantic_final.py",
        "promotion package time must not be in the future",
    ),
    (
        "src/evoagent/local_policy/package_semantic_final.py",
        "Existing local-policy package differs from immutable evidence",
    ),
    (
        "src/evoagent/local_policy/repository_chronology_final.py",
        "Registry write time must include a timezone",
    ),
    (
        "src/evoagent/local_policy/repository_chronology_final.py",
        "candidate admission requires a candidate manifest",
    ),
    (
        "src/evoagent/campaigns/governance.py",
        "decision: ApprovalDecision = ApprovalDecision.APPROVE",
    ),
    (
        "src/evoagent/lab/__init__.py",
        "AcceptedLocalPolicyPromotionLab",
    ),
    (
        "src/evoagent/lab/local_policy_promotion_final.py",
        "class AcceptedLocalPolicyPromotionLab",
    ),
    (
        "tests/test_local_policy_campaign_api_adapter.py",
        "adapts_current_campaign_service_and_repository",
    ),
    (
        "tests/test_local_policy_campaign_semantics.py",
        "rehashed_promotion_completion_actor_is_rejected",
    ),
    (
        "tests/test_local_policy_package_provenance.py",
        "rehashed_top_level_provenance_substitution_is_rejected",
    ),
    (
        "tests/test_local_policy_submission_recovery.py",
        "promotion_submission_recovers_open_campaign_without_registry_rewrite",
    ),
    (
        "tests/test_local_policy_runtime_role_separation.py",
        "campaign_bound_promotion_arguments_cannot_be_substituted",
    ),
    (
        "tests/test_local_policy_interface_alignment.py",
        "role_guard_uses_persisted_candidate_actor_ids",
    ),
    (
        "tests/test_local_policy_interface_alignment.py",
        "conflicting_existing_package_is_not_overwritten",
    ),
    (
        "tests/test_local_policy_package_export.py",
        "exact_package_reexport_is_read_only",
    ),
    (
        "tests/test_local_policy_rollback_bypass.py",
        "direct_registry_authorization_cannot_normalize_promotion_reviewer_overlap",
    ),
    (
        "tests/test_local_policy_public_contract.py",
        "repository_chronology_final",
    ),
    (
        "tests/test_v2_2_exact_head_gate_contract.py",
        "test_v2_2_exact_head_gate_covers_final_contract",
    ),
    (
        "scripts/run_v2_2_exact_head_gate.py",
        "validate_local_policy_promotion_final_source.py",
    ),
    (
        "scripts/run_v2_2_exact_head_gate.py",
        "tests/test_local_policy_campaign_semantics.py",
    ),
    (
        "scripts/run_v2_2_exact_head_gate.py",
        "tests/test_local_policy_runtime_role_separation.py",
    ),
    (
        "scripts/run_v2_2_exact_head_gate.py",
        "tests/test_local_policy_interface_alignment.py",
    ),
    (
        ".github/workflows/ci.yml",
        "python scripts/validate_local_policy_promotion_final_source.py",
    ),
    (
        ".github/workflows/ci.yml",
        "python scripts/validate_v2_2_gate_contract_proof.py",
    ),
    (
        ".github/workflows/ci.yml",
        "run: pytest -q",
    ),
    (
        ".github/workflows/ci.yml",
        "python -m pip wheel . --no-deps --wheel-dir dist",
    ),
):
    require(path, marker)

for relative in (
    "src/evoagent/campaigns/governance.py",
    "src/evoagent/local_policy/__init__.py",
    "src/evoagent/local_policy/lifecycle_hardened.py",
    "src/evoagent/local_policy/lifecycle_recovery_final.py",
    "src/evoagent/local_policy/package_hardened.py",
    "src/evoagent/local_policy/package_semantic_final.py",
    "src/evoagent/local_policy/repository_chronology_final.py",
    "src/evoagent/lab/__init__.py",
    "src/evoagent/lab/local_policy_promotion_final.py",
    "tests/test_local_policy_campaign_api_adapter.py",
    "tests/test_local_policy_campaign_semantics.py",
    "tests/test_local_policy_package_provenance.py",
    "tests/test_local_policy_package_export.py",
    "tests/test_local_policy_submission_recovery.py",
    "tests/test_local_policy_runtime_role_separation.py",
    "tests/test_local_policy_interface_alignment.py",
    "tests/test_local_policy_rollback_bypass.py",
    "tests/test_local_policy_public_contract.py",
    "tests/test_v2_2_exact_head_gate_contract.py",
    "scripts/run_v2_2_exact_head_gate.py",
):
    target = ROOT / relative
    try:
        ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except SyntaxError as exc:
        raise SystemExit(
            f"Python syntax error in {target.relative_to(ROOT)}: {exc}"
        ) from exc

print("Final v2.2 public implementation source invariants verified")
