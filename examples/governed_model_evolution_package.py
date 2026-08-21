from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.lab import GovernedModelEvolutionLab


with TemporaryDirectory() as directory:
    lab = GovernedModelEvolutionLab(
        Path(directory) / "model-lab",
        source_commit="4444444444444444444444444444444444444444",
    )
    first = lab.run()
    second = lab.run()

    print("first resumed:", first.resumed)
    print("second resumed:", second.resumed)
    print("evidence tasks:", len(first.evidence_task_ids))
    print("persisted traces:", first.persisted_trace_count)
    print("dataset manifest:", first.dataset_manifest_hash)
    print("SFT examples:", first.supervised_example_count)
    print("preference pairs:", first.preference_pair_count)
    print("RL seeds:", first.replay_seed_count)
    print("campaign state:", first.campaign_state)
    print("same campaign:", second.campaign_id == first.campaign_id)
    print("same candidate:", second.model_candidate == first.model_candidate)
    print("selected method:", first.model_candidate.method.value)
    print("rollout budget:", first.model_candidate.task_spec.rollout_budget)
    print("training executed:", first.training_executed)
    print("external work performed:", first.external_execution_performed)
