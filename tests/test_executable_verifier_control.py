from __future__ import annotations

from evoagent.diagnosis import CounterfactualAttributionEngine
from evoagent.domain.models import FailureLayer
from evoagent.runtime import (
    ExecutableCrossLayerCounterfactualRunner,
    build_executable_fault_scenario,
)


def final_fingerprint(trace):
    for event in reversed(trace.observable_events):
        if event.get("event") == "verification":
            return event["final_state_fingerprint"]
    raise AssertionError("verification event missing")


def tool_result_payloads(trace):
    return [
        event["result"]
        for event in trace.observable_events
        if event.get("event") == "tool_result"
    ]


def test_oracle_verifier_changes_only_the_verification_decision(tmp_path):
    runner = ExecutableCrossLayerCounterfactualRunner(
        root=tmp_path / "verifier-control",
        scenario=build_executable_fault_scenario(FailureLayer.VERIFIER),
    )
    report = CounterfactualAttributionEngine().diagnose(runner)
    oracle = runner.traces()["exp:verifier"]
    baseline = runner.baseline_trace

    assert baseline.verifier_passed is False
    assert baseline.verifier_feedback == "verifier_fault: false_negative"
    assert oracle.verifier_passed is True
    assert baseline.task == oracle.task
    assert baseline.model_id == oracle.model_id
    assert baseline.skill_id == oracle.skill_id
    assert baseline.skill_version == oracle.skill_version
    assert baseline.final_output == oracle.final_output
    assert baseline.cost["steps"] == oracle.cost["steps"]
    assert baseline.cost["tool_calls"] == oracle.cost["tool_calls"]
    assert tool_result_payloads(baseline) == tool_result_payloads(oracle)
    assert final_fingerprint(baseline) == final_fingerprint(oracle)

    supported = [
        item.experiment_type.value
        for item in report.experiments
        if item.supports_hypothesis
    ]
    assert supported == ["oracle_verifier"]
