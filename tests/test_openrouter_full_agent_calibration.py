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
    OpenRouterIntegrationError,
    OpenRouterModelPreset,
    OpenRouterUsageLedger,
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


def _required_mimo_preset() -> OpenRouterModelPreset:
    root = Path(__file__).resolve().parents[1]
    return OpenRouterModelPreset.model_validate_json(
        (
            root / "configs/full_agent/openrouter-mimo-v2.5-xiaomi-required.json"
        ).read_text(encoding="utf-8")
    )


def _matching_transport(
    *, provider="Xiaomi", mutate_arguments=False, cost=0.0001, content=None
):
    def transport(payload, api_key, timeout_seconds):
        assert api_key == "test-only-key"
        assert 0 < timeout_seconds <= 90
        requested = json.loads(payload["messages"][1]["content"])
        arguments = dict(requested["required_arguments"])
        if mutate_arguments:
            arguments["path"] = "wrong.txt"
        return {
            "model": "xiaomi/mimo-v2.5-20260422",
            "provider": provider,
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": content,
                        "tool_calls": [
                            {
                                "id": "call_frozen_action",
                                "type": "function",
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
    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot", model_id=_preset().model_id
    )
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

    def transport(payload, api_key, timeout_seconds):
        assert api_key == "test-only-key"
        assert 0 < timeout_seconds <= 90
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

    def capture(payload, api_key, timeout_seconds):
        payloads.append(payload)
        return transport(payload, api_key, timeout_seconds)

    _, trace, _ = _run(tmp_path, capture)

    assert trace.verifier_passed is True
    assert all("reasoning" not in payload for payload in payloads)


def test_required_single_tool_mode_exposes_one_tool_and_never_uses_auto(
    tmp_path: Path,
):
    preset = _required_mimo_preset()
    assert preset.endpoint_tag == "xiaomi/fp8"
    assert preset.context_length == 1_048_576
    payloads = []
    transport = _matching_transport(content="")

    def capture(payload, api_key, timeout_seconds):
        payloads.append(payload)
        response = transport(payload, api_key, timeout_seconds)
        response["choices"][0]["message"].update(
            {
                "reasoning": None,
                "reasoning_content": "",
                "reasoning_details": [],
            }
        )
        return response

    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )
    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
        transport=capture,
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
    assert all(payload["tool_choice"] == "required" for payload in payloads)
    assert all(len(payload["tools"]) == 1 for payload in payloads)
    assert all(
        payload["provider"]
        == {
            "only": ["xiaomi/fp8"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        for payload in payloads
    )
    assert all(
        payload["tools"][0]["function"]["name"]
        == json.loads(payload["messages"][1]["content"])["required_tool"]
        for payload in payloads
    )


def test_required_single_tool_mode_requires_capability_timestamp():
    payload = _preset().model_dump(mode="json")
    payload["tool_choice_mode"] = "required_single_tool"
    payload["tool_choice_verified_at"] = None

    with pytest.raises(ValueError, match="capability verification time"):
        OpenRouterModelPreset.model_validate(payload)


def test_legacy_mode_rejects_unbound_capability_timestamp():
    payload = _preset().model_dump(mode="json")
    payload["tool_choice_mode"] = None
    payload["tool_choice_verified_at"] = "2026-08-28T02:22:53+00:00"

    with pytest.raises(ValueError, match="capability verification time"):
        OpenRouterModelPreset.model_validate(payload)


def test_explicit_tool_choice_mode_requires_exact_endpoint_tag():
    payload = _preset().model_dump(mode="json")
    payload["tool_choice_mode"] = "required_single_tool"
    payload["tool_choice_verified_at"] = "2026-08-28T02:22:53+00:00"
    payload["endpoint_tag"] = None

    with pytest.raises(ValueError, match="exact provider endpoint"):
        OpenRouterModelPreset.model_validate(payload)


def test_exact_endpoint_tag_must_belong_to_provider_slug():
    payload = _required_mimo_preset().model_dump(mode="json")
    payload["endpoint_tag"] = "another-provider/fp8"

    with pytest.raises(ValueError, match="does not belong"):
        OpenRouterModelPreset.model_validate(payload)


def test_legacy_named_function_preset_retains_historical_fingerprint_shape():
    preset = _preset()
    round_tripped = OpenRouterModelPreset.model_validate_json(preset.model_dump_json())

    assert preset.tool_choice_mode is None
    assert "tool_choice_mode" not in preset.fingerprint_payload()
    assert round_tripped.fingerprint_payload() == preset.fingerprint_payload()
    assert _required_mimo_preset().fingerprint_payload()["tool_choice_mode"] == (
        "required_single_tool"
    )


def test_tool_choice_schema_rejects_auto_mode():
    payload = _required_mimo_preset().model_dump(mode="json")
    payload["tool_choice_mode"] = "auto"

    with pytest.raises(ValueError):
        OpenRouterModelPreset.model_validate(payload)


@pytest.mark.parametrize("tool_call_count", [0, 2])
def test_required_single_tool_response_still_requires_exactly_one_call(
    tool_call_count: int,
):
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_frozen_action",
                            "type": "function",
                            "function": {
                                "name": "read_document",
                                "arguments": '{"path":"note.txt"}',
                            }
                        }
                        for _ in range(tool_call_count)
                    ]
                }
            }
        ]
    }

    with pytest.raises(OpenRouterIntegrationError, match="one Tool call"):
        OpenRouterControlledToolPolicy._one_tool_call(response)


@pytest.mark.parametrize(
    "raw_arguments",
    [
        '{"path":"attacker-value","path":"note.txt"}',
        '{"path":NaN}',
        '{"path":Infinity}',
        '{"nested":{"value":1,"value":2}}',
    ],
)
def test_required_single_tool_arguments_reject_ambiguous_or_non_finite_json(
    raw_arguments: str,
):
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_document",
                                "arguments": raw_arguments,
                            }
                        }
                    ]
                }
            }
        ]
    }

    with pytest.raises(OpenRouterIntegrationError, match="unambiguous finite JSON"):
        OpenRouterControlledToolPolicy._one_tool_call(response)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ({"finish_reason": "stop"}, "did not finish"),
        ({"content": "unexpected prose"}, "unexpected prose"),
        ({"reasoning": "private reasoning"}, "unexpected reasoning"),
        ({"reasoning_content": "private reasoning"}, "unexpected reasoning"),
        (
            {"reasoning_details": [{"type": "reasoning.text"}]},
            "unexpected reasoning",
        ),
        ({"tool_call_id": ""}, "identity is invalid"),
        ({"tool_call_type": "custom"}, "identity is invalid"),
    ],
)
def test_required_single_tool_shape_fails_closed_on_incomplete_response(
    mutation: dict[str, object], error: str
):
    choice = {
        "finish_reason": mutation.get("finish_reason", "tool_calls"),
        "message": {
            "content": mutation.get("content"),
            "tool_calls": [
                {
                    "id": mutation.get("tool_call_id", "call_frozen_action"),
                    "type": mutation.get("tool_call_type", "function"),
                    "function": {
                        "name": "read_document",
                        "arguments": '{"path":"note.txt"}',
                    },
                }
            ],
        },
    }
    for key in ("reasoning", "reasoning_content", "reasoning_details"):
        if key in mutation:
            choice["message"][key] = mutation[key]

    with pytest.raises(OpenRouterIntegrationError, match=error):
        OpenRouterControlledToolPolicy._verify_required_single_tool_shape(
            {"choices": [choice]}
        )


@pytest.mark.parametrize("content", [None, ""])
def test_required_single_tool_shape_accepts_no_prose_content(
    content: str | None,
):
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": content,
                    "tool_calls": [
                        {
                            "id": "call_frozen_action",
                            "type": "function",
                            "function": {
                                "name": "read_document",
                                "arguments": '{"path":"note.txt"}',
                            },
                        }
                    ],
                },
            }
        ]
    }

    OpenRouterControlledToolPolicy._verify_required_single_tool_shape(response)


@pytest.mark.parametrize(
    "reasoning_fields",
    [
        {},
        {"reasoning": None},
        {"reasoning": ""},
        {"reasoning_content": None},
        {"reasoning_content": ""},
        {"reasoning_details": None},
        {"reasoning_details": []},
        {
            "reasoning": None,
            "reasoning_content": "",
            "reasoning_details": [],
        },
    ],
)
def test_required_single_tool_shape_accepts_empty_reasoning_placeholders(
    reasoning_fields: dict[str, object],
):
    message = {
        "content": None,
        "tool_calls": [
            {
                "id": "call_frozen_action",
                "type": "function",
                "function": {
                    "name": "read_document",
                    "arguments": '{"path":"note.txt"}',
                },
            }
        ],
        **reasoning_fields,
    }
    response = {
        "choices": [{"finish_reason": "tool_calls", "message": message}]
    }

    OpenRouterControlledToolPolicy._verify_required_single_tool_shape(response)


@pytest.mark.parametrize(
    "reasoning_fields",
    [
        {"reasoning": " "},
        {"reasoning": False},
        {"reasoning": []},
        {"reasoning_content": "\n"},
        {"reasoning_content": {}},
        {"reasoning_details": ""},
        {"reasoning_details": {}},
        {"reasoning_details": [{"type": "reasoning.text"}]},
    ],
)
def test_required_single_tool_shape_rejects_nonempty_or_invalid_reasoning(
    reasoning_fields: dict[str, object],
):
    message = {
        "content": None,
        "tool_calls": [
            {
                "id": "call_frozen_action",
                "type": "function",
                "function": {
                    "name": "read_document",
                    "arguments": '{"path":"note.txt"}',
                },
            }
        ],
        **reasoning_fields,
    }
    response = {
        "choices": [{"finish_reason": "tool_calls", "message": message}]
    }

    with pytest.raises(OpenRouterIntegrationError, match="unexpected reasoning"):
        OpenRouterControlledToolPolicy._verify_required_single_tool_shape(response)


@pytest.mark.parametrize("content", [" ", "\n", [], {}, 0, False])
def test_required_single_tool_shape_rejects_any_other_content(
    content: object,
):
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": content,
                    "tool_calls": [
                        {
                            "id": "call_frozen_action",
                            "type": "function",
                            "function": {
                                "name": "read_document",
                                "arguments": '{"path":"note.txt"}',
                            },
                        }
                    ],
                },
            }
        ]
    }

    with pytest.raises(OpenRouterIntegrationError, match="unexpected prose"):
        OpenRouterControlledToolPolicy._verify_required_single_tool_shape(response)


def test_required_single_tool_transport_failure_does_not_retry_or_fallback(
    tmp_path: Path,
):
    attempts = 0
    preset = _required_mimo_preset()
    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )

    def reject(_payload, _api_key, _timeout_seconds):
        nonlocal attempts
        attempts += 1
        raise OpenRouterIntegrationError("synthetic route rejection")

    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
        transport=reject,
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(build_calibration_task(), to_runtime_snapshot(snapshot))

    assert attempts == 1
    assert policy.usage.requests == 1
    assert trace.verifier_passed is False
    assert trace.final_output["error_type"] == "OpenRouterIntegrationError"


def test_global_deadline_stops_policy_before_network(tmp_path: Path):
    attempts = 0
    preset = _required_mimo_preset()
    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )

    def transport(_payload, _api_key, _timeout_seconds):
        nonlocal attempts
        attempts += 1
        return {}

    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
        transport=transport,
        deadline_monotonic=5.0,
        monotonic=lambda: 5.0,
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(build_calibration_task(), to_runtime_snapshot(snapshot))

    assert attempts == 0
    assert policy.usage.requests == 0
    assert trace.final_output["error_type"] == "OpenRouterIntegrationError"


def test_policy_rejects_actual_completion_over_requested_cap(tmp_path: Path):
    preset = _required_mimo_preset()
    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )
    matching_transport = _matching_transport()

    def overlong_transport(payload, api_key, timeout_seconds):
        response = matching_transport(payload, api_key, timeout_seconds)
        response["usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": 9,
            "total_tokens": 109,
            "cost": 0.0001,
        }
        return response

    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
        max_output_tokens=8,
        transport=overlong_transport,
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(build_calibration_task(), to_runtime_snapshot(snapshot))

    assert policy.usage.requests == 1
    assert policy.usage.completion_tokens == 9
    assert policy.usage.total_tokens == 109
    assert policy.usage.cost_usd == pytest.approx(0.0001)
    assert trace.verifier_passed is False
    assert trace.final_output["error_type"] == "OpenRouterIntegrationError"


def test_policy_and_shared_ledger_record_billed_cost_before_cap_failure(
    tmp_path: Path,
):
    preset = _required_mimo_preset()
    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )
    ledger = OpenRouterUsageLedger(
        preset=preset,
        max_requests=3,
        max_prompt_bytes_per_request=4_096,
        max_output_tokens_per_request=256,
        max_cost_usd=0.01,
    )
    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
        max_prompt_bytes_per_request=4_096,
        max_cost_usd=0.01,
        shared_ledger=ledger,
        transport=_matching_transport(cost=0.02),
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(build_calibration_task(), to_runtime_snapshot(snapshot))

    assert policy.usage.requests == 1
    assert policy.usage.total_tokens == 110
    assert policy.usage.cost_usd == pytest.approx(0.02)
    assert ledger.usage.requests == 1
    assert ledger.usage.total_tokens == 110
    assert ledger.usage.cost_usd == pytest.approx(0.02)
    assert trace.verifier_passed is False
    assert trace.final_output["error_type"] == "OpenRouterIntegrationError"


def test_global_deadline_is_rechecked_after_network(tmp_path: Path):
    attempts = 0
    clock_values = iter((0.0, 0.0, 0.0, 6.0))
    preset = _required_mimo_preset()
    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )
    ledger = OpenRouterUsageLedger(
        preset=preset,
        max_requests=3,
        max_prompt_bytes_per_request=32_768,
        max_output_tokens_per_request=256,
        max_cost_usd=2.0,
    )

    def transport(payload, api_key, timeout_seconds):
        nonlocal attempts
        attempts += 1
        assert 0 < timeout_seconds <= 5.0
        return _matching_transport()(payload, api_key, timeout_seconds)

    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
        transport=transport,
        shared_ledger=ledger,
        deadline_monotonic=5.0,
        monotonic=lambda: next(clock_values),
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(build_calibration_task(), to_runtime_snapshot(snapshot))

    assert attempts == 1
    assert policy.usage.requests == 1
    assert policy.usage.total_tokens == 110
    assert policy.usage.cost_usd == pytest.approx(0.0001)
    assert ledger.usage.requests == 1
    assert ledger.usage.total_tokens == 110
    assert ledger.usage.cost_usd == pytest.approx(0.0001)
    assert trace.final_output["error_type"] == "OpenRouterIntegrationError"


def test_default_transport_timeout_consumes_attempt_and_never_retries(
    tmp_path: Path,
    monkeypatch,
):
    attempts = 0
    preset = _required_mimo_preset()
    snapshot = build_calibration_snapshot(
        tmp_path / "snapshot",
        model_id=preset.model_id,
    )

    def timeout(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout <= 90
        raise TimeoutError("synthetic timeout")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    policy = OpenRouterControlledToolPolicy(
        controller=UnifiedDocumentPolicy(snapshot),
        preset=preset,
        api_key="test-only-key",
    )
    trace = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(tmp_path / "environment"),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=30),
        seed=43,
    ).run(build_calibration_task(), to_runtime_snapshot(snapshot))

    assert attempts == 1
    assert policy.usage.requests == 1
    assert trace.final_output["error_type"] == "OpenRouterIntegrationError"


@pytest.mark.parametrize(
    "transport",
    [
        _matching_transport(provider="AnotherProvider"),
        _matching_transport(mutate_arguments=True),
    ],
)
def test_mimo_policy_fails_closed_on_provider_or_action_drift(
    tmp_path: Path, transport
):
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
