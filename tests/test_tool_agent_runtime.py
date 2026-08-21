from __future__ import annotations

import json
from time import sleep

import pytest

from evoagent.domain.models import AgentSnapshot, Skill, Task
from evoagent.runtime import (
    AgentAction,
    AgentContext,
    DocumentSkillPolicy,
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    RuntimeLimits,
    ToolAgentPolicy,
    ToolAgentRuntime,
)


SKILL_ID = "document_writer"
MODEL_ID = "synthetic/local-document-policy-v1"


def snapshot(*rules: str, snapshot_id: str = "A0") -> AgentSnapshot:
    skill = Skill(
        skill_id=SKILL_ID,
        name="Document Writer",
        version="1.0.0" if snapshot_id == "A0" else "1.1.0",
        description="Synthetic local document policy.",
        rules=list(rules),
    )
    return AgentSnapshot(
        snapshot_id=snapshot_id,
        round_index=0 if snapshot_id == "A0" else 1,
        model_id=MODEL_ID,
        skills={SKILL_ID: skill},
        metadata={"active_skill_id": SKILL_ID},
    )


def create_task() -> Task:
    return Task(
        task_id="local:create",
        task_type="local-document",
        input={
            "initial_documents": {},
            "target_path": "note.txt",
            "content": "synthetic note",
            "expected_status": "completed",
            "require_verification": True,
        },
    )


def protected_task() -> Task:
    return Task(
        task_id="local:protected",
        task_type="local-document",
        input={
            "initial_documents": {
                "policy.txt": {"content": "stable", "protected": True}
            },
            "target_path": "policy.txt",
            "content": "replacement",
            "expected_status": "blocked",
            "require_verification": True,
        },
    )


def runtime(tmp_path, *, limits=None, policy=None) -> ToolAgentRuntime:
    return ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "episodes"),
        policy=policy or DocumentSkillPolicy(),
        verifier=DocumentTaskVerifier(),
        limits=limits or RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=5),
        seed=17,
    )


def test_runtime_executes_real_local_tools_and_records_only_observable_events(tmp_path):
    trace = runtime(tmp_path).run(
        create_task(),
        snapshot("verify_after_write"),
    )

    assert trace.verifier_passed is True
    assert trace.final_output == {
        "status": "completed",
        "path": "note.txt",
        "verified": True,
    }
    assert trace.skill_id == SKILL_ID
    assert trace.cost["tool_calls"] == 2.0
    assert trace.cost["llm_tokens"] == 0.0
    event_names = [item["event"] for item in trace.observable_events]
    assert event_names[0] == "environment_reset"
    assert event_names[-1] == "verification"
    assert event_names.count("tool_result") == 2
    verification = trace.observable_events[-1]
    assert verification["initial_state_fingerprint"] != verification["final_state_fingerprint"]

    serialized = json.dumps(trace.model_dump(mode="json"), sort_keys=True).lower()
    assert "chain_of_thought" not in serialized
    assert "scratchpad" not in serialized
    assert "traceback" not in serialized


def test_inspect_before_write_fixes_protected_document_failure_without_attempting_write(tmp_path):
    base = runtime(tmp_path).run(
        protected_task(),
        snapshot("verify_after_write", snapshot_id="A0"),
    )
    evolved = runtime(tmp_path).run(
        protected_task(),
        snapshot("verify_after_write", "inspect_before_write", snapshot_id="A1"),
    )

    assert base.verifier_passed is False
    assert base.verifier_feedback == "missing_skill_rule: inspect_before_write"
    assert base.final_output["status"] == "failed"
    assert base.cost["tool_calls"] == 1.0
    base_tools = [
        item["result"]["tool_name"]
        for item in base.observable_events
        if item["event"] == "tool_result"
    ]
    assert base_tools == ["write_document"]
    assert "attempted_protected_write" in base.observable_events[-1]["safety_violations"]

    assert evolved.verifier_passed is True
    assert evolved.final_output["status"] == "blocked"
    assert evolved.cost["tool_calls"] == 1.0
    evolved_tools = [
        item["result"]["tool_name"]
        for item in evolved.observable_events
        if item["event"] == "tool_result"
    ]
    assert evolved_tools == ["read_document"]


class EndlessReadPolicy(ToolAgentPolicy):
    def next_action(self, context: AgentContext) -> AgentAction:
        return AgentAction.call(
            f"{context.task.task_id}:{context.step_index}:read",
            "read_document",
            path=context.task.input["target_path"],
        )


class SlowReadPolicy(ToolAgentPolicy):
    def next_action(self, context: AgentContext) -> AgentAction:
        sleep(0.03)
        return AgentAction.call(
            f"{context.task.task_id}:{context.step_index}:read",
            "read_document",
            path=context.task.input["target_path"],
        )


class FailingPolicy(ToolAgentPolicy):
    def next_action(self, context: AgentContext) -> AgentAction:
        raise RuntimeError("private implementation detail")


@pytest.mark.parametrize(
    ("limits", "expected_limit", "expected_calls"),
    [
        (RuntimeLimits(max_steps=1, max_tool_calls=4, max_wall_seconds=5), "steps", 1.0),
        (RuntimeLimits(max_steps=4, max_tool_calls=1, max_wall_seconds=5), "tool_calls", 1.0),
    ],
)
def test_step_and_tool_call_limits_fail_closed(
    tmp_path,
    limits: RuntimeLimits,
    expected_limit: str,
    expected_calls: float,
):
    trace = runtime(tmp_path, limits=limits, policy=EndlessReadPolicy()).run(
        create_task(),
        snapshot("verify_after_write"),
    )

    assert trace.verifier_passed is False
    assert trace.verifier_feedback == f"runtime_limit_exceeded: {expected_limit}"
    assert trace.final_output == {"status": "limit_exceeded", "limit": expected_limit}
    assert trace.cost["tool_calls"] == expected_calls


def test_wall_budget_expires_before_slow_policy_action_can_call_a_tool(tmp_path):
    trace = runtime(
        tmp_path,
        limits=RuntimeLimits(max_steps=4, max_tool_calls=4, max_wall_seconds=0.01),
        policy=SlowReadPolicy(),
    ).run(create_task(), snapshot("verify_after_write"))

    assert trace.verifier_passed is False
    assert trace.verifier_feedback == "runtime_limit_exceeded: wall_time"
    assert trace.final_output == {"status": "limit_exceeded", "limit": "wall_time"}
    assert trace.cost["tool_calls"] == 0.0
    assert all(item["event"] != "tool_result" for item in trace.observable_events)


def test_policy_exception_fails_closed_without_persisting_message_or_stack(tmp_path):
    trace = runtime(tmp_path, policy=FailingPolicy()).run(
        create_task(), snapshot("verify_after_write")
    )

    assert trace.verifier_passed is False
    assert trace.verifier_feedback == "runtime_error"
    assert trace.final_output == {"status": "runtime_error", "error_type": "RuntimeError"}
    serialized = json.dumps(trace.model_dump(mode="json"), sort_keys=True).lower()
    assert "private implementation detail" not in serialized
    assert "traceback" not in serialized
