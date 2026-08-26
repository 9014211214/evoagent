from __future__ import annotations

import json
import tempfile
from pathlib import Path

from evoagent.lab import ClosedLoopEvolutionSupervisorLab


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="evoagent-closed-loop-") as root:
        lab = ClosedLoopEvolutionSupervisorLab(
            Path(root),
            source_commit="6" * 40,
        )
        first = lab.run()
        second = lab.run()
    print(
        json.dumps(
            {
                "first_resumed": first.resumed,
                "second_resumed": second.resumed,
                "run_status": second.run_status,
                "case_ids": second.case_ids,
                "tracks": second.tracks,
                "case_statuses": second.case_statuses,
                "skill_scores": [
                    second.skill_initial_score,
                    second.skill_final_score,
                ],
                "model_scores": [
                    second.model_initial_score,
                    second.model_final_score,
                ],
                "composite_scores": [
                    second.composite_initial_score,
                    second.composite_final_score,
                ],
                "composite_gain": second.composite_gain,
                "escalation_count": second.escalation_count,
                "same_package": first.package_hash == second.package_hash,
                "supervisor_events": second.supervisor_event_count,
                "training_executed_by_evoagent": (
                    second.training_executed_by_evoagent
                ),
                "external_execution_performed": (
                    second.external_execution_performed
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
