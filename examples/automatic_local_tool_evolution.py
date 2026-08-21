from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.lab import AutomaticLocalToolEvolutionLab

with TemporaryDirectory() as directory:
    lab = AutomaticLocalToolEvolutionLab(Path(directory) / "automatic-local-tool")
    first = lab.run()
    second = lab.run()

    print("first resumed:", first.resumed)
    print("second resumed:", second.resumed)
    print("training task:", first.training_task_id)
    print("frozen tasks:", list(first.frozen_task_ids))
    print("attributed layer:", first.attribution.root_cause_layer.value)
    print("supported experiments:", [
        item.experiment_type.value
        for item in first.attribution.experiments
        if item.supports_hypothesis
    ])
    print("added rules:", list(first.added_rules))
    print("base score:", first.summary.initial_score)
    print("candidate score:", first.summary.final_score)
    print("evolution gain:", first.summary.evolution_gain)
    print("regression count:", first.regression_count)
    print("active version:", first.active_version)
    print("campaign state:", first.campaign_state)
    print("same campaign:", second.campaign_id == first.campaign_id)
    print("same training trace:", second.training_trace_id == first.training_trace_id)
    print("versions:", second.skill_version_count)
    print("campaigns:", second.campaign_count)
    print("persisted traces:", second.persisted_trace_count)
    print("promotion events:", second.promotion_event_count)
    print("external execution performed:", first.external_execution_performed)
