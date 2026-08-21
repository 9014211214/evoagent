from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        raise SystemExit(f"required public-contract file missing: {path}")
    text = target.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(
            f"required public-contract marker missing from {path}: {marker}"
        )


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
        "src/evoagent/program/controller_public_contract.py",
        "one exact persisted generation evidence set",
    ),
    (
        "src/evoagent/program/controller_public_contract.py",
        "Program generation cannot add feedback after its decision",
    ),
    (
        "src/evoagent/program/controller_public_contract_final.py",
        "Partially approved Campaign has inconsistent approval cardinality",
    ),
    (
        "src/evoagent/program/package_public_contract.py",
        "one evidence lineage",
    ),
    (
        "src/evoagent/program/package_public_contract_final.py",
        "len(governed_origins) != 6",
    ),
    (
        "tests/test_program_consolidated_public_contract.py",
        "controller_program_attestation_final",
    ),
):
    require(path, marker)

for root in (
    ROOT / "src" / "evoagent" / "program",
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

print("v2.0 public Program contract source invariants verified")
