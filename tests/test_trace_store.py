import pytest

from evoagent.domain.models import ExecutionTrace, Task
from evoagent.traces import (
    DuplicateTraceError,
    JsonlTraceStore,
    TraceIntegrityError,
    TracePolicyError,
    TraceTrustLevel,
)


def trace(trace_id: str, *, passed: bool, event=None) -> ExecutionTrace:
    return ExecutionTrace(
        trace_id=trace_id,
        task=Task(task_id=f"task:{trace_id}", task_type="synthetic-note", input={"id": trace_id}),
        model_id="public/model-v0",
        skill_id="create_note",
        skill_version="0.1.0",
        observable_events=[event or {"event": "tool_called", "tool": "create_note"}],
        final_output={"status": "created" if passed else "failed"},
        verifier_passed=passed,
        verifier_feedback="" if passed else "status mismatch",
        cost={"llm_tokens": 10},
    )


def test_append_verify_query_and_duplicate_protection(tmp_path):
    store = JsonlTraceStore(tmp_path / "traces.jsonl")
    store.append(
        trace("trace:1", passed=True),
        source="synthetic-test",
        trust_level=TraceTrustLevel.SYNTHETIC,
    )
    store.append(
        trace("trace:2", passed=False),
        source="synthetic-test",
        trust_level=TraceTrustLevel.VERIFIED,
        safety_flags=("reviewed",),
    )

    assert store.verify() is True
    assert [item.sequence for item in store.list()] == [1, 2]
    failed = store.query(
        skill_id="create_note",
        skill_version="0.1.0",
        verifier_passed=False,
    )
    assert [item.trace.trace_id for item in failed] == ["trace:2"]

    with pytest.raises(DuplicateTraceError):
        store.append(
            trace("trace:2", passed=False),
            source="synthetic-test",
            trust_level=TraceTrustLevel.SYNTHETIC,
        )


def test_manual_trace_tampering_is_detected(tmp_path):
    path = tmp_path / "traces.jsonl"
    store = JsonlTraceStore(path)
    store.append(
        trace("trace:1", passed=True),
        source="synthetic-test",
        trust_level=TraceTrustLevel.SYNTHETIC,
    )
    content = path.read_text(encoding="utf-8")
    assert '"verifier_passed":true' in content
    path.write_text(content.replace('"verifier_passed":true', '"verifier_passed":false'), encoding="utf-8")

    with pytest.raises(TraceIntegrityError):
        store.list()


def test_hidden_reasoning_fields_are_rejected(tmp_path):
    store = JsonlTraceStore(tmp_path / "traces.jsonl")
    with pytest.raises(TracePolicyError):
        store.append(
            trace(
                "trace:hidden",
                passed=True,
                event={"chain_of_thought": "private scratchpad must not be stored"},
            ),
            source="synthetic-test",
            trust_level=TraceTrustLevel.SYNTHETIC,
        )


def test_external_checkpoint_detects_tail_truncation(tmp_path):
    path = tmp_path / "traces.jsonl"
    store = JsonlTraceStore(path)
    store.append(
        trace("trace:1", passed=True),
        source="synthetic-test",
        trust_level=TraceTrustLevel.SYNTHETIC,
    )
    store.append(
        trace("trace:2", passed=True),
        source="synthetic-test",
        trust_level=TraceTrustLevel.SYNTHETIC,
    )
    checkpoint = store.checkpoint()
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n", encoding="utf-8")

    assert store.verify() is True
    with pytest.raises(TraceIntegrityError):
        store.verify(checkpoint)
