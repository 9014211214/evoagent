from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required final local-RL evidence file missing: {path}")
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(
            f"required final local-RL evidence marker missing from {path}: {marker}"
        )


for path, marker in (
    ("src/evoagent/program_rl/__init__.py", "ProgramLocalRLAcceptanceManager"),
    ("src/evoagent/program_rl/__init__.py", "package_verified_public_final"),
    ("src/evoagent/program_rl/__init__.py", "stage_managers_final"),
    ("src/evoagent/program_rl/package_verified_final.py", "result hash mismatch"),
    ("src/evoagent/program_rl/package_verified_public_final.py", "full I/O API"),
    ("src/evoagent/program_rl/stage_managers_final.py", "recursive base-package verification"),
    ("src/evoagent/program_rl/stage_managers_final.py", "recursive attested-package verification"),
    ("src/evoagent/program_rl/stage_managers_final.py", "recursive schema-package verification"),
    ("src/evoagent/program_rl/intent_binding.py", "running_attestation_payload"),
    ("src/evoagent/program_rl/intent_binding_verified_final.py", "Embedded Campaign checkpoint differs"),
    ("src/evoagent/program_rl/evidence_verified_final.py", "Final recursive verifier"),
    ("src/evoagent/program_rl/evidence_verified_public_final.py", "complete evidence-chain role separation"),
    ("src/evoagent/program_rl/trusted_acceptance.py", "independent external anchors"),
    ("tests/test_program_local_rl_full_lineage.py", "coherent_running_anchor_rewrite"),
    ("tests/test_program_local_rl_stage_recursive_tamper.py", "public_stages_recompute_nested_result_hash"),
    ("tests/test_program_local_rl_stage_recursive_tamper.py", "result hash mismatch"),
    ("tests/test_program_rl_public_contract.py", "stage_managers_final"),
    ("tests/test_program_rl_public_contract.py", "trusted_acceptance"),
):
    require(path, marker)

for root in (
    ROOT / "src" / "evoagent" / "program_rl",
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

print("Final Program local-RL evidence source invariants verified")
