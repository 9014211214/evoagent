from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required v2.0 hardening file missing: {path}")
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(
            f"required v2.0 hardening marker missing from {path}: {marker}"
        )


def parse_tree(path: Path) -> None:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise SystemExit(
            f"v2.0 hardening Python syntax error in {path.relative_to(ROOT)}: {exc}"
        ) from exc


for path, marker in (
    (
        "src/evoagent/program/__init__.py",
        "controller_program_attestation_final",
    ),
    (
        "src/evoagent/program/__init__.py",
        "package_provenance_hardened_final",
    ),
    (
        "src/evoagent/program/controller_program_scope_final.py",
        "Program-scoped lifecycle audit reads",
    ),
    (
        "src/evoagent/program/controller_program_scope_final.py",
        "event.program_id == self._program_id",
    ),
    (
        "src/evoagent/program/controller_evidence_hardened.py",
        "Program decision signal is not the exact persisted",
    ),
    (
        "src/evoagent/program/controller_evidence_hardened.py",
        "Generation execution actor must differ from independent",
    ),
    (
        "src/evoagent/program/controller_final_hardened.py",
        "release package differs from the observed generation",
    ),
    (
        "src/evoagent/program/controller_final_hardened.py",
        "Attribution storage time differs from its immutable receipt",
    ),
    (
        "src/evoagent/program/controller_evidence_hardened_final.py",
        "controller_final_hardened",
    ),
    (
        "src/evoagent/program/controller_evidence_hardened_final.py",
        "one immutable generation signal",
    ),
    (
        "src/evoagent/program/controller_evidence_hardened_final.py",
        "Generation Campaign recovery must preserve its evaluator identity",
    ),
    (
        "src/evoagent/program/controller_evidence_hardened_final.py",
        "def decide",
    ),
    (
        "src/evoagent/program/controller_public_hardened.py",
        "complete read-only retry revalidation",
    ),
    (
        "src/evoagent/program/controller_public_final.py",
        "phase-correct, audit-bound retries",
    ),
    (
        "src/evoagent/program/controller_public_final.py",
        "New Generation approval is invalid in its persisted lifecycle phase",
    ),
    (
        "src/evoagent/program/controller_public_final.py",
        "Generation Campaign audit lifecycle is missing, duplicated or reordered",
    ),
    (
        "src/evoagent/program/controller_public_final.py",
        "Program generation completion audit differs from immutable outcome",
    ),
    (
        "src/evoagent/program/controller_public_final.py",
        "Generation approval rows differ from Campaign audit evidence",
    ),
    (
        "src/evoagent/program/controller_retry_hardened.py",
        "cannot ingest its own Program feedback",
    ),
    (
        "src/evoagent/program/controller_retry_hardened.py",
        "declared attributor",
    ),
    (
        "src/evoagent/program/package_policy_hardened.py",
        "requires one generation, signal and decision",
    ),
    (
        "src/evoagent/program/package_provenance_hardened.py",
        "differs from embedded",
    ),
    (
        "src/evoagent/program/package_provenance_hardened.py",
        "causal chronology",
    ),
    (
        "src/evoagent/program/package_provenance_hardened_final.py",
        "legitimate cross-registry recovery evidence",
    ),
    (
        "src/evoagent/program/package_provenance_hardened_final.py",
        "Recovered Campaign completion predates Generation completion",
    ),
    (
        "src/evoagent/program/package_audit_hardened.py",
        "feedback ingestion actor equals",
    ),
    (
        "src/evoagent/program/repository_hardened.py",
        "current_generation_index=record.generation_index",
    ),
    (
        "src/evoagent/program/constraints.py",
        "validate_single_release_package_budget",
    ),
    (
        "src/evoagent/lab/evolution_program_hardened.py",
        "drift_package.created_at",
    ),
    (
        "src/evoagent/campaigns/governance.py",
        "now: datetime | None = None",
    ),
    (
        "tests/test_program_decision_evidence.py",
        "requires_exact_persisted_evidence",
    ),
    (
        "tests/test_program_feedback_binding.py",
        "feedback_requires_exact_observed_release_package",
    ),
    (
        "tests/test_program_running_head.py",
        "tracks_the_active_successor_generation",
    ),
    (
        "tests/test_program_approval_retries.py",
        "approval_retry_is_read_only",
    ),
    (
        "tests/test_program_approval_retry_revalidation.py",
        "coherently_rehashed_audit_reason",
    ),
    (
        "tests/test_program_approval_retry_revalidation.py",
        "campaign_audit_tail_truncation",
    ),
    (
        "tests/test_program_approval_retry_revalidation.py",
        "coherently_rehashed_program_completion_payload",
    ),
    (
        "tests/test_program_partial_approval_retry.py",
        "first_approval_exact_retry",
    ),
    (
        "tests/test_program_partial_approval_retry.py",
        "coherently_rehashed_forged_evaluation",
    ),
    (
        "tests/test_program_submission_recovery_evaluator.py",
        "preserves_persisted_evaluator_identity",
    ),
    (
        "tests/test_program_unique_evidence_binding.py",
        "unique",
    ),
    (
        "tests/test_program_control_audit_semantics.py",
        "missing_required_record_without_index_error",
    ),
    (
        "tests/test_program_package_provenance.py",
        "rehashed_top_level_provenance_substitution",
    ),
    (
        "tests/test_program_package_recovery_roles.py",
        "independent_recovery_actor",
    ),
    (
        "tests/test_program_package_recovery_full_verify.py",
        "full_package_accepts_independent_campaign_completion_recovery",
    ),
    (
        "tests/test_program_identity_governance.py",
        "feedback_ingestor_and_attribution_writer_are_authenticated",
    ),
    (
        "tests/test_program_final_public_modules.py",
        "controller_public_final",
    ),
    (
        "tests/test_program_scoped_audit_reads.py",
        "isolate lifecycle audit reads by Program identity",
    ),
    (
        "tests/test_program_scoped_audit_reads.py",
        "controller_program_scope_final",
    ),
):
    require(path, marker)

for root in (
    ROOT / "src" / "evoagent" / "program",
    ROOT / "tests",
    ROOT / "scripts",
):
    for path in root.rglob("*.py"):
        parse_tree(path)

print("v2.0 Program hardening source invariants verified")
