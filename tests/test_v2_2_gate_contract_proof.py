from __future__ import annotations

import runpy
from pathlib import Path


def test_v2_2_gate_contract_proof() -> None:
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(
        str(root / "scripts" / "validate_v2_2_gate_contract_proof.py"),
        run_name="__main__",
    )
