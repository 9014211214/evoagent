from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.execution import (
    ExecutionAdapter,
    ExecutionAuthorizationManager,
    ExecutionBudget,
    ExecutionInvocation,
)


with TemporaryDirectory() as directory:
    workspace = Path(directory) / "workspace"
    workspace.mkdir()
    invocation = ExecutionInvocation(
        adapter=ExecutionAdapter.HARBOR,
        command=(sys.executable, "--version"),
        workspace=str(workspace.resolve()),
        network_access=True,
        budget=ExecutionBudget(max_cost_usd=0, max_wall_seconds=30),
        version_arguments=("--version",),
        expected_version_pattern=r"Python 3\.",
    )
    manager = ExecutionAuthorizationManager()
    issued = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    request = manager.prepare_request(
        request_id="request:synthetic-preflight:1",
        requester_id="requester",
        purpose="Demonstrate exact-command authorization without execution.",
        issued_at=issued,
        expires_at=issued + timedelta(hours=1),
        invocation=invocation,
    )
    approvals = tuple(
        manager.approve(
            request,
            approver_id=f"reviewer-{index}",
            approved_at=issued + timedelta(minutes=index),
            reason="Synthetic independent approval.",
        )
        for index in (1, 2)
    )
    authorization = manager.authorize(request, approvals=approvals)
    preflight = manager.preflight(
        authorization,
        invocation,
        now=issued + timedelta(minutes=10),
    )

    print("request hash:", request.request_hash)
    print("authorization hash:", authorization.authorization_hash)
    print("approvers:", list(preflight.approver_ids))
    print("version output:", preflight.executable_version_output)
    print("ready:", preflight.ready)
    print("external command executed:", False)
