from __future__ import annotations

import argparse
from pathlib import Path

from evoagent.integrations.minimal_scientific_result import (
    MinimalScientificSeedResultImporter,
)
from evoagent.integrations.minimal_scientific_seed import (
    MinimalScientificSeedLock,
    build_minimal_scientific_seed_plan,
)
from evoagent.integrations.openrouter import OpenRouterModelPreset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a minimal scientific seed result without network access."
    )
    parser.add_argument("--controlled-root", type=Path, required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-authorization-anchor-hash", required=True)
    parser.add_argument("--expected-requester-id", required=True)
    parser.add_argument("--expected-approver-id", action="append", required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--frozen-lock", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    preset = OpenRouterModelPreset.model_validate_json(
        args.preset.read_text(encoding="utf-8")
    )
    lock = MinimalScientificSeedLock.model_validate_json(
        args.frozen_lock.read_text(encoding="utf-8")
    )
    plan, _ = build_minimal_scientific_seed_plan(
        args.workspace / "regenerated-plan",
        preset=preset,
    )
    if len(args.expected_approver_id) != 2:
        raise ValueError("Scientific import requires exactly two expected approvers.")
    receipt = MinimalScientificSeedResultImporter(
        args.controlled_root
    ).import_file(
        args.result,
        expected_sha256=args.expected_sha256,
        expected_source_commit=args.expected_source_commit,
        expected_authorization_anchor_hash=args.expected_authorization_anchor_hash,
        expected_requester_id=args.expected_requester_id,
        expected_approver_ids=tuple(args.expected_approver_id),
        plan=plan,
        lock=lock,
        preset=preset,
    )

    if args.receipt.exists() and args.receipt.is_symlink():
        raise RuntimeError("Scientific import receipt output must not be a symlink.")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        receipt.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "Minimal scientific result imported; "
        f"status={receipt.result_status}; "
        f"receipt_hash={receipt.receipt_hash}; "
        f"cost_usd={receipt.usage.cost_usd:.8f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
