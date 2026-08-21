from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent.execution import (
    ExecutionAdapter,
    ExecutionAuthorizationError,
    ExecutionAuthorizationManager,
    ExecutionBudget,
    ExecutionInvocation,
)


def invocation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return ExecutionInvocation(
        adapter=ExecutionAdapter.ML_INTERN,
        command=("ml-intern", "--sandbox-tools", "synthetic task"),
        workspace=str(workspace.resolve()),
        required_environment_variables=("HF_TOKEN",),
        network_access=True,
        training=True,
        budget=ExecutionBudget(
            max_cost_usd=1,
            max_gpu_hours=1,
            max_wall_seconds=30,
            max_iterations=3,
        ),
        version_arguments=("--help",),
        expected_version_pattern="Hugging Face Agent CLI",
    )


def test_execution_request_validity_window_is_bounded(tmp_path):
    manager = ExecutionAuthorizationManager()
    issued = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="24 hours"):
        manager.prepare_request(
            request_id="request:too-long",
            requester_id="requester",
            purpose="synthetic test",
            issued_at=issued,
            expires_at=issued + timedelta(hours=25),
            invocation=invocation(tmp_path),
        )


def test_request_purpose_and_approval_reason_cannot_store_secrets(tmp_path):
    manager = ExecutionAuthorizationManager()
    issued = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)
    with pytest.raises(ExecutionAuthorizationError, match="potential secret"):
        manager.prepare_request(
            request_id="request:secret-purpose",
            requester_id="requester",
            purpose="Use sk-abcdefghijklmnop for this task",
            issued_at=issued,
            expires_at=issued + timedelta(hours=1),
            invocation=invocation(tmp_path),
        )

    request = manager.prepare_request(
        request_id="request:secret-reason",
        requester_id="requester",
        purpose="synthetic test",
        issued_at=issued,
        expires_at=issued + timedelta(hours=1),
        invocation=invocation(tmp_path),
    )
    clean = manager.approve(
        request,
        approver_id="reviewer-a",
        approved_at=issued + timedelta(minutes=1),
        reason="reviewed",
    )
    secret = manager.approve(
        request,
        approver_id="reviewer-b",
        approved_at=issued + timedelta(minutes=2),
        reason="token=hf_abcdefghijklmnopqrstuv",  # synthetic-secret-fixture
    )
    with pytest.raises(ExecutionAuthorizationError, match="potential secret"):
        manager.authorize(request, approvals=(clean, secret))
