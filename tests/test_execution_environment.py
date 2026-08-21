from __future__ import annotations

import os

import pytest

from evoagent.execution import ExecutionAdapter, ExecutionBudget, ExecutionInvocation
from evoagent.execution.environment import (
    ExecutionEnvironmentError,
    build_authorized_environment,
)


def invocation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ExecutionInvocation(
        adapter=ExecutionAdapter.HARBOR,
        command=("harbor", "--version"),
        workspace=str(workspace.resolve()),
        required_environment_variables=("MODEL_API_KEY",),
        network_access=True,
        budget=ExecutionBudget(max_wall_seconds=30),
        expected_version_pattern=r"^0\.16",
    )


def test_only_essential_and_approved_environment_names_are_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "approved-value")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-inherited")
    environment = build_authorized_environment(invocation(tmp_path))

    assert environment["MODEL_API_KEY"] == "approved-value"
    assert "UNRELATED_SECRET" not in environment
    assert "PATH" in environment


def test_unapproved_supplied_environment_name_is_rejected(tmp_path):
    with pytest.raises(ExecutionEnvironmentError, match="unapproved variable names"):
        build_authorized_environment(
            invocation(tmp_path),
            {
                "MODEL_API_KEY": "approved-value",
                "UNAPPROVED_ENDPOINT": "https://example.invalid",
            },
        )
