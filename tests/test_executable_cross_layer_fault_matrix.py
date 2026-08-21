from __future__ import annotations

import json

import pytest

from evoagent.diagnosis import CounterfactualAttributionEngine
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.runtime import (
    ExecutableCrossLayerCounterfactualRunner,
    build_conflicting_skill_router_scenario,
    build_executable_fault_scenario,
)


EXPECTED = {
    FailureLayer.SKILL: ("replace_skill", EvolutionAction.UPDATE_SKILL),
    FailureLayer.ROUTER: ("force_router", EvolutionAction.UPDATE_ROUTER),
    FailureLayer.TOOL: ("replay_tool", EvolutionAction.REPAIR_TOOL),
    FailureLayer.CONTEXT: ("complete_context", EvolutionAction.UPDATE_CONTEXT),
    FailureLayer.VERIFIER: ("oracle_verifier", EvolutionAction.REPAIR_VERIFIER),
    FailureLayer.ENVIRONMENT: ("reset_environment", EvolutionAction.ESCALATE),
    FailureLayer.MODEL: ("reference_model", EvolutionAction.TRAIN_MODEL),
}


@pytest.mark.parametrize("layer", tuple(EXPECTED))
def test_each_executable_fault_has_exactly_one_matching_counterfactual(tmp_path, layer):
    runner = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path / "matrix",
        scenario=build_executable_fault_scenario(layer),
    )
    report = CounterfactualAttributionEngine().diagnose(runner)
    expected_experiment, expected_action = EXPECTED[layer]

    assert runner.baseline_trace.verifier_passed is False
    assert report.root_cause_layer == layer
    assert report.recommended_action == expected_action
    assert report.actionable is (expected_action != EvolutionAction.ESCALATE)
    assert len(report.experiments) == 7
    supported = [
        item.experiment_type.value
        for item in report.experiments
        if item.supports_hypothesis
    ]
    assert supported == [expected_experiment]
    traces = runner.traces()
    assert len(traces) == 7
    assert all(trace.task.task_id == runner.baseline_task.task_id for trace in traces.values())
    assert all(trace.task.input == runner.baseline_task.input for trace in traces.values())
    assert traces[f"exp:{layer.value}"].verifier_passed is True
    for experiment_id, trace in traces.items():
        if experiment_id != f"exp:{layer.value}":
            assert trace.verifier_passed is False
    assert all(trace.cost["llm_tokens"] == 0.0 for trace in traces.values())
    assert all(trace.cost["cost_usd"] == 0.0 for trace in traces.values())


def test_router_fault_has_a_correct_skill_but_selects_the_wrong_one(tmp_path):
    runner = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path,
        scenario=build_executable_fault_scenario(FailureLayer.ROUTER),
    )
    report = CounterfactualAttributionEngine().diagnose(runner)

    assert set(runner.baseline_snapshot.skills) == {
        "safe_document_writer",
        "unsafe_document_writer",
    }
    assert runner.baseline_snapshot.metadata["active_skill_id"] == "unsafe_document_writer"
    assert runner.baseline_snapshot.metadata["reference_skill_id"] == "safe_document_writer"
    assert runner.baseline_trace.skill_id == "unsafe_document_writer"
    assert report.experiments[0].experiment_type.value == "replace_skill"
    assert report.experiments[0].counterfactual_success is False
    router_result = next(
        item for item in report.experiments if item.experiment_type.value == "force_router"
    )
    assert router_result.counterfactual_success is True
    assert runner.traces()["exp:router"].skill_id == "safe_document_writer"


def test_tool_environment_verifier_context_and_model_faults_have_distinct_evidence(tmp_path):
    tool = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path,
        scenario=build_executable_fault_scenario(FailureLayer.TOOL),
    )
    CounterfactualAttributionEngine().diagnose(tool)
    tool_results = [
        event["result"]
        for event in tool.baseline_trace.observable_events
        if event.get("event") == "tool_result"
    ]
    assert tool_results[-1]["error_code"] == "tool_backend_failure"

    environment = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path,
        scenario=build_executable_fault_scenario(FailureLayer.ENVIRONMENT),
    )
    environment_report = CounterfactualAttributionEngine().diagnose(environment)
    assert environment_report.root_cause_layer == FailureLayer.ENVIRONMENT
    assert environment_report.recommended_action == EvolutionAction.ESCALATE
    assert environment_report.actionable is False
    assert environment.baseline_trace.final_output["status"] == "blocked"
    assert environment.baseline_trace.cost["tool_calls"] == 1.0
    assert environment.traces()["exp:environment"].final_output["status"] == "completed"

    verifier = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path,
        scenario=build_executable_fault_scenario(FailureLayer.VERIFIER),
    )
    CounterfactualAttributionEngine().diagnose(verifier)
    assert verifier.baseline_trace.final_output["status"] == "completed"
    assert verifier.baseline_trace.verifier_feedback == "verifier_fault: false_negative"
    assert verifier.traces()["exp:verifier"].verifier_passed is True

    context = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path,
        scenario=build_executable_fault_scenario(FailureLayer.CONTEXT),
    )
    CounterfactualAttributionEngine().diagnose(context)
    assert context.baseline_trace.task.input["content"] == "synthetic cross-layer result"
    assert context.baseline_trace.final_output["status"] == "configuration_error"
    assert context.baseline_trace.cost["tool_calls"] == 0.0
    assert context.traces()["exp:context"].task.input == context.baseline_trace.task.input
    assert context.traces()["exp:context"].final_output["status"] == "completed"

    model = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path,
        scenario=build_executable_fault_scenario(FailureLayer.MODEL),
    )
    CounterfactualAttributionEngine().diagnose(model)
    assert model.baseline_trace.final_output == {
        "status": "failed",
        "error_code": "model_capability_failure",
    }
    assert model.baseline_trace.cost["tool_calls"] == 0.0
    assert model.traces()["exp:model"].verifier_passed is True
    assert model.traces()["exp:model"].model_id == (
        "synthetic/reference-local-document-policy-v1"
    )


def test_competing_skill_and_router_repairs_escalate_instead_of_guessing(tmp_path):
    runner = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path,
        scenario=build_conflicting_skill_router_scenario(),
    )
    report = CounterfactualAttributionEngine().diagnose(runner)

    supported = {
        item.experiment_type.value
        for item in report.experiments
        if item.supports_hypothesis
    }
    assert supported == {"replace_skill", "force_router"}
    assert report.root_cause_layer == FailureLayer.UNKNOWN
    assert report.recommended_action == EvolutionAction.ESCALATE
    assert report.actionable is False
    assert "Conflicting counterfactuals" in report.reason

    serialized = json.dumps(
        {
            "baseline": runner.baseline_trace.model_dump(mode="json"),
            "traces": {
                key: value.model_dump(mode="json")
                for key, value in runner.traces().items()
            },
        },
        sort_keys=True,
    ).lower()
    assert "chain_of_thought" not in serialized
    assert "scratchpad" not in serialized
    assert "traceback" not in serialized
