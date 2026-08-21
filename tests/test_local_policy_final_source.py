from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_final_local_policy_public_implementation_source_gate():
    runpy.run_path(
        str(
            ROOT
            / "scripts"
            / "validate_local_policy_promotion_final_source.py"
        ),
        run_name="__main__",
    )
