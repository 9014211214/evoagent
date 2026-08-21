from __future__ import annotations

import json
import shutil
from pathlib import Path

from evoagent.lab import ShadowCanaryReleaseLab


ROOT = Path(".evoagent/shadow-canary-release-example")


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    lab = ShadowCanaryReleaseLab(ROOT, source_commit="9" * 40)
    first = lab.run()
    second = lab.run()
    print(
        json.dumps(
            {
                "first_resumed": first.resumed,
                "second_resumed": second.resumed,
                "same_drift_package": (
                    first.drift.package_hash == second.drift.package_hash
                ),
                "same_passing_package": (
                    first.passing.package_hash == second.passing.package_hash
                ),
                "drift_actions": second.drift.actions,
                "drift_final_state": second.drift.final_state,
                "drift_reasons": second.drift.rollback_reasons,
                "drift_allocation": (
                    second.drift.final_candidate_allocation_percent
                ),
                "passing_actions": second.passing.actions,
                "passing_final_state": second.passing.final_state,
                "passing_allocation": (
                    second.passing.final_candidate_allocation_percent
                ),
                "production_deployment_performed": (
                    second.production_deployment_performed
                ),
                "external_rollout_performed": (
                    second.external_rollout_performed_by_evoagent
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()