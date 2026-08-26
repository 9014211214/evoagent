from __future__ import annotations

import argparse
from pathlib import Path

from evoagent.integrations.full_agent_calibration import build_contract_dry_run_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", default="xiaomi/mimo-v2.5")
    args = parser.parse_args()

    plan = build_contract_dry_run_plan(args.workspace, model_id=args.model_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Full-Agent credential-free dry-run plan verified: {plan.plan_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
