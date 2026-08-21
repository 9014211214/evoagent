from __future__ import annotations

import json
import shutil
from pathlib import Path

from evoagent.lab import AuthoritativeBenchmarkEvidenceLab


ROOT = Path(".evoagent/authoritative-benchmark-evidence-example")


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    lab = AuthoritativeBenchmarkEvidenceLab(
        ROOT,
        source_commit="7" * 40,
    )
    first = lab.run()
    second = lab.run()
    print(
        json.dumps(
            {
                "first_resumed": first.resumed,
                "second_resumed": second.resumed,
                "scores": [
                    second.a0_score,
                    second.a1_score,
                    second.a2_score,
                ],
                "final_gain": second.final_gain,
                "best_round": second.best_round,
                "monotonic_score": second.monotonic_score,
                "final_task_changes": {
                    "improved": second.improved_tasks,
                    "regressed": second.regressed_tasks,
                    "tied": second.tied_tasks,
                },
                "same_model_comparator_score": second.comparator_score,
                "anchor_rank": second.anchor_rank,
                "anchor_pairwise": {
                    "wins": second.anchor_wins,
                    "losses": second.anchor_losses,
                    "ties": second.anchor_ties,
                },
                "mismatched_model_rejected": (
                    second.mismatched_model_rejected
                ),
                "submission_prerequisites_met_count": (
                    second.submission_prerequisites_met_count
                ),
                "same_package": first.package_hash == second.package_hash,
                "registry_events": second.registry_event_count,
                "harbor_execution_performed_by_evoagent": (
                    second.harbor_execution_performed_by_evoagent
                ),
                "external_model_call_performed_by_evoagent": (
                    second.external_model_call_performed_by_evoagent
                ),
                "upload_performed": second.upload_performed,
                "official_submission_performed": (
                    second.official_submission_performed
                ),
                "official_submission_accepted": (
                    second.official_submission_accepted
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
