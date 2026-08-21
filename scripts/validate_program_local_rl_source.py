from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required Program local-RL file missing: {path}")
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(
            f"required Program local-RL marker missing from {path}: {marker}"
        )


def forbid(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required Program local-RL file missing: {path}")
    if marker in target.read_text(encoding="utf-8"):
        raise SystemExit(
            f"forbidden Program local-RL public marker remains in {path}: {marker}"
        )


for path, marker in (
    ("src/evoagent/program_rl/models.py", "optimizer_execution_authorized: Literal[False]"),
    ("src/evoagent/program_rl/models.py", "foundation_model_weights_updated: Literal[False]"),
    ("src/evoagent/program_rl/__init__.py", "adapter_attested_final"),
    ("src/evoagent/program_rl/__init__.py", "stage_managers_final"),
    ("src/evoagent/program_rl/adapter.py", "explicitly running Program generation"),
    ("src/evoagent/program_rl/adapter.py", "strict held-out reward and success improvement"),
    ("src/evoagent/program_rl/package.py", "widens its offline non-promotion boundary"),
    ("src/evoagent/local_rl/__init__.py", "ProgramLocalRLBindingManager"),
    (
        "src/evoagent/local_rl/program_binding_persistent.py",
        "Public binding manager with semantic audit and atomic file persistence",
    ),
    (
        "tests/test_local_rl_public_contract.py",
        "local_rl_public_api_uses_persistent_program_binding_manager",
    ),
    (
        "tests/test_local_rl_public_contract.py",
        "generic_legacy_program_adapter_is_not_exported",
    ),
    ("tests/test_program_local_rl_adapter.py", "requires_separate_execution_authorizer"),
    ("tests/test_program_local_rl_verified_native.py", "verified_native_package_binds_to_program"),
    ("docs/24-program-to-local-rl-adapter.md", "## Canonical API boundaries"),
    ("docs/24-program-to-local-rl-adapter.md", "ProgramLocalRLExecutionTicket"),
    ("docs/24-program-to-local-rl-adapter.md", "completed native package must never be converted retroactively"),
    (".github/workflows/ci.yml", "name: ci"),
    (".github/workflows/ci.yml", 'python-version: ["3.11", "3.12"]'),
    (".github/workflows/ci.yml", "python scripts/validate_program_local_rl_source.py"),
    (".github/workflows/ci.yml", "test -z \"$(git status --short)\""),
    (".github/workflows/ci.yml", "run: pytest -q"),
):
    require(path, marker)

for marker in (
    "build_program_local_rl_binding_package",
    "build_program_local_rl_evidence",
    "build_program_local_rl_execution_authorization",
    "build_program_local_rl_intent",
):
    forbid("src/evoagent/local_rl/__init__.py", marker)

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

print("Program-to-local-RL source invariants verified")
