from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from evoagent.diagnosis import AttributionReport, CounterfactualAttributionEngine
from evoagent.domain.models import (
    EvolutionAction,
    EvolutionDecision,
    EvolutionTicket,
    FailureLayer,
)
from evoagent.evolution.controller import EvolutionController
from evoagent.runtime import (
    ExecutableCrossLayerCounterfactualRunner,
    ExecutableFaultScenario,
    build_conflicting_skill_router_scenario,
    build_executable_fault_scenario,
)


_ORDERED_LAYERS = (
    FailureLayer.SKILL,
    FailureLayer.ROUTER,
    FailureLayer.TOOL,
    FailureLayer.CONTEXT,
    FailureLayer.VERIFIER,
    FailureLayer.ENVIRONMENT,
    FailureLayer.MODEL,
)


class ExecutableLayerDispatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    injected_layers: tuple[FailureLayer, ...]
    baseline_trace_id: str
    baseline_feedback: str
    attribution: AttributionReport
    decision: EvolutionDecision
    supported_experiments: tuple[str, ...]
    counterfactual_trace_ids: dict[str, str]
    evolution_ticket: EvolutionTicket | None = None
    external_execution_performed: Literal[False] = False


class ExecutableCrossLayerMatrixResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    results: tuple[ExecutableLayerDispatchResult, ...]
    conflict: ExecutableLayerDispatchResult
    repeatable: Literal[True] = True
    external_execution_performed: Literal[False] = False


class ExecutableCrossLayerAttributionLab:
    """Execute, attribute and dispatch all supported local failure layers."""

    def __init__(self, root: str | Path):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Cross-layer lab root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine = CounterfactualAttributionEngine()
        self.controller = EvolutionController()

    def run(self) -> ExecutableCrossLayerMatrixResult:
        first_results, first_conflict = self._run_once()
        second_results, second_conflict = self._run_once()
        if self._signature(first_results, first_conflict) != self._signature(
            second_results, second_conflict
        ):
            raise RuntimeError("Executable cross-layer matrix was not repeatable.")
        return ExecutableCrossLayerMatrixResult(
            results=first_results,
            conflict=first_conflict,
        )

    def _run_once(
        self,
    ) -> tuple[
        tuple[ExecutableLayerDispatchResult, ...],
        ExecutableLayerDispatchResult,
    ]:
        results = tuple(
            self._run_scenario(build_executable_fault_scenario(layer))
            for layer in _ORDERED_LAYERS
        )
        conflict = self._run_scenario(build_conflicting_skill_router_scenario())
        return results, conflict

    def _run_scenario(
        self,
        scenario: ExecutableFaultScenario,
    ) -> ExecutableLayerDispatchResult:
        runner = ExecutableCrossLayerCounterfactualRunner(
            root=self.root / "scenarios",
            scenario=scenario,
        )
        report = self.engine.diagnose(runner)
        decision = self.controller.decide_attribution(report)
        self._validate_dispatch(scenario, report, decision)

        ticket = None
        if decision.action != EvolutionAction.ESCALATE:
            target_id = self._target_id(scenario, runner)
            ticket = self.controller.create_ticket(
                report,
                ticket_id=f"ticket:{scenario.scenario_id}",
                target_id=target_id,
                evidence_trace_ids=[runner.baseline_trace.trace_id],
            )

        traces = runner.traces()
        return ExecutableLayerDispatchResult(
            scenario_id=scenario.scenario_id,
            injected_layers=scenario.fault_layers,
            baseline_trace_id=runner.baseline_trace.trace_id,
            baseline_feedback=runner.baseline_trace.verifier_feedback,
            attribution=report,
            decision=decision,
            supported_experiments=tuple(
                item.experiment_type.value
                for item in report.experiments
                if item.supports_hypothesis
            ),
            counterfactual_trace_ids={
                experiment_id: trace.trace_id
                for experiment_id, trace in traces.items()
            },
            evolution_ticket=ticket,
        )

    @staticmethod
    def _validate_dispatch(
        scenario: ExecutableFaultScenario,
        report: AttributionReport,
        decision: EvolutionDecision,
    ) -> None:
        if len(scenario.fault_layers) == 1:
            expected_layer = scenario.expected_layer
            expected_action = scenario.expected_action
            if report.root_cause_layer != expected_layer:
                raise RuntimeError(
                    f"Scenario {scenario.scenario_id} attributed to "
                    f"{report.root_cause_layer.value}, expected {expected_layer.value}."
                )
            if expected_action == EvolutionAction.ESCALATE:
                if report.actionable:
                    raise RuntimeError(
                        f"Escalated scenario was incorrectly marked actionable: {scenario.scenario_id}"
                    )
            elif not report.actionable:
                raise RuntimeError(
                    f"Single-layer scenario is not actionable: {scenario.scenario_id}"
                )
            if report.recommended_action != expected_action:
                raise RuntimeError(
                    f"Scenario {scenario.scenario_id} recommended "
                    f"{report.recommended_action.value}, expected {expected_action.value}."
                )
            if decision.action != expected_action:
                raise RuntimeError(
                    f"Controller dispatched {decision.action.value}, expected "
                    f"{expected_action.value}."
                )
            supported = [item for item in report.experiments if item.supports_hypothesis]
            if len(supported) != 1:
                raise RuntimeError(
                    f"Single-layer scenario has {len(supported)} supported interventions."
                )
            supported_layer = {
                "replace_skill": FailureLayer.SKILL,
                "force_router": FailureLayer.ROUTER,
                "replay_tool": FailureLayer.TOOL,
                "complete_context": FailureLayer.CONTEXT,
                "oracle_verifier": FailureLayer.VERIFIER,
                "reset_environment": FailureLayer.ENVIRONMENT,
                "reference_model": FailureLayer.MODEL,
            }[supported[0].experiment_type.value]
            if supported_layer != expected_layer:
                raise RuntimeError("Supported intervention does not match injected layer.")
            return

        if (
            report.root_cause_layer != FailureLayer.UNKNOWN
            or report.actionable
            or report.recommended_action != EvolutionAction.ESCALATE
            or decision.action != EvolutionAction.ESCALATE
        ):
            raise RuntimeError("Conflicting multi-layer scenario did not escalate safely.")
        if sum(item.supports_hypothesis for item in report.experiments) < 2:
            raise RuntimeError("Conflict scenario did not produce competing causal explanations.")

    @staticmethod
    def _target_id(
        scenario: ExecutableFaultScenario,
        runner: ExecutableCrossLayerCounterfactualRunner,
    ) -> str:
        layer = scenario.fault_layers[0]
        if layer == FailureLayer.SKILL:
            return f"skill:{runner.baseline_trace.skill_id}@{runner.baseline_trace.skill_version}"
        if layer == FailureLayer.ROUTER:
            return f"router:{scenario.scenario_id}"
        if layer == FailureLayer.TOOL:
            return "tool:local-document-write"
        if layer == FailureLayer.CONTEXT:
            return f"context:{runner.baseline_trace.task.task_id}"
        if layer == FailureLayer.VERIFIER:
            return "verifier:local-document-v1"
        if layer == FailureLayer.MODEL:
            return f"model:{runner.baseline_trace.model_id}"
        raise RuntimeError("Environment and unknown failures cannot create automatic tickets.")

    @staticmethod
    def _signature(
        results: tuple[ExecutableLayerDispatchResult, ...],
        conflict: ExecutableLayerDispatchResult,
    ) -> tuple:
        return tuple(
            (
                item.scenario_id,
                tuple(layer.value for layer in item.injected_layers),
                item.baseline_trace_id,
                item.attribution.root_cause_layer.value,
                item.attribution.recommended_action.value,
                item.attribution.actionable,
                item.decision.action.value,
                item.supported_experiments,
                tuple(sorted(item.counterfactual_trace_ids.items())),
                (
                    item.evolution_ticket.model_dump_json()
                    if item.evolution_ticket is not None
                    else None
                ),
            )
            for item in (*results, conflict)
        )


__all__ = [
    "ExecutableCrossLayerAttributionLab",
    "ExecutableCrossLayerMatrixResult",
    "ExecutableLayerDispatchResult",
]
