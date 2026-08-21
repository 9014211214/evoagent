import runpy
from pathlib import Path


def test_v2_public_program_contract_source_gate():
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(
        str(root / "scripts" / "validate_v2_public_contract.py"),
        run_name="__main__",
    )
