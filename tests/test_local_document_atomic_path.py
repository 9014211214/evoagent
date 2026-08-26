from pathlib import Path

from evoagent.domain.models import Task
from evoagent.runtime import LocalDocumentEnvironment, ToolCall


def test_atomic_write_temp_name_does_not_repeat_long_target_name(tmp_path: Path):
    long_name = "a" * 120 + ".txt"
    task = Task(
        task_id="long-atomic-path",
        task_type="local-document",
        input={"initial_documents": {}},
    )
    environment = LocalDocumentEnvironment(tmp_path / "episodes")
    environment.reset(task, seed=1)

    result = environment.execute(
        ToolCall(
            call_id="write-long-name",
            tool_name="write_document",
            arguments={"path": long_name, "content": "bounded"},
        )
    ).last_tool_result

    assert result is not None and result.ok is True
    assert (environment.episode_root / long_name).read_text(encoding="utf-8") == "bounded"
    assert not tuple(environment.episode_root.glob(".evo-*.tmp"))
