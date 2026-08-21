import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.lab import MultiGenerationEvolutionProgramLab

with TemporaryDirectory() as directory:
    lab = MultiGenerationEvolutionProgramLab(
        Path(directory) / "program-lab",
        source_commit="a" * 40,
    )
    first = lab.run()
    second = lab.run()
    print(
        json.dumps(
            {
                "first_resumed": first.resumed,
                "second_resumed": second.resumed,
                "program_state": second.program_state,
                "decision_actions": second.decision_actions,
                "generation_statuses": second.generation_statuses,
                "same_champion_snapshot": second.same_champion_snapshot,
                "runtime_identity_changed": (
                    second.g0_runtime_config_sha256
                    != second.g1_runtime_config_sha256
                ),
                "authorization_started_generation": (
                    second.authorization_started_generation
                ),
                "budget_control": {
                    "action": second.budget_control_action,
                    "state": second.budget_control_state,
                },
                "ambiguous_control": {
                    "action": second.ambiguous_control_action,
                    "state": second.ambiguous_control_state,
                },
                "same_package": first.package_hash == second.package_hash,
                "training_executed_by_evoagent": (
                    second.training_executed_by_evoagent
                ),
                "external_rollout_performed_by_evoagent": (
                    second.external_rollout_performed_by_evoagent
                ),
                "production_deployment_performed": (
                    second.production_deployment_performed
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
