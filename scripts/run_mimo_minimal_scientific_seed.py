from __future__ import annotations

import argparse
import os
from pathlib import Path

from evoagent.integrations.minimal_scientific_seed import (
    MinimalScientificSeedLock,
    build_minimal_scientific_seed_plan,
    execute_minimal_scientific_seed,
    verify_minimal_scientific_seed_lock,
)
from evoagent.integrations.openrouter import OpenRouterModelPreset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--frozen-plan", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--requester-id", required=True)
    parser.add_argument("--approver-id", action="append", default=[])
    parser.add_argument("--authorization-anchor", required=True)
    args = parser.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is unavailable to scientific execution.")
    preset = OpenRouterModelPreset.model_validate_json(
        args.preset.read_text(encoding="utf-8")
    )
    frozen = MinimalScientificSeedLock.model_validate_json(
        args.frozen_plan.read_text(encoding="utf-8")
    )
    generated, snapshots = build_minimal_scientific_seed_plan(
        args.workspace / "regenerated-plan",
        preset=preset,
    )
    verify_minimal_scientific_seed_lock(generated, frozen)
    approvers = tuple(args.approver_id)
    if len(approvers) != 2:
        raise PermissionError("Scientific run requires exactly two approvers.")

    result = execute_minimal_scientific_seed(
        args.workspace / "external-evaluation",
        plan=generated,
        snapshots=snapshots,
        preset=preset,
        api_key=api_key,
        source_commit=args.source_commit,
        requester_id=args.requester_id,
        approver_ids=(approvers[0], approvers[1]),
        authorization_anchor=args.authorization_anchor,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        "MiMo minimal scientific seed "
        f"{result.status}; cost_usd={result.usage.cost_usd:.8f}"
    )
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
