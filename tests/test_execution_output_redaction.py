from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from evoagent.execution import (
    ExecutionAuthorizationManager,
    ExecutionBudget,
    SQLiteExecutionUseStore,
)
from evoagent.integrations import HarborCLIAdapter
from evoagent.training import MLInternCLIAdapter, MLInternTaskSpec


def leaking_cli(tmp_path: Path) -> Path:
    path = tmp_path / "leaking-cli"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('0.16.1')\n"
        "elif '--help' in sys.argv:\n"
        "    print('Hugging Face Agent CLI')\n"
        "else:\n"
        "    print('stdout=' + os.getenv('MODEL_API_KEY', os.getenv('HF_TOKEN', '')))\n"
        "    print('stderr=' + os.getenv('MODEL_API_KEY', os.getenv('HF_TOKEN', '')), file=sys.stderr)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def authorization(invocation):
    manager = ExecutionAuthorizationManager()
    issued = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
    request = manager.prepare_request(
        request_id=f"request:redaction:{invocation.adapter.value}",
        requester_id="requester",
        purpose="synthetic output-redaction test",
        issued_at=issued,
        expires_at=issued + timedelta(hours=1),
        invocation=invocation,
    )
    approvals = tuple(
        manager.approve(
            request,
            approver_id=f"reviewer-{index}",
            approved_at=issued + timedelta(minutes=index),
            reason="synthetic independent approval",
        )
        for index in (1, 2)
    )
    return manager, issued, manager.authorize(request, approvals=approvals)


def test_harbor_redacts_authorized_environment_values_from_output(tmp_path):
    executable = leaking_cli(tmp_path)
    workspace = tmp_path / "harbor-workspace"
    workspace.mkdir()
    adapter = HarborCLIAdapter(binary=str(executable), execution_enabled=True)
    spec = adapter.build_run(
        agent="synthetic-agent",
        model="synthetic/model",
        workspace=str(workspace),
        required_environment_variables=("MODEL_API_KEY",),
        max_cost_usd=1,
        max_wall_seconds=30,
    )
    invocation = adapter.to_execution_invocation(spec)
    manager, issued, approved = authorization(invocation)
    completed = adapter.execute(
        spec,
        authorization=approved,
        authorization_manager=manager,
        use_store=SQLiteExecutionUseStore(tmp_path / "harbor-uses.db"),
        environment={"MODEL_API_KEY": "super-secret-harbor-value"},
        now=issued + timedelta(minutes=10),
    )
    combined = (completed.stdout or "") + (completed.stderr or "")
    assert "super-secret-harbor-value" not in combined
    assert "[REDACTED]" in combined


def test_ml_intern_redacts_authorized_environment_values_from_output(tmp_path):
    executable = leaking_cli(tmp_path)
    workspace = tmp_path / "ml-workspace"
    workspace.mkdir()
    adapter = MLInternCLIAdapter(
        binary=str(executable),
        max_iterations=3,
        execution_enabled=True,
    )
    spec = MLInternTaskSpec(
        command=(
            str(executable),
            "--sandbox-tools",
            "--max-iterations",
            "3",
            "--no-stream",
            "synthetic experiment",
        ),
        prompt="synthetic experiment",
        workspace=str(workspace.resolve()),
        runtime_config={"tool_runtime": "sandbox", "share_traces": False},
        required_environment_variables=("HF_TOKEN",),
        execution_enabled=True,
    )
    budget = ExecutionBudget(
        max_cost_usd=1,
        max_gpu_hours=1,
        max_wall_seconds=30,
        max_iterations=3,
    )
    invocation = adapter.to_execution_invocation(spec, budget=budget)
    manager, issued, approved = authorization(invocation)
    completed = adapter.execute(
        spec,
        budget=budget,
        authorization=approved,
        authorization_manager=manager,
        use_store=SQLiteExecutionUseStore(tmp_path / "ml-uses.db"),
        environment={"HF_TOKEN": "super-secret-hf-value"},
        now=issued + timedelta(minutes=10),
    )
    combined = (completed.stdout or "") + (completed.stderr or "")
    assert "super-secret-hf-value" not in combined
    assert "[REDACTED]" in combined
