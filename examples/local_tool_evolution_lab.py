from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.benchmarks import LocalToolEvolutionLab

with TemporaryDirectory() as directory:
    result = LocalToolEvolutionLab(Path(directory) / "local-tool-lab").run()
    first = result.first_run.evaluations

    print("A0 score:", result.summary.initial_score)
    print("A1 score:", result.summary.final_score)
    print("evolution gain:", result.summary.evolution_gain)
    print("A0 tasks:", first[0].per_task)
    print("A1 tasks:", first[1].per_task)
    print("repeatable:", result.repeatable)
    print("same model:", first[0].model_id == first[1].model_id)
    print("external execution performed:", result.external_execution_performed)
    print(
        "protected A0 feedback:",
        result.first_traces["A0-local-tool"]["local:protected-policy"].verifier_feedback,
    )
    print(
        "protected A1 status:",
        result.first_traces["A1-local-tool"]["local:protected-policy"].final_output["status"],
    )
