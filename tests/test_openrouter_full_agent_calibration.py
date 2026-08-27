import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from evoagent.continual.builders import to_runtime_snapshot
from evoagent.continual.runtime import ContinualDocumentVerifier, UnifiedDocumentPolicy
from evoagent.integrations.full_agent_calibration import (
    build_calibration_snapshot,
    build_calibration_task,
)
from evoagent.integrations.openrouter import (
    OpenRouterControlledToolPolicy,
    OpenRouterModelPreset,
)
from evoagent.runtime import LocalDocumentEnvironment, RuntimeLimits, ToolAgentRuntime
from scripts.run_mimo_full_agent_calibration import _authorization


def _preset() -> OpenRouterModelPreset:
    return OpenRouterModelPreset(
        preset_id="openrouter-mimo-v2.5-xiaomi-v1",
        model_id="xiaomi/mimo-v2.5",
        canonical_model_id="xiaomi/mimo-v2.5-20260422",
        provider_slug="xiaomi",
        provider_name="Xiaomi",
        prompt_cost_per_token_usd="0.00000014",
        completion_cost_per_token_usd="0.00000028",
        context_length=1_050_000,
        max_completion_tokens=131_072,
        supports_tools=True,
        catalogue_verified_at="2026-08-26T12:14:22+00:00",
    )


def _qwen_preset() -> OpenRouterModelPreset:
    root = Path(__file__).resolve().parents[1]
    return OpenRouterModelPreset.model_validate_json(
        (root / "configs/full_agent/openrouter-qwen3.8-flash-alibaba.json").read_text(
            encoding="utf-8"
        )
    )


def _matching_transport(*, provider="Xiaomi", mutate_arguments=False, cost=0.0001):
    def transport(payload, api_key):
        assert api_key == "test-only-key"
        requested = json.loads(payload["messages"][1]["content"])
        arguments = dict(requested["required_arguments"])
        if mutate_arguments:
            arguments["path"] = "wrong.txt"
        return {
            "model": "xiaomi/mimo-v2.5-20260422",
            "provider": provider,
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": requested["required_tool"],
                                    "arguments": json.dumps(arguments),
                                }
                            }
                        ]
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cost": cost,
            },
        }

    return transport


def _run(tmp_path: Path, transport):
    snapshot = build_calibration_snapshot(tmp_path / "snapshot", model_id=_preset().model_id)
    task = build_calibration_task()
    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=_preset(),
        api_key="test-only-key",
        transport=transport,
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(task, to_runtime_snapshot(snapshot))
    return snapshot, trace, policy


def test_mimo_policy_runs_snapshot_controlled_read_write_verify_loop(tmp_path: Path):
    snapshot, trace, policy = _run(tmp_path, _matching_transport())

    assert trace.verifier_passed is True
    assert policy.usage.requests == 3
    assert policy.usage.total_tokens == 330
    assert policy.usage.cost_usd == pytest.approx(0.0003)
    assert trace.cost["llm_tokens"] == 330.0
    assert trace.cost["llm_requests"] == 3.0
    assert trace.cost["cost_usd"] == pytest.approx(0.0003)
    observations = [
        item["metadata"]
        for item in trace.observable_events
        if item.get("event") == "policy_observation"
    ]
    assert observations[0]["router_source"] == "verified_memory"
    assert observations[0]["memory_record_ids"] == ("memory-write-verify-v1",)
    assert observations[0]["selected_skill_ids"] == ("document_writer",)
    assert observations[0]["policy_state"] == "adversarial"
    assert observations[0]["initial_policy_action"] == "inspect"
    assert observations[0]["snapshot_hash"] == snapshot.snapshot_hash


def test_qwen_policy_disables_reasoning_and_requires_exact_provider_parameters(
    tmp_path: Path,
):
    preset = _qwen_preset()
    payloads = []

    def transport(payload, api_key):
        assert api_key == "test-only-key"
        payloads.append(payload)
        requested = json.loads(payload["messages"][1]["content"])
        return {
            "model": preset.canonical_model_id,
            "provider": preset.provider_name,
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": requested["required_tool"],
                                    "arguments": json.dumps(
                                        requested["required_arguments"]
                                    ),
                                }
                            }
                        ]
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cost": 0.0001,
            },
        }

    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )
    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
        transport=transport,
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(build_calibration_task(), to_runtime_snapshot(snapshot))

    assert trace.verifier_passed is True
    assert payloads
    assert all(payload["reasoning"] == {"enabled": False} for payload in payloads)
    assert all(
        payload["provider"]
        == {
            "only": ["alibaba"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        for payload in payloads
    )


def test_mimo_preset_omits_unfrozen_reasoning_setting(tmp_path: Path):
    payloads = []
    transport = _matching_transport()

    def capture(payload, api_key):
        payloads.append(payload)
        return transport(payload, api_key)

    _, trace, _ = _run(tmp_path, capture)

    assert trace.verifier_passed is True
    assert all("reasoning" not in payload for payload in payloads)


@pytest.mark.parametrize(
    "transport",
    [
        _matching_transport(provider="AnotherProvider"),
        _matching_transport(mutate_arguments=True),
    ],
)
def test_mimo_policy_fails_closed_on_provider_or_action_drift(tmp_path: Path, transport):
    _, trace, _ = _run(tmp_path, transport)
    assert trace.verifier_passed is False
    assert any(
        item.get("event") == "runtime_error"
        and item.get("error_type") == "OpenRouterIntegrationError"
        for item in trace.observable_events
    )


def test_mimo_policy_rejects_mathematical_over_budget_plan():
    with pytest.raises(ValueError, match="mathematical request ceiling"):
        OpenRouterControlledToolPolicy(
            controller=object(),
            preset=_preset(),
            api_key="test-only-key",
            max_requests=1_000_000,
            max_output_tokens=131_072,
            max_prompt_bytes_per_request=1_050_000,
            max_cost_usd=2.0,
            transport=_matching_transport(),
        )


def test_mimo_preset_and_public_workflow_are_exact_and_credential_free():
    root = Path(__file__).resolve().parents[1]
    preset = OpenRouterModelPreset.model_validate_json(
        (root / "configs/full_agent/openrouter-mimo-v2.5-xiaomi.json").read_text(
            encoding="utf-8"
        )
    )
    workflow = (root / ".github/workflows/full-agent-external-dry-run.yml").read_text(
        encoding="utf-8"
    )
    assert preset.model_id == "xiaomi/mimo-v2.5"
    assert preset.canonical_model_id == "xiaomi/mimo-v2.5-20260422"
    assert preset.provider_slug == "xiaomi"
    assert preset.prompt_cost_per_token_usd == Decimal("0.00000014")
    assert preset.completion_cost_per_token_usd == Decimal("0.00000028")
    assert "OPENROUTER_API_KEY" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "ubuntu-latest" in workflow


def test_qwen_preset_is_exact_current_endpoint_and_credential_free():
    preset = _qwen_preset()

    assert preset.model_id == "qwen/qwen3.8-flash"
    assert preset.canonical_model_id == "qwen/qwen3.8-flash-20260826"
    assert preset.provider_slug == "alibaba"
    assert preset.provider_name == "Alibaba"
    assert preset.prompt_cost_per_token_usd == Decimal("0.00000016")
    assert preset.completion_cost_per_token_usd == Decimal("0.00000047")
    assert preset.reasoning_enabled is False
    assert "OPENROUTER_API_KEY" not in (
        Path(__file__).resolve().parents[1]
        / "configs/full_agent/openrouter-qwen3.8-flash-alibaba.json"
    ).read_text(encoding="utf-8")


def test_real_calibration_requires_external_anchor_and_two_independent_approvers():
    base = {
        "requester_id": "codex-requester",
        "approver_id": ["owner", "budget-gate"],
        "authorization_anchor": "github-actions://repo/run/1",
    }
    assert _authorization(SimpleNamespace(**base))["approver_ids"] == (
        "owner",
        "budget-gate",
    )
    with pytest.raises(PermissionError, match="exactly two"):
        _authorization(SimpleNamespace(**{**base, "approver_id": ["owner"]}))
    with pytest.raises(PermissionError, match="self-approve"):
        _authorization(
            SimpleNamespace(
                **{**base, "approver_id": ["codex-requester", "budget-gate"]}
            )
        )
    with pytest.raises(PermissionError, match="externally anchored"):
        _authorization(SimpleNamespace(**{**base, "authorization_anchor": "local"}))
