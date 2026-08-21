import runpy
from pathlib import Path


def test_local_policy_promotion_source_gate():
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(
        str(root / "scripts" / "validate_local_policy_promotion_source.py"),
        run_name="__main__",
    )
