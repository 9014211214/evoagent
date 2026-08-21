import runpy
from pathlib import Path


def test_program_local_rl_intervention_scope_source_gate():
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(
        str(root / "scripts" / "validate_program_local_rl_scope.py"),
        run_name="__main__",
    )
