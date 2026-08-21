from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evoagent.cli import main
from evoagent.execution import (
    ExecutionAdapter,
    ExecutionAuthorizationManager,
    ExecutionBudget,
    ExecutionInvocation,
)


def fake_cli(tmp_path: Path) -> Path:
    path = tmp_path / "fake-harbor"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('0.16.1' if '--version' in sys.argv else 'ok')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_execution_cli_creates_unsigned_request_and_runs_read_only_preflight(
    tmp_path, capsys, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invocation = ExecutionInvocation(
        adapter=ExecutionAdapter.HARBOR,
        command=(str(fake_cli(tmp_path)), "run", "--synthetic"),
        workspace=str(workspace.resolve()),
        required_environment_variables=("MODEL_API_KEY",),
        network_access=True,
        budget=ExecutionBudget(max_cost_usd=1, max_wall_seconds=30),
        version_arguments=("--version",),
        expected_version_pattern=r"^0\.16\.1$",
    )
    invocation_path = tmp_path / "invocation.json"
    invocation_path.write_text(invocation.model_dump_json(indent=2), encoding="utf-8")
    request_path = tmp_path / "request.json"
    issued = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)

    assert main(
        [
            "execution",
            "request",
            "--invocation",
            str(invocation_path),
            "--request-id",
            "request:cli:1",
            "--requester",
            "requester",
            "--purpose",
            "synthetic preflight",
            "--issued-at",
            issued.isoformat(),
            "--expires-at",
            (issued + timedelta(hours=1)).isoformat(),
            "--out",
            str(request_path),
        ]
    ) == 0
    request_output = json.loads(capsys.readouterr().out)
    assert request_output["required_approvals"] == 2
    assert request_path.is_file()

    manager = ExecutionAuthorizationManager()
    request = manager.load_request(request_path)
    approvals = tuple(
        manager.approve(
            request,
            approver_id=f"reviewer-{index}",
            approved_at=issued + timedelta(minutes=index),
            reason="synthetic review",
        )
        for index in (1, 2)
    )
    authorization = manager.authorize(request, approvals=approvals)
    authorization_path = tmp_path / "authorization.json"
    manager.write_authorization(authorization, authorization_path)

    assert main(
        ["execution", "show-request", "--request", str(request_path)]
    ) == 0
    shown_request = json.loads(capsys.readouterr().out)
    assert shown_request["request_hash"] == request.request_hash

    assert main(
        [
            "execution",
            "show-authorization",
            "--authorization",
            str(authorization_path),
        ]
    ) == 0
    shown_authorization = json.loads(capsys.readouterr().out)
    assert shown_authorization["authorization_hash"] == authorization.authorization_hash

    monkeypatch.setenv("MODEL_API_KEY", "never-print-this-value")
    assert main(
        [
            "execution",
            "preflight",
            "--authorization",
            str(authorization_path),
            "--invocation",
            str(invocation_path),
            "--now",
            (issued + timedelta(minutes=10)).isoformat(),
        ]
    ) == 0
    preflight_text = capsys.readouterr().out
    preflight = json.loads(preflight_text)
    assert preflight["environment_presence"] == {"MODEL_API_KEY": True}
    assert "never-print-this-value" not in preflight_text
    assert list(workspace.iterdir()) == []
