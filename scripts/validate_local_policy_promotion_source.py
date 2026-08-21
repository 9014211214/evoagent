from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required local-policy promotion file missing: {path}")
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(
            f"required local-policy promotion marker missing from {path}: {marker}"
        )


for path, marker in (
    (
        "src/evoagent/campaigns/models.py",
        'LOCAL_POLICY_PROMOTION = "local_policy_promotion"',
    ),
    (
        "src/evoagent/campaigns/models.py",
        'LOCAL_POLICY_ROLLBACK = "local_policy_rollback"',
    ),
    (
        "src/evoagent/local_policy/models.py",
        "production_activation_authorized: Literal[False]",
    ),
    (
        "src/evoagent/local_policy/repository.py",
        "Only an authorized local-policy candidate may become active",
    ),
    (
        "src/evoagent/local_policy/repository.py",
        "Expected active revision",
    ),
    (
        "src/evoagent/local_policy/lifecycle.py",
        "Completed promotion Campaign has no matching active pointer",
    ),
    (
        "src/evoagent/local_policy/lifecycle.py",
        "Completed rollback Campaign has no matching active pointer",
    ),
    (
        "src/evoagent/local_policy/package.py",
        "Local-policy audit reasons differ from governed semantics",
    ),
    (
        "tests/test_local_policy_promotion_lifecycle.py",
        "activation_recovery_completes_campaign_once_then_is_read_only",
    ),
    (
        "tests/test_local_policy_promotion_tamper.py",
        "coherently_rehashed_local_policy_audit_semantics_are_rejected",
    ),
    (
        "tests/test_v2_2_exact_head_gate_contract.py",
        "test_v2_2_exact_head_gate_contract",
    ),
):
    require(path, marker)

for root in (
    ROOT / "src" / "evoagent" / "local_policy",
    ROOT / "tests",
    ROOT / "scripts",
):
    for target in root.rglob("*.py"):
        try:
            ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        except SyntaxError as exc:
            raise SystemExit(
                f"Python syntax error in {target.relative_to(ROOT)}: {exc}"
            ) from exc

print("Local-policy promotion source invariants verified")
