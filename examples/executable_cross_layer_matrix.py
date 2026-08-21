from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.lab import ExecutableCrossLayerAttributionLab

with TemporaryDirectory() as directory:
    result = ExecutableCrossLayerAttributionLab(
        Path(directory) / "cross-layer-matrix"
    ).run()

    for item in result.results:
        print(
            f"fault={item.injected_layers[0].value:<11} "
            f"attributed={item.attribution.root_cause_layer.value:<11} "
            f"action={item.decision.action.value:<15} "
            f"supported={list(item.supported_experiments)} "
            f"ticket={item.evolution_ticket is not None}"
        )

    print("conflict layers:", [item.value for item in result.conflict.injected_layers])
    print("conflict supported:", list(result.conflict.supported_experiments))
    print("conflict action:", result.conflict.decision.action.value)
    print("repeatable:", result.repeatable)
    print("external execution performed:", result.external_execution_performed)
