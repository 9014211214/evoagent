from __future__ import annotations

from evoagent.diagnosis import CounterfactualAttributionEngine
from evoagent.domain.models import Task
from evoagent.runtime import (
    DocumentSkillPolicy,
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    LocalToolCounterfactualRunner,
    RuntimeLimits,
    ToolAgentRuntime,
    snapshot_from_skill_spec,
)
from evoagent.skills import SkillSpec


def training_task() -> Task:
    return Task(
        task_id="local:counterfactual-protected",
        task_type="local-document-evolution-train",
        input={
            "initial_documents": {
                "config.txt": {"content": "stable", "protected": True}
            },
            "target_path": "config.txt",
            "content": "replacement",
            "expected_status": "blocked",
            "require_verification": True,
        },
    )


def initial_skill() -> SkillSpec:
    return SkillSpec(
        skill_id="local_document_writer",
        name="Local Document Writer",
        version="1.0.0",
        description="Write and verify a local document.",
        rules=("verify_after_write",),
        allowed_tools=("read_document", "write_document", "list_documents"),
    )


def runtime_factory(root):
    def create():
        return ToolAgentRuntime(
            environment_factory=lambda: LocalDocumentEnvironment(root / "episodes"),
            policy=DocumentSkillPolicy(),
            verifier=DocumentTaskVerifier(),
            limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=5),
            seed=31,
        )

    return create


def test_actual_counterfactual_replays_attribute_only_the_skill_layer(tmp_path):
    task = training_task()
    snapshot = snapshot_from_skill_spec(
        initial_skill(),
        snapshot_id="A0-counterfactual",
        round_index=0,
        model_id="synthetic/local-document-policy-v1",
    )
    factory = runtime_factory(tmp_path)
    baseline = factory().run(task, snapshot)
    assert baseline.verifier_passed is False
    assert baseline.verifier_feedback == "missing_skill_rule: inspect_before_write"

    runner = LocalToolCounterfactualRunner(
        runtime_factory=factory,
        task=task,
        baseline_snapshot=snapshot,
        baseline_trace=baseline,
    )
    report = CounterfactualAttributionEngine().diagnose(runner)

    assert report.root_cause_layer.value == "skill"
    assert report.recommended_action.value == "update_skill"
    assert report.actionable is True
    assert runner.structured_rule == "inspect_before_write"
    assert len(report.experiments) == 7
    supported = [
        item.experiment_type.value
        for item in report.experiments
        if item.supports_hypothesis
    ]
    assert supported == ["replace_skill"]
    assert all(item.baseline_success is False for item in report.experiments)

    traces = runner.traces()
    assert traces["exp:skill"].verifier_passed is True
    assert traces["exp:skill"].final_output["status"] == "blocked"
    for experiment_id, trace in traces.items():
        if experiment_id != "exp:skill":
            assert trace.verifier_passed is False
    assert all(trace.task.task_id == task.task_id for trace in traces.values())
    assert all(trace.cost["cost_usd"] == 0.0 for trace in traces.values())
    assert all(trace.cost["llm_tokens"] == 0.0 for trace in traces.values())


def test_missing_structured_rule_cannot_create_causal_skill_evidence(tmp_path):
    task = training_task()
    snapshot = snapshot_from_skill_spec(
        initial_skill(),
        snapshot_id="A0-no-rule",
        round_index=0,
        model_id="synthetic/local-document-policy-v1",
    )
    factory = runtime_factory(tmp_path)
    baseline = factory().run(task, snapshot).model_copy(
        deep=True,
        update={"verifier_feedback": "protected document attempt without structured patch"},
    )
    runner = LocalToolCounterfactualRunner(
        runtime_factory=factory,
        task=task,
        baseline_snapshot=snapshot,
        baseline_trace=baseline,
    )
    report = CounterfactualAttributionEngine().diagnose(runner)

    assert runner.structured_rule is None
    assert report.root_cause_layer.value == "unknown"
    assert report.actionable is False
    assert report.recommended_action.value == "escalate"
    assert not any(item.supports_hypothesis for item in report.experiments)
