from __future__ import annotations

import pytest

from evoagent.diagnosis import CounterfactualAttributionEngine
from evoagent.domain.models import FailureLayer
from evoagent.runtime import (
    ExecutableCrossLayerCounterfactualRunner,
    build_executable_fault_scenario,
)


LAYERS = (
    FailureLayer.SKILL,
    FailureLayer.ROUTER,
    FailureLayer.TOOL,
    FailureLayer.CONTEXT,
    FailureLayer.VERIFIER,
    FailureLayer.ENVIRONMENT,
    FailureLayer.MODEL,
)


@pytest.mark.parametrize("layer", LAYERS)
def test_counterfactuals_hold_all_non_intervened_identifiers_and_budgets_fixed(
    tmp_path,
    layer,
):
    runner = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path / "controls",
        scenario=build_executable_fault_scenario(layer),
    )
    report = CounterfactualAttributionEngine().diagnose(runner)
    traces = runner.traces()
    baseline = runner.baseline_trace

    assert len(traces) == 7
    assert len({baseline.trace_id, *(trace.trace_id for trace in traces.values())}) == 8
    for experiment_id, trace in traces.items():
        assert trace.task.task_id == baseline.task.task_id
        assert trace.task.input == baseline.task.input
        assert trace.task.expected_outcome == baseline.task.expected_outcome
        assert trace.cost["steps"] <= 6.0
        assert trace.cost["tool_calls"] <= 4.0
        assert trace.cost["llm_tokens"] == 0.0
        assert trace.cost["cost_usd"] == 0.0

        if layer == FailureLayer.MODEL and experiment_id == "exp:model":
            assert trace.model_id == "synthetic/reference-local-document-policy-v1"
            assert trace.model_id != baseline.model_id
        else:
            assert trace.model_id == baseline.model_id

        if layer == FailureLayer.ROUTER and experiment_id == "exp:router":
            assert baseline.skill_id == "unsafe_document_writer"
            assert trace.skill_id == "safe_document_writer"
        else:
            assert trace.skill_id == baseline.skill_id

        if layer == FailureLayer.SKILL and experiment_id == "exp:skill":
            assert trace.skill_version == "1.1.0-counterfactual"
            assert trace.skill_id == baseline.skill_id
        elif not (layer == FailureLayer.ROUTER and experiment_id == "exp:router"):
            assert trace.skill_version == baseline.skill_version

    if layer == FailureLayer.CONTEXT:
        assert baseline.task.input["content"] == "synthetic cross-layer result"
        assert baseline.final_output["status"] == "configuration_error"
        assert baseline.cost["tool_calls"] == 0.0
        assert traces["exp:context"].task.input == baseline.task.input
        assert traces["exp:context"].final_output["status"] == "completed"

    supported = [
        item.experiment_id for item in report.experiments if item.supports_hypothesis
    ]
    assert supported == [f"exp:{layer.value}"]
