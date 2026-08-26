from __future__ import annotations

import argparse
import json
from pathlib import Path

from evoagent.integrations.minimal_scientific_seed import (
    build_minimal_scientific_seed_plan,
    lock_minimal_scientific_seed_plan,
    run_zero_cost_scientific_dry_run,
)
from evoagent.integrations.openrouter import OpenRouterModelPreset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--plan-output", type=Path, required=True)
    parser.add_argument("--lock-output", type=Path)
    parser.add_argument("--evidence-output", type=Path, required=True)
    args = parser.parse_args()

    preset = OpenRouterModelPreset.model_validate_json(
        args.preset.read_text(encoding="utf-8")
    )
    plan, _ = build_minimal_scientific_seed_plan(
        args.workspace / "plan",
        preset=preset,
    )
    evidence = run_zero_cost_scientific_dry_run(
        args.workspace / "dry-run",
        preset=preset,
    )
    if evidence["plan_hash"] != plan.plan_hash:
        raise RuntimeError("Independent dry-run plan generation is not deterministic.")

    args.plan_output.parent.mkdir(parents=True, exist_ok=True)
    args.plan_output.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if args.lock_output is not None:
        args.lock_output.parent.mkdir(parents=True, exist_ok=True)
        args.lock_output.write_text(
            lock_minimal_scientific_seed_plan(plan).model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Frozen 12-Task scientific dry-run passed; "
        f"plan_hash={plan.plan_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
