from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required local-RL runtime-contract file missing: {path}")
    if marker not in target.read_text(encoding="utf-8"):
        raise SystemExit(
            f"required local-RL runtime-contract marker missing from {path}: {marker}"
        )


REQUIRED = (
    ("src/evoagent/program_rl/__init__.py", "EvoagentLocalRLPackageProjector"),
    ("src/evoagent/program_rl/evoagent_native.py", "native_governance_final"),
    ("src/evoagent/program_rl/evoagent_native.py", "_governance_installed"),
    ("src/evoagent/program_rl/evoagent_native.py", "_verify_native_governance(package)"),
    ("src/evoagent/program_rl/native_contract.py", "self.manager.verify(package) is not True"),
    ("src/evoagent/program_rl/native_governance_final.py", "registrar, trainer, evaluator and selector"),
    ("src/evoagent/program_rl/native_governance_final.py", "audit timestamps are not monotonic"),
    ("src/evoagent/program_rl/native_governance_final.py", "audit reasons differ from the governed lifecycle"),
    ("tests/test_evoagent_native_local_rl_attestor.py", "projector_rejects_selector_trainer_overlap"),
    ("tests/test_evoagent_native_local_rl_attestor.py", "projector_rejects_non_monotonic_native_audit"),
    ("tests/test_program_local_rl_native_runtime.py", "concrete_projector_rejects_tampered_native_evaluation"),
    ("tests/test_program_local_rl_verified_native.py", "verified_native_package_binds_to_program"),
    ("tests/test_program_rl_public_contract.py", "EvoagentLocalRLPackageProjector"),
)

for path, marker in REQUIRED:
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

print("Program local-RL runtime-contract source invariants verified")
