from __future__ import annotations

import argparse
from pathlib import Path

from evoagent.integrations.minimal_scientific_seed import (
    MinimalScientificSeedLock,
    build_minimal_scientific_seed_plan,
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
    del snapshots
    raise PermissionError(
        "Direct paid execution is disabled. Use an exact-head private one-use "
        "controller with fresh preflight, independent approvals, an expiring "
        "ExecutionAuthorization, and a transactional SQLite claim."
    )


if __name__ == "__main__":
    raise SystemExit(main())
