from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evoagent.execution import (
    ExecutionAdapter,
    ExecutionAuthorizationError,
    ExecutionAuthorizationManager,
    ExecutionBudget,
    ExecutionInvocation,
    ExecutionUseError,
    ExecutionUseStatus,
    SQLiteExecutionUseStore,
    preflight_hash,
)
from evoagent.integrations import HarborCLIAdapter
from evoagent.training import MLInternCLIAdapter, MLInternTaskSpec


def fake_cli(tmp_path: Path) -> Path:
    path = tmp_path / "fake-external-cli"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('0.16.1')\n"
        "elif '--help' in sys.argv:\n"
        "    print('Hugging Face Agent CLI')\n"
        "else:\n"
        "    print('synthetic external command completed')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def authorized(invocation, *, requester="requester", approvals=2):
    manager = ExecutionAuthorizationManager()
    issued = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    request = manager.prepare_request(
        request_id="request:synthetic:1",
        requester_id=requester,
        purpose="synthetic external execution test",
        issued_at=issued,
        expires_at=issued + timedelta(hours=1),
        invocation=invocation,
    )
    approval_items = tuple(
        manager.approve(
            request,
            approver_id=f"reviewer-{index}",
            approved_at=issued + timedelta(minutes=index),
            reason="synthetic independent approval",
        )
        for index in range(1, approvals + 1)
    )
    return (
        manager,
        issued,
        request,
        manager.authorize(request, approvals=approval_items),
    )


def base_invocation(tmp_path: Path, executable: Path) -> ExecutionInvocation:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ExecutionInvocation(
        adapter=ExecutionAdapter.HARBOR,
        command=(str(executable), "run", "--synthetic"),
        workspace=str(workspace.resolve()),
        required_environment_variables=("MODEL_API_KEY",),
        network_access=True,
        budget=ExecutionBudget(max_cost_usd=1, max_wall_seconds=30, max_trials=1),
        version_arguments=("--version",),
        expected_version_pattern=r"^0\.16\.1$",
    )


def test_authorization_requires_distinct_non_requester_approvers(tmp_path):
    invocation = base_invocation(tmp_path, fake_cli(tmp_path))
    manager = ExecutionAuthorizationManager()
    issued = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    request = manager.prepare_request(
        request_id="request:approval:1",
        requester_id="requester",
        purpose="networked test",
        issued_at=issued,
        expires_at=issued + timedelta(hours=1),
        invocation=invocation,
    )
    one = manager.approve(
        request,
        approver_id="reviewer-a",
        approved_at=issued + timedelta(minutes=1),
        reason="reviewed",
    )
    with pytest.raises(ExecutionAuthorizationError, match="at least 2"):
        manager.authorize(request, approvals=(one,))

    duplicate = manager.approve(
        request,
        approver_id="reviewer-a",
        approved_at=issued + timedelta(minutes=2),
        reason="duplicate identity",
    )
    with pytest.raises(ExecutionAuthorizationError, match="distinct"):
        manager.authorize(request, approvals=(one, duplicate))

    self_approval = manager.approve(
        request,
        approver_id="requester",
        approved_at=issued + timedelta(minutes=2),
        reason="self approval",
    )
    with pytest.raises(ExecutionAuthorizationError, match="self-approve"):
        manager.authorize(request, approvals=(one, self_approval))


def test_preflight_binds_exact_invocation_expiry_credentials_and_budget(tmp_path):
    invocation = base_invocation(tmp_path, fake_cli(tmp_path))
    manager, issued, _, authorization = authorized(invocation)

    with pytest.raises(ExecutionAuthorizationError, match="expired"):
        manager.preflight(
            authorization,
            invocation,
            environment={"MODEL_API_KEY": "not-returned"},
            now=issued + timedelta(hours=1),
        )
    with pytest.raises(ExecutionAuthorizationError, match="Missing"):
        manager.preflight(
            authorization,
            invocation,
            environment={},
            now=issued + timedelta(minutes=10),
        )

    changed_command = invocation.model_copy(
        update={"command": (*invocation.command, "--changed")}
    )
    with pytest.raises(ExecutionAuthorizationError, match="differs"):
        manager.preflight(
            authorization,
            changed_command,
            environment={"MODEL_API_KEY": "not-returned"},
            now=issued + timedelta(minutes=10),
        )

    changed_budget = invocation.model_copy(
        update={"budget": invocation.budget.model_copy(update={"max_cost_usd": 2})}
    )
    with pytest.raises(ExecutionAuthorizationError, match="differs"):
        manager.preflight(
            authorization,
            changed_budget,
            environment={"MODEL_API_KEY": "not-returned"},
            now=issued + timedelta(minutes=10),
        )

    result = manager.preflight(
        authorization,
        invocation,
        environment={"MODEL_API_KEY": "not-returned"},
        now=issued + timedelta(minutes=10),
    )
    assert result.environment_presence == {"MODEL_API_KEY": True}
    assert "not-returned" not in result.model_dump_json()
    assert result.required_approvals == 2
    assert result.checked_at == issued + timedelta(minutes=10)
    assert result.fresh_until == issued + timedelta(minutes=15)
    assert result.preflight_hash == preflight_hash(result)


def test_request_and_authorization_hash_tampering_and_command_secrets_are_rejected(
    tmp_path,
):
    invocation = base_invocation(tmp_path, fake_cli(tmp_path))
    manager, _, request, authorization = authorized(invocation)

    with pytest.raises(ExecutionAuthorizationError, match="request hash"):
        manager.verify_request(
            request.model_copy(update={"purpose": "tampered purpose"})
        )
    with pytest.raises(ExecutionAuthorizationError, match="authorization hash"):
        manager.verify_authorization(
            authorization.model_copy(
                update={"approvals": tuple(reversed(authorization.approvals))}
            )
        )

    synthetic_secret = "API_KEY=sk-abcdefghijklmnop"  # synthetic-secret-fixture
    secret_invocation = invocation.model_copy(
        update={"command": (invocation.command[0], synthetic_secret)}
    )
    with pytest.raises(ExecutionAuthorizationError, match="potential secret"):
        manager.prepare_request(
            request_id="request:secret:1",
            requester_id="requester",
            purpose="must fail",
            issued_at=request.issued_at,
            expires_at=request.expires_at,
            invocation=secret_invocation,
        )


def test_transactional_one_use_ledger_rejects_replay(tmp_path):
    invocation = base_invocation(tmp_path, fake_cli(tmp_path))
    manager, issued, _, authorization = authorized(invocation)
    preflight = manager.preflight(
        authorization,
        invocation,
        environment={"MODEL_API_KEY": "value"},
        now=issued + timedelta(minutes=10),
    )
    store = SQLiteExecutionUseStore(tmp_path / "execution-uses.db")
    first = store.claim(authorization, preflight, now=issued + timedelta(minutes=10))
    assert first.status == ExecutionUseStatus.CLAIMED
    assert first.preflight_hash == preflight.preflight_hash
    with pytest.raises(ExecutionUseError, match="already used"):
        store.claim(authorization, preflight, now=issued + timedelta(minutes=11))
    completed = store.complete(
        authorization.authorization_hash,
        return_code=0,
        now=issued + timedelta(minutes=12),
    )
    assert completed.status == ExecutionUseStatus.COMPLETED


def test_transactional_claim_revalidates_freshness_authorization_and_command(tmp_path):
    invocation = base_invocation(tmp_path, fake_cli(tmp_path))
    manager, issued, _, authorization = authorized(invocation)
    preflight = manager.preflight(
        authorization,
        invocation,
        environment={"MODEL_API_KEY": "value"},
        now=issued + timedelta(minutes=10),
    )

    with pytest.raises(ExecutionUseError, match="stale"):
        SQLiteExecutionUseStore(tmp_path / "stale.db").claim(
            authorization,
            preflight,
            now=issued + timedelta(minutes=15),
        )

    tampered_authorization = authorization.model_copy(
        update={
            "request": authorization.request.model_copy(
                update={"purpose": "tampered after approval"}
            )
        }
    )
    with pytest.raises(ExecutionUseError, match="invalid at claim time"):
        SQLiteExecutionUseStore(tmp_path / "authorization.db").claim(
            tampered_authorization,
            preflight,
            now=issued + timedelta(minutes=11),
        )

    changed_command = preflight.model_copy(update={"command_hash": "a" * 64})
    changed_command = changed_command.model_copy(
        update={"preflight_hash": preflight_hash(changed_command)}
    )
    with pytest.raises(ExecutionUseError, match="command hash"):
        SQLiteExecutionUseStore(tmp_path / "command.db").claim(
            authorization,
            changed_command,
            now=issued + timedelta(minutes=11),
        )

    other_root = tmp_path / "other"
    other_root.mkdir()
    other_executable = fake_cli(other_root)
    changed_executable = preflight.model_copy(
        update={"executable_path": str(other_executable.resolve())}
    )
    changed_executable = changed_executable.model_copy(
        update={"preflight_hash": preflight_hash(changed_executable)}
    )
    with pytest.raises(ExecutionUseError, match="executable"):
        SQLiteExecutionUseStore(tmp_path / "executable.db").claim(
            authorization,
            changed_executable,
            now=issued + timedelta(minutes=11),
        )

    invalid_time_chain = preflight.model_copy(
        update={
            "checked_at": issued - timedelta(minutes=1),
            "fresh_until": issued + timedelta(minutes=1),
        }
    )
    invalid_time_chain = invalid_time_chain.model_copy(
        update={"preflight_hash": preflight_hash(invalid_time_chain)}
    )
    with pytest.raises(ExecutionUseError, match="time chain"):
        SQLiteExecutionUseStore(tmp_path / "time-chain.db").claim(
            authorization,
            invalid_time_chain,
            now=issued + timedelta(seconds=30),
        )


def test_execution_use_store_migrates_legacy_receipts(tmp_path):
    database = tmp_path / "legacy-uses.db"
    authorization_digest = "b" * 64
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE execution_uses (
                authorization_hash TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                command_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                return_code INTEGER
            )
            """
        )
        connection.execute(
            "INSERT INTO execution_uses VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                authorization_digest,
                "request:legacy:1",
                "c" * 64,
                ExecutionUseStatus.COMPLETED.value,
                "2026-08-10T01:00:00+00:00",
                "2026-08-10T01:01:00+00:00",
                0,
            ),
        )

    receipt = SQLiteExecutionUseStore(database).get(authorization_digest)

    assert receipt.preflight_hash is None
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(execution_uses)")
        }
    assert "preflight_hash" in columns


def test_openrouter_execution_adapter_has_fixed_side_effect_free_probe(tmp_path):
    workspace = tmp_path / "openrouter-workspace"
    workspace.mkdir()
    invocation = ExecutionInvocation(
        adapter=ExecutionAdapter.OPENROUTER,
        command=("python", "scripts/run_openrouter_minimal_scientific_seed.py"),
        workspace=str(workspace.resolve()),
        required_environment_variables=("OPENROUTER_API_KEY",),
        network_access=True,
        budget=ExecutionBudget(max_cost_usd=1.2, max_wall_seconds=5400),
        version_arguments=("--version",),
        expected_version_pattern=r"Python 3\.",
    )

    assert invocation.adapter is ExecutionAdapter.OPENROUTER
    with pytest.raises(ValueError, match="fixed and side-effect-free"):
        ExecutionInvocation.model_validate(
            invocation.model_dump() | {"version_arguments": ("scripts/probe.py",)}
        )


def test_harbor_execute_requires_authorization_and_consumes_it_once(tmp_path):
    executable = fake_cli(tmp_path)
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
    with pytest.raises(PermissionError, match="external authorization"):
        adapter.execute(spec, environment={"MODEL_API_KEY": "secret-value"})

    invocation = adapter.to_execution_invocation(spec)
    manager, issued, _, authorization = authorized(invocation)
    store = SQLiteExecutionUseStore(tmp_path / "harbor-uses.db")
    completed = adapter.execute(
        spec,
        authorization=authorization,
        authorization_manager=manager,
        use_store=store,
        environment={"MODEL_API_KEY": "secret-value"},
        now=issued + timedelta(minutes=10),
    )
    assert completed.returncode == 0
    receipt = store.get(authorization.authorization_hash)
    assert receipt.status == ExecutionUseStatus.COMPLETED
    assert "secret-value" not in receipt.model_dump_json()

    with pytest.raises(ExecutionUseError, match="already used"):
        adapter.execute(
            spec,
            authorization=authorization,
            authorization_manager=manager,
            use_store=store,
            environment={"MODEL_API_KEY": "secret-value"},
            now=issued + timedelta(minutes=11),
        )


def test_ml_intern_execute_requires_authorization_and_never_serializes_token(tmp_path):
    executable = fake_cli(tmp_path)
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
            "synthetic candidate experiment",
        ),
        prompt="synthetic candidate experiment",
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
    with pytest.raises(PermissionError, match="external authorization"):
        adapter.execute(spec, budget=budget, environment={"HF_TOKEN": "secret-token"})

    invocation = adapter.to_execution_invocation(spec, budget=budget)
    manager, issued, _, authorization = authorized(invocation)
    store = SQLiteExecutionUseStore(tmp_path / "ml-uses.db")
    completed = adapter.execute(
        spec,
        budget=budget,
        authorization=authorization,
        authorization_manager=manager,
        use_store=store,
        environment={"HF_TOKEN": "secret-token"},
        now=issued + timedelta(minutes=10),
    )
    assert completed.returncode == 0
    config = json.loads(
        (workspace / ".evoagent" / "ml-intern-config.json").read_text(encoding="utf-8")
    )
    assert config == {"share_traces": False, "tool_runtime": "sandbox"}
    assert "secret-token" not in json.dumps(config)
    assert (
        store.get(authorization.authorization_hash).status
        == ExecutionUseStatus.COMPLETED
    )
