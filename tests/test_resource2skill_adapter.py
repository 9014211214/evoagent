import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evoagent.execution import (
    ExecutionAuthorizationManager,
    ExecutionUseError,
    SQLiteExecutionUseStore,
)
from evoagent.integrations import RESOURCE2SKILL_REPOSITORY, Resource2SkillAdapter


def test_resource2skill_plan_uses_external_checkout_and_documented_paths(tmp_path):
    checkout = tmp_path / "resource2skill"
    spec = Resource2SkillAdapter(execution_enabled=False).build_spec(
        checkout_path=str(checkout),
        domain="public-demo",
    )

    assert spec.repository_url == RESOURCE2SKILL_REPOSITORY
    assert spec.validation_command == (
        "python",
        "cli.py",
        "validate-domain",
        "--domain",
        "public-demo",
    )
    assert Path(spec.skills_wiki_path) == checkout.resolve() / "skills_wiki" / "public-demo"
    assert Path(spec.skills_library_path) == checkout.resolve() / "skills_library" / "public-demo"
    assert spec.execution_enabled is False

    with pytest.raises(PermissionError):
        Resource2SkillAdapter(execution_enabled=False).execute_validation(spec)


@pytest.mark.parametrize("domain", ["../private", "/absolute/private", r"..\private"])
def test_resource2skill_domain_rejects_path_escape(tmp_path, domain):
    with pytest.raises(ValueError):
        Resource2SkillAdapter().build_spec(
            checkout_path=str(tmp_path),
            domain=domain,
        )


def test_resource2skill_enabled_execution_still_requires_authorization(tmp_path):
    checkout = tmp_path / "resource2skill"
    checkout.mkdir()
    (checkout / "cli.py").write_text("print('synthetic')\n", encoding="utf-8")
    adapter = Resource2SkillAdapter(
        python_binary=sys.executable,
        execution_enabled=True,
    )
    spec = adapter.build_spec(checkout_path=str(checkout), domain="public-demo")

    with pytest.raises(PermissionError, match="external authorization"):
        adapter.execute_validation(spec)


def test_resource2skill_execution_uses_minimal_environment_and_one_use_ledger(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "resource2skill"
    checkout.mkdir()
    (checkout / "cli.py").write_text(
        "import json, os\n"
        "print(json.dumps({'ambient_secret_present': bool(os.getenv('PRIVATE_TEST_SECRET'))}))\n",
        encoding="utf-8",
    )
    adapter = Resource2SkillAdapter(
        python_binary=sys.executable,
        execution_enabled=True,
    )
    spec = adapter.build_spec(checkout_path=str(checkout), domain="public-demo")
    invocation = adapter.to_execution_invocation(spec, timeout_seconds=30)
    manager = ExecutionAuthorizationManager()
    issued = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
    request = manager.prepare_request(
        request_id="request:resource2skill:1",
        requester_id="requester",
        purpose="validate a reviewed external Resource2Skill checkout",
        issued_at=issued,
        expires_at=issued + timedelta(hours=1),
        invocation=invocation,
    )
    approval = manager.approve(
        request,
        approver_id="reviewer",
        approved_at=issued + timedelta(minutes=1),
        reason="reviewed local validation",
    )
    authorization = manager.authorize(request, approvals=(approval,))
    store = SQLiteExecutionUseStore(tmp_path / "resource2skill-uses.db")
    monkeypatch.setenv("PRIVATE_TEST_SECRET", "must-not-be-inherited")

    completed = adapter.execute_validation(
        spec,
        timeout_seconds=30,
        authorization=authorization,
        authorization_manager=manager,
        use_store=store,
        now=issued + timedelta(minutes=2),
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"ambient_secret_present": False}
    with pytest.raises(ExecutionUseError, match="already used"):
        adapter.execute_validation(
            spec,
            timeout_seconds=30,
            authorization=authorization,
            authorization_manager=manager,
            use_store=store,
            now=issued + timedelta(minutes=3),
        )
