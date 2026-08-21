import pytest

from evoagent.execution import (
    ExecutionAdapter,
    ExecutionBudget,
    ExecutionInvocation,
)


def values(tmp_path, adapter):
    workspace = tmp_path / adapter.value
    workspace.mkdir()
    return dict(
        adapter=adapter,
        command=(adapter.value, "synthetic-task"),
        workspace=str(workspace.resolve()),
        network_access=True,
        budget=ExecutionBudget(max_wall_seconds=30),
        expected_version_pattern="version",
    )


def test_harbor_version_probe_is_fixed(tmp_path):
    with pytest.raises(ValueError, match="version probe"):
        ExecutionInvocation(
            **values(tmp_path, ExecutionAdapter.HARBOR),
            version_arguments=("run", "--side-effect"),
        )


def test_ml_intern_version_probe_is_fixed(tmp_path):
    with pytest.raises(ValueError, match="version probe"):
        ExecutionInvocation(
            **values(tmp_path, ExecutionAdapter.ML_INTERN),
            version_arguments=("synthetic-task",),
        )


def test_resource2skill_version_probe_is_fixed(tmp_path):
    with pytest.raises(ValueError, match="version probe"):
        ExecutionInvocation(
            **values(tmp_path, ExecutionAdapter.RESOURCE2SKILL),
            version_arguments=("cli.py", "validate-domain"),
        )
