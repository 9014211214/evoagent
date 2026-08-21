import runpy
from pathlib import Path


def test_v2_program_hardening_source_gate():
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(
        str(root / "scripts" / "validate_v2_program_hardening.py"),
        run_name="__main__",
    )
