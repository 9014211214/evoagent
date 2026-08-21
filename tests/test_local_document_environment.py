from __future__ import annotations

import os

import pytest

from evoagent.domain.models import Task
from evoagent.runtime import (
    LocalDocumentEnvironment,
    LocalDocumentEnvironmentError,
    ToolCall,
)


def task(*, initial=None) -> Task:
    return Task(
        task_id="local-environment-test",
        task_type="local-document",
        input={"initial_documents": initial or {}},
    )


def test_reset_is_deterministic_and_removes_prior_episode_state(tmp_path):
    environment = LocalDocumentEnvironment(tmp_path / "episodes")
    first_observation = environment.reset(
        task(initial={"seed.txt": {"content": "stable", "protected": False}}),
        seed=7,
    )
    initial = environment.inspect_state()

    write = environment.execute(
        ToolCall(
            call_id="write-extra",
            tool_name="write_document",
            arguments={"path": "extra.txt", "content": "temporary"},
        )
    ).last_tool_result
    assert write is not None and write.ok is True
    assert environment.inspect_state().state_fingerprint != initial.state_fingerprint

    second_observation = environment.reset(
        task(initial={"seed.txt": {"content": "stable", "protected": False}}),
        seed=7,
    )
    restored = environment.inspect_state()

    assert first_observation.episode_id == second_observation.episode_id
    assert restored.state_fingerprint == initial.state_fingerprint
    assert set(restored.public_state["documents"]) == {"seed.txt"}
    assert restored.public_state["attempted_writes"] == ()


def test_protected_document_unknown_tool_and_traversal_fail_closed(tmp_path):
    environment = LocalDocumentEnvironment(tmp_path / "episodes")
    environment.reset(
        task(initial={"policy.txt": {"content": "stable", "protected": True}}),
        seed=3,
    )
    before = environment.inspect_state()

    protected = environment.execute(
        ToolCall(
            call_id="write-protected",
            tool_name="write_document",
            arguments={"path": "policy.txt", "content": "replacement"},
        )
    ).last_tool_result
    assert protected is not None
    assert protected.ok is False
    assert protected.error_code == "protected_document"
    after = environment.inspect_state()
    assert after.public_state["documents"]["policy.txt"]["content"] == "stable"
    assert after.public_state["attempted_writes"] == ("policy.txt",)
    assert after.state_fingerprint != before.state_fingerprint

    traversal = environment.execute(
        ToolCall(
            call_id="traversal",
            tool_name="read_document",
            arguments={"path": "../outside.txt"},
        )
    ).last_tool_result
    assert traversal is not None
    assert traversal.ok is False
    assert traversal.error_code == "unsafe_path"

    unknown = environment.execute(
        ToolCall(call_id="unknown", tool_name="shell", arguments={})
    ).last_tool_result
    assert unknown is not None
    assert unknown.ok is False
    assert unknown.error_code == "unknown_tool"


def test_document_symlink_is_never_followed(tmp_path):
    environment = LocalDocumentEnvironment(tmp_path / "episodes")
    environment.reset(task(), seed=5)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = environment.episode_root / "link.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("Symlinks are unavailable in this environment.")

    result = environment.execute(
        ToolCall(
            call_id="read-link",
            tool_name="read_document",
            arguments={"path": "link.txt"},
        )
    ).last_tool_result
    assert result is not None
    assert result.ok is False
    assert result.error_code == "unsafe_path"
    assert outside.read_text(encoding="utf-8") == "outside"


def test_environment_root_symlink_is_rejected_before_resolution(tmp_path):
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    linked_root = tmp_path / "linked-root"
    try:
        os.symlink(real_root, linked_root, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment.")

    with pytest.raises(LocalDocumentEnvironmentError, match="root must not be a symlink"):
        LocalDocumentEnvironment(linked_root)
