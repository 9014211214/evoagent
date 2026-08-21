import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab


with TemporaryDirectory() as directory:
    lab = LocalAgenticRLTrainingLab(
        Path(directory) / "local-agentic-rl",
        source_commit="a" * 40,
    )
    first = lab.run()
    second = lab.run()
    print(
        json.dumps(
            {
                "first_resumed": first.resumed,
                "second_resumed": second.resumed,
                "first_optimizer_invoked": first.optimizer_invoked,
                "second_optimizer_invoked": second.optimizer_invoked,
                "baseline_score": second.baseline_score,
                "selected_score": second.selected_score,
                "baseline_unsafe_actions": second.baseline_unsafe_actions,
                "selected_unsafe_actions": second.selected_unsafe_actions,
                "iterations": second.iterations,
                "rollouts": second.rollouts,
                "parameter_updates": second.parameter_updates,
                "selected_iteration": second.selected_iteration,
                "parameter_delta_l2": second.parameter_delta_l2,
                "same_package": first.package_hash == second.package_hash,
                "tiny_tabular_policy_only": second.tiny_tabular_policy_only,
                "local_rollout_training_executed_by_evoagent": (
                    second.local_rollout_training_executed_by_evoagent
                ),
                "foundation_model_training_performed": (
                    second.foundation_model_training_performed
                ),
                "external_model_call_performed_by_evoagent": (
                    second.external_model_call_performed_by_evoagent
                ),
                "gpu_execution_performed": second.gpu_execution_performed,
                "network_execution_performed": second.network_execution_performed,
                "production_deployment_performed": (
                    second.production_deployment_performed
                ),
                "official_benchmark_claimed": second.official_benchmark_claimed,
            },
            indent=2,
            sort_keys=True,
        )
    )
