from __future__ import annotations

import json
import shutil
from pathlib import Path

from evoagent.lab import BenchmarkGatedChampionLab


ROOT = Path(".evoagent/benchmark-gated-champion-example")


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    lab = BenchmarkGatedChampionLab(
        ROOT,
        source_commit="8" * 40,
    )
    first = lab.run()
    second = lab.run()
    print(
        json.dumps(
            {
                "first_resumed": first.resumed,
                "second_resumed": second.resumed,
                "scores": {
                    "a0": second.baseline_score,
                    "a1": second.a1_score,
                    "a2": second.a2_score,
                },
                "selected": {
                    "run_id": second.selected_run_id,
                    "snapshot_id": second.selected_snapshot_id,
                    "round": second.selected_round,
                    "score": second.selected_score,
                },
                "round_statuses": {
                    "a1": second.a1_status,
                    "a2": second.a2_status,
                },
                "a2_reasons": list(second.a2_reasons),
                "stop_recommendation": second.stop_recommendation,
                "campaign_state": second.campaign_state,
                "approvals": second.approval_count,
                "active_snapshot": second.active_snapshot_id,
                "active_revision": second.active_revision,
                "same_package": first.package_hash == second.package_hash,
                "harbor_execution_performed_by_evoagent": (
                    second.harbor_execution_performed_by_evoagent
                ),
                "external_model_call_performed_by_evoagent": (
                    second.external_model_call_performed_by_evoagent
                ),
                "production_deployment_performed": (
                    second.production_deployment_performed
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
