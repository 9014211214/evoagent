from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2_3_composite_source_invariants():
    runpy.run_path(
        str(ROOT / "scripts" / "validate_v2_3_composite_source.py"),
        run_name="__main__",
    )
