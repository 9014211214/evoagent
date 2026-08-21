from evoagent.diagnosis.counterfactual_engine import CounterfactualAttributionEngine
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import FailureLayer
from evoagent.evolution.controller import EvolutionController


def run_case(layer: FailureLayer) -> None:
    engine = CounterfactualAttributionEngine()
    runner = SyntheticCounterfactualRunner(
        SyntheticFaultScenario(
            scenario_id=f"demo:{layer.value}",
            fault_layers={layer},
        )
    )
    report = engine.diagnose(runner)
    decision = EvolutionController().decide_attribution(report)
    print(
        f"fault={layer.value:<11} "
        f"attributed={report.root_cause_layer.value:<11} "
        f"action={decision.action.value:<15} "
        f"confidence={report.confidence:.2f}"
    )


if __name__ == "__main__":
    for failure_layer in (
        FailureLayer.SKILL,
        FailureLayer.ROUTER,
        FailureLayer.TOOL,
        FailureLayer.CONTEXT,
        FailureLayer.VERIFIER,
        FailureLayer.ENVIRONMENT,
        FailureLayer.MODEL,
    ):
        run_case(failure_layer)
