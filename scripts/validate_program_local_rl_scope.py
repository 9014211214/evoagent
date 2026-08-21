from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required local-RL scope file missing: {path}")
    if marker not in target.read_text(encoding="utf-8"):
        raise SystemExit(
            f"required local-RL scope marker missing from {path}: {marker}"
        )


for path, marker in (
    (
        "src/evoagent/program_rl/__init__.py",
        "from .adapter_attested_final import ProgramLocalRLAdapter",
    ),
    (
        "src/evoagent/program_rl/adapter_attested_final.py",
        "from evoagent.program_rl.adapter_final import ProgramLocalRLAdapter as _ScopedAdapter",
    ),
    (
        "src/evoagent/program_rl/adapter_attested_final.py",
        "def build_intent_from_attestation",
    ),
    (
        "src/evoagent/program_rl/adapter_final.py",
        "FailureLayer.ROUTER",
    ),
    (
        "src/evoagent/program_rl/adapter_final.py",
        "FailureLayer.CONTEXT",
    ),
    (
        "src/evoagent/program_rl/adapter_final.py",
        "FailureLayer.VERIFIER",
    ),
    (
        "tests/test_program_local_rl_intervention_scope.py",
        "non_policy_intervention_layers_cannot_use_local_rl",
    ),
    (
        "tests/test_program_local_rl_full_lineage.py",
        "build_intent_from_attestation",
    ),
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

print("Program local-RL intervention scope invariants verified")
