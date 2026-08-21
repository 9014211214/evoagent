from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.domain.models import ExecutionTrace, Task
from evoagent.traces import JsonlTraceStore, TraceTrustLevel

with TemporaryDirectory() as directory:
    store = JsonlTraceStore(Path(directory) / "traces.jsonl")
    store.append(
        ExecutionTrace(
            trace_id="trace:demo:1",
            task=Task(task_id="task:demo:1", task_type="synthetic-note", input={}),
            model_id="public/model-v0",
            skill_id="create_a_verified_note",
            skill_version="0.1.0",
            observable_events=({"event": "tool_called", "tool": "create_note"},),
            final_output={"status": "created"},
            verifier_passed=True,
            verifier_feedback="",
            cost={"llm_tokens": 10},
        ),
        source="synthetic-demo",
        trust_level=TraceTrustLevel.SYNTHETIC,
    )
    print("verified:", store.verify())
    print("records:", len(store.list()))
    print("successful:", len(store.query(verifier_passed=True)))
