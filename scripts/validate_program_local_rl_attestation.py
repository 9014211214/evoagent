from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required native-attestation file missing: {path}")
    if marker not in target.read_text(encoding="utf-8"):
        raise SystemExit(
            f"required native-attestation marker missing from {path}: {marker}"
        )


for path, marker in (
    (
        "src/evoagent/program_rl/attestation.py",
        "verifier.verify(package)",
    ),
    (
        "src/evoagent/program_rl/attestation.py",
        "projector.project(package)",
    ),
    (
        "src/evoagent/program_rl/attested_package.py",
        "Native local-RL attestation differs from the Program optimization intent",
    ),
    (
        "src/evoagent/program_rl/attested_package.py",
        "Program result was bound before native package verification",
    ),
    (
        "tests/test_program_local_rl_attestation.py",
        "failed_native_package_verification_produces_no_attestation",
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

print("Native local-RL attestation source invariants verified")
