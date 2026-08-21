from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CONTRACT_TEST = ROOT / "tests" / "test_v2_2_exact_head_gate_contract.py"
SOURCE_GATE = ROOT / "scripts" / "validate_local_policy_promotion_source.py"


def require_file(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(
            f"required v2.2 gate-contract artifact missing: {path.relative_to(ROOT)}"
        )
    return path.read_text(encoding="utf-8")


def require(text: str, marker: str, *, path: Path) -> None:
    if marker not in text:
        raise SystemExit(
            f"required marker missing from {path.relative_to(ROOT)}: {marker}"
        )


def forbid(text: str, marker: str, *, path: Path) -> None:
    if marker in text:
        raise SystemExit(
            f"forbidden marker in {path.relative_to(ROOT)}: {marker}"
        )


ci_text = require_file(CI_WORKFLOW)
contract_text = require_file(CONTRACT_TEST)
source_text = require_file(SOURCE_GATE)

require(
    ci_text,
    "python scripts/validate_local_policy_promotion_source.py",
    path=CI_WORKFLOW,
)
require(ci_text, "python scripts/validate_v2_2_gate_contract_proof.py", path=CI_WORKFLOW)
require(ci_text, "permissions:", path=CI_WORKFLOW)
require(ci_text, "contents: read", path=CI_WORKFLOW)
require(ci_text, "run: pytest -q", path=CI_WORKFLOW)
forbid(ci_text, "contents: write", path=CI_WORKFLOW)
require(
    source_text,
    "test_v2_2_exact_head_gate_contract.py",
    path=SOURCE_GATE,
)

for path, text in (
    (CONTRACT_TEST, contract_text),
    (SOURCE_GATE, source_text),
    (Path(__file__).resolve(), Path(__file__).read_text(encoding="utf-8")),
):
    try:
        ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise SystemExit(
            f"v2.2 gate-contract Python syntax error in {path.relative_to(ROOT)}: {exc}"
        ) from exc

print("v2.2 gate-contract proof verified")
