from __future__ import annotations

from pydantic import BaseModel, Field

from evoagent.diagnosis.counterfactual import (
    CounterfactualExperiment,
    CounterfactualRunner,
    ExperimentResult,
    ExperimentType,
)
from evoagent.domain.models import FailureLayer


_LAYER_BY_EXPERIMENT = {
    ExperimentType.REPLACE_SKILL: FailureLayer.SKILL,
    ExperimentType.FORCE_ROUTER: FailureLayer.ROUTER,
    ExperimentType.REPLAY_TOOL: FailureLayer.TOOL,
    ExperimentType.COMPLETE_CONTEXT: FailureLayer.CONTEXT,
    ExperimentType.ORACLE_VERIFIER: FailureLayer.VERIFIER,
    ExperimentType.RESET_ENVIRONMENT: FailureLayer.ENVIRONMENT,
    ExperimentType.REFERENCE_MODEL: FailureLayer.MODEL,
}


class SyntheticFaultScenario(BaseModel):
    scenario_id: str
    fault_layers: set[FailureLayer] = Field(default_factory=set)


class SyntheticCounterfactualRunner(CounterfactualRunner):
    """Deterministic fault-injection environment for attribution tests.

    A task succeeds only when every injected fault is removed. Each experiment
    changes exactly one layer and leaves all other layers fixed.
    """

    def __init__(self, scenario: SyntheticFaultScenario):
        self.scenario = scenario

    def run(self, experiment: CounterfactualExperiment) -> ExperimentResult:
        intervened_layer = _LAYER_BY_EXPERIMENT[experiment.experiment_type]
        baseline_success = not self.scenario.fault_layers
        remaining_faults = self.scenario.fault_layers - {intervened_layer}
        counterfactual_success = not remaining_faults
        supports = not baseline_success and counterfactual_success

        if supports:
            evidence = [
                f"Baseline failed with injected {intervened_layer.value} fault.",
                f"Replacing only {intervened_layer.value} changed the outcome to success.",
            ]
        else:
            evidence = [
                f"Replacing only {intervened_layer.value} did not resolve all active faults."
            ]

        return ExperimentResult(
            experiment_id=experiment.experiment_id,
            hypothesis_id=experiment.hypothesis_id,
            experiment_type=experiment.experiment_type,
            baseline_success=baseline_success,
            counterfactual_success=counterfactual_success,
            supports_hypothesis=supports,
            confidence=1.0,
            evidence=evidence,
            metadata={
                "scenario_id": self.scenario.scenario_id,
                "intervened_layer": intervened_layer.value,
                "remaining_faults": sorted(layer.value for layer in remaining_faults),
            },
        )
