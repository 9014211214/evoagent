import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import import_skillevolbench_comparison as comparison
from scripts import prepare_harbor_openrouter
from scripts import run_skillevolbench_evoagent as launcher
from scripts import summarize_harbor_trajectory
from scripts import verify_openrouter_model


class _FakeBaseline:
    def __init__(self, *, harbor_agent_name="claude-code", agent_kwargs=None):
        self.harbor_agent_name = harbor_agent_name
        self.agent_kwargs = agent_kwargs or {}

    def model_copy(self, *, update):
        return _FakeBaseline(
            harbor_agent_name=self.harbor_agent_name,
            agent_kwargs=update.get("agent_kwargs", self.agent_kwargs),
        )


class _FakeConfig:
    def __init__(self, max_tasks=None, baseline=None):
        self.max_tasks = max_tasks
        self.baseline = baseline or _FakeBaseline()

    def model_copy(self, *, update):
        return _FakeConfig(
            max_tasks=update.get("max_tasks", self.max_tasks),
            baseline=update.get("baseline", self.baseline),
        )


def test_max_tasks_override_is_scoped_and_uses_upstream_fixture_knob(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_TURNS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", raising=False)
    original = lambda args: _FakeConfig()  # noqa: E731
    upstream = SimpleNamespace(_build_run_config=original)

    saved = launcher._install_run_config_overrides(upstream, max_tasks=1)

    assert saved is original
    assert upstream._build_run_config(SimpleNamespace()).max_tasks == 1


def test_no_max_tasks_leaves_publishable_full_run_unmodified(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_TURNS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", raising=False)
    config = _FakeConfig()
    original = lambda args: config  # noqa: E731
    upstream = SimpleNamespace(_build_run_config=original)

    saved = launcher._install_run_config_overrides(upstream, max_tasks=None)

    assert saved is original
    assert upstream._build_run_config(SimpleNamespace()) is config


def test_claude_code_turn_limit_is_persisted_in_agent_kwargs(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_MAX_TURNS", "64")
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "100000")
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "50")
    original = lambda args: _FakeConfig()  # noqa: E731
    upstream = SimpleNamespace(_build_run_config=original)

    launcher._install_run_config_overrides(upstream, max_tasks=2)
    config = upstream._build_run_config(SimpleNamespace())

    assert config.max_tasks == 2
    assert config.baseline.agent_kwargs == {
        "max_turns": 64,
        "evoagent_max_output_tokens": 8192,
        "evoagent_file_read_max_output_tokens": 8192,
        "evoagent_max_context_tokens": 100000,
        "evoagent_autocompact_pct_override": 50,
    }


def test_claude_code_policy_descriptors_avoid_harbor_extra_env_collision():
    class FakeEnvVar:
        def __init__(self, kwarg, *, env, type):
            self.kwarg = kwarg
            self.env = env
            self.type = type

    existing = FakeEnvVar("max_thinking_tokens", env="MAX_THINKING_TOKENS", type="int")

    class FakeClaudeCode:
        ENV_VARS = [existing]

    original = launcher._install_claude_code_policy_descriptors(
        FakeClaudeCode,
        FakeEnvVar,
    )

    assert original == [existing]
    assert len(FakeClaudeCode.ENV_VARS) == 5
    assert {
        descriptor.kwarg: descriptor.env for descriptor in FakeClaudeCode.ENV_VARS
    } == {
        "max_thinking_tokens": "MAX_THINKING_TOKENS",
        "evoagent_max_output_tokens": "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "evoagent_file_read_max_output_tokens": (
            "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS"
        ),
        "evoagent_max_context_tokens": "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "evoagent_autocompact_pct_override": "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
    }
    assert all(descriptor.type == "int" for descriptor in FakeClaudeCode.ENV_VARS)


def test_claude_code_extra_env_agent_kwarg_fails_before_harbor_factory(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_TURNS", raising=False)
    for env_name in launcher._CLAUDE_CODE_BOUNDED_ENV:
        monkeypatch.delenv(env_name, raising=False)
    upstream = SimpleNamespace(
        _build_run_config=lambda args: _FakeConfig(
            baseline=_FakeBaseline(agent_kwargs={"extra_env": {"A": "B"}})
        )
    )

    launcher._install_run_config_overrides(upstream, max_tasks=None)

    with pytest.raises(RuntimeError, match="pass the keyword twice"):
        upstream._build_run_config(SimpleNamespace())


def test_claude_code_turn_limit_fails_closed_when_invalid(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_MAX_TURNS", "unbounded")
    upstream = SimpleNamespace(_build_run_config=lambda args: _FakeConfig())

    launcher._install_run_config_overrides(upstream, max_tasks=None)

    with pytest.raises(RuntimeError, match="positive integer"):
        upstream._build_run_config(SimpleNamespace())


def test_claude_code_output_limit_fails_closed_when_invalid(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_TURNS", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "0")
    upstream = SimpleNamespace(_build_run_config=lambda args: _FakeConfig())

    launcher._install_run_config_overrides(upstream, max_tasks=None)

    with pytest.raises(RuntimeError, match="MAX_OUTPUT_TOKENS.*positive integer"):
        upstream._build_run_config(SimpleNamespace())


def test_claude_code_autocompact_percentage_fails_closed_above_100(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_TURNS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    monkeypatch.setenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "101")
    upstream = SimpleNamespace(_build_run_config=lambda args: _FakeConfig())

    launcher._install_run_config_overrides(upstream, max_tasks=None)

    with pytest.raises(RuntimeError, match="between 1 and 100"):
        upstream._build_run_config(SimpleNamespace())


def test_benchmark_harbor_contract_accepts_exact_legacy_trial_api():
    class CompatibleTrial:
        async def _execute_agent(self):
            return None

    launcher._assert_harbor_runtime_contract(
        installed_version="0.7.0",
        trial_type=CompatibleTrial,
    )


def test_harbor_openrouter_patch_uses_documented_claude_code_auth(tmp_path):
    adapter = tmp_path / "claude_code.py"
    adapter.write_text(
        "class ClaudeCode:\n"
        "    async def run(self):\n"
        "        escaped_instruction = shlex.quote(instruction)\n"
        "        env = {\n"
        '            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY")\n'
        '            or os.environ.get("ANTHROPIC_AUTH_TOKEN")\n'
        '            or "",\n'
        '            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": os.environ.get(\n'
        '                "CLAUDE_CODE_MAX_OUTPUT_TOKENS", None\n'
        "            ),\n"
        '            "FORCE_AUTO_BACKGROUND_TASKS": "1",\n'
        "        }\n"
        "        env = {k: v for k, v in env.items() if v}\n",
        encoding="utf-8",
    )

    before, after, patch = prepare_harbor_openrouter.prepare_claude_code_adapter(
        adapter
    )
    prepared = adapter.read_text(encoding="utf-8")

    assert before != after
    assert prepare_harbor_openrouter.AGENT_INSTRUCTION_PREFIX in prepared
    assert "must be an actual tool call" in prepared
    assert "never put an intended command only in prose" in prepared
    assert "bounded file or command output" in prepared
    assert '"ANTHROPIC_API_KEY": ""' in prepared
    assert '"ANTHROPIC_AUTH_TOKEN": os.environ.get' in prepared
    assert 'if v or k == "ANTHROPIC_API_KEY"' in prepared
    assert "harbor/agents/installed/claude_code.py" in patch
    assert prepare_harbor_openrouter.prepare_claude_code_source(prepared) == prepared


def test_trajectory_summary_excludes_raw_content_and_counts_tools(tmp_path):
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        """{
          "steps": [
            {"source": "user", "message": "private prompt"},
            {
              "source": "agent",
              "message": "private answer",
              "model_name": "qwen/qwen3-coder-plus",
              "metrics": {
                "prompt_tokens": 1234,
                "completion_tokens": 56,
                "cached_tokens": 789
              },
              "tool_calls": [{
                "function_name": "shell",
                "arguments": {"command": "secret command"}
              }],
              "observation": {"results": [{"content": "secret output"}]}
            }
          ],
          "final_metrics": {
            "total_prompt_tokens": 1234,
            "total_completion_tokens": 56,
            "total_cached_tokens": 789,
            "total_cost_usd": 0.12,
            "total_steps": 2,
            "extra": {
              "total_cache_creation_input_tokens": 100,
              "total_cache_read_input_tokens": 789,
              "private_provider_field": "secret usage metadata"
            }
          }
        }""",
        encoding="utf-8",
    )

    summary = summarize_harbor_trajectory.summarize_trajectory(trajectory)

    assert summary["tool_call_count"] == 1
    assert summary["tool_name_counts"] == {"shell": 1}
    assert summary["observation_result_count"] == 1
    assert summary["raw_messages_included"] is False
    assert summary["model_request_count"] == 1
    assert summary["max_prompt_tokens_per_request"] == 1234
    assert summary["max_completion_tokens_per_request"] == 56
    assert summary["usage"] == {
        "total_prompt_tokens": 1234,
        "total_completion_tokens": 56,
        "total_cached_tokens": 789,
        "total_cost_usd": 0.12,
        "total_steps": 2,
        "total_cache_creation_input_tokens": 100,
        "total_cache_read_input_tokens": 789,
    }
    serialized = str(summary)
    assert "private prompt" not in serialized
    assert "secret command" not in serialized
    assert "secret output" not in serialized
    assert "secret usage metadata" not in serialized


def test_trajectory_summary_rejects_non_finite_usage(tmp_path):
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        '{"steps": [], "final_metrics": {"total_cost_usd": NaN}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="finite and non-negative"):
        summarize_harbor_trajectory.summarize_trajectory(trajectory)


@pytest.mark.parametrize(
    ("version", "trial_type", "message"),
    [
        (
            "0.7.1",
            type("LegacyTrial", (), {"_execute_agent": lambda self: None}),
            "expected 0.7.0",
        ),
        ("0.7.0", type("RefactoredTrial", (), {}), "_execute_agent is absent"),
    ],
)
def test_benchmark_harbor_contract_fails_closed_on_api_drift(
    version,
    trial_type,
    message,
):
    with pytest.raises(RuntimeError, match=message):
        launcher._assert_harbor_runtime_contract(
            installed_version=version,
            trial_type=trial_type,
        )


def test_openrouter_catalogue_requires_exact_model_and_agent_parameters(monkeypatch):
    monkeypatch.setattr(
        verify_openrouter_model,
        "_request_json",
        lambda request, timeout: {
            "data": [
                {
                    "id": "qwen/qwen3-coder-plus",
                    "canonical_slug": "qwen/qwen3-coder-plus",
                    "name": "Qwen: Qwen3 Coder Plus",
                    "context_length": 1_000_000,
                    "pricing": {"prompt": "0.1", "completion": "0.2"},
                    "supported_parameters": ["max_tokens", "tools"],
                    "top_provider": {"max_completion_tokens": 65_536},
                    "expiration_date": None,
                }
            ]
        },
    )

    record = verify_openrouter_model.verify_model("qwen/qwen3-coder-plus")

    assert record["model_id"] == "qwen/qwen3-coder-plus"
    assert record["context_length"] == 1_000_000
    assert record["max_completion_tokens"] == 65_536


def test_openrouter_catalogue_rejects_unlisted_model(monkeypatch):
    monkeypatch.setattr(
        verify_openrouter_model,
        "_request_json",
        lambda request, timeout: {"data": []},
    )

    with pytest.raises(RuntimeError, match="not currently listed"):
        verify_openrouter_model.verify_model("qwen/guessed-id")


def test_openrouter_preset_separates_router_slug_from_exact_endpoint_tag(
    tmp_path: Path,
    monkeypatch,
):
    preset = {
        "preset_id": "mimo-required-v2",
        "model_id": "xiaomi/mimo-v2.5",
        "canonical_model_id": "xiaomi/mimo-v2.5-20260422",
        "provider_slug": "xiaomi",
        "provider_name": "Xiaomi",
        "endpoint_tag": "xiaomi/fp8",
        "prompt_cost_per_token_usd": "0.00000014",
        "completion_cost_per_token_usd": "0.00000028",
        "context_length": 1_048_576,
        "max_completion_tokens": 131_072,
        "supports_tools": True,
        "tool_choice_mode": "required_single_tool",
        "tool_choice_verified_at": "2026-08-28T02:22:53+00:00",
    }
    path = tmp_path / "preset.json"
    path.write_text(json.dumps(preset), encoding="utf-8")

    def fake_request(request, *, timeout):
        if request.full_url.endswith("/endpoints"):
            return {
                "data": {
                    "endpoints": [
                        {
                            "tag": "xiaomi/fp8",
                            "provider_name": "Xiaomi",
                            "model_id": "xiaomi/mimo-v2.5",
                            "context_length": 1_048_576,
                            "max_completion_tokens": 131_072,
                            "pricing": {
                                "prompt": "0.00000014",
                                "completion": "0.00000028",
                            },
                            "supported_parameters": [
                                "max_tokens",
                                "tools",
                                "tool_choice",
                            ],
                            "status": 0,
                        }
                    ]
                }
            }
        return {
            "data": [
                {
                    "id": "xiaomi/mimo-v2.5",
                    "canonical_slug": "xiaomi/mimo-v2.5-20260422",
                    "name": "Xiaomi: MiMo-V2.5",
                    "context_length": 1_050_000,
                    "pricing": {
                        "prompt": "0.00000014",
                        "completion": "0.00000028",
                    },
                    "supported_parameters": ["max_tokens", "tools", "tool_choice"],
                    "top_provider": {"max_completion_tokens": 131_072},
                    "expiration_date": None,
                }
            ]
        }

    monkeypatch.setattr(verify_openrouter_model, "_request_json", fake_request)

    record = verify_openrouter_model.verify_preset(path)

    assert record["endpoint"]["provider_slug"] == "xiaomi"
    assert record["endpoint"]["endpoint_tag"] == "xiaomi/fp8"
    assert record["tool_choice_mode"] == "required_single_tool"
    assert record["required_tool_choice_route_probe_required"] is True


def test_openrouter_required_tool_choice_needs_route_probe_not_catalogue_subtype(
    tmp_path: Path,
    monkeypatch,
):
    preset = {
        "model_id": "xiaomi/mimo-v2.5",
        "canonical_model_id": "xiaomi/mimo-v2.5-20260422",
        "provider_slug": "xiaomi",
        "provider_name": "Xiaomi",
        "endpoint_tag": "xiaomi/fp8",
        "prompt_cost_per_token_usd": "0.00000014",
        "completion_cost_per_token_usd": "0.00000028",
        "context_length": 1_048_576,
        "max_completion_tokens": 131_072,
        "supports_tools": True,
        "tool_choice_mode": "required_single_tool",
        "tool_choice_verified_at": "2026-08-28T02:22:53+00:00",
    }
    path = tmp_path / "preset.json"
    path.write_text(json.dumps(preset), encoding="utf-8")

    def fake_request(request, *, timeout):
        if request.full_url.endswith("/endpoints"):
            return {
                "data": {
                    "endpoints": [
                        {
                            "tag": "xiaomi/fp8",
                            "provider_name": "Xiaomi",
                            "model_id": "xiaomi/mimo-v2.5",
                            "context_length": 1_048_576,
                            "max_completion_tokens": 131_072,
                            "pricing": {
                                "prompt": "0.00000014",
                                "completion": "0.00000028",
                            },
                            "supported_parameters": [
                                "max_tokens",
                                "tools",
                                "tool_choice",
                            ],
                            "status": 0,
                        }
                    ]
                }
            }
        return {
            "data": [
                {
                    "id": "xiaomi/mimo-v2.5",
                    "canonical_slug": "xiaomi/mimo-v2.5-20260422",
                    "name": "Xiaomi: MiMo-V2.5",
                    "context_length": 1_050_000,
                    "pricing": {
                        "prompt": "0.00000014",
                        "completion": "0.00000028",
                    },
                    "supported_parameters": ["max_tokens", "tools", "tool_choice"],
                    "top_provider": {"max_completion_tokens": 131_072},
                    "expiration_date": None,
                }
            ]
        }

    monkeypatch.setattr(verify_openrouter_model, "_request_json", fake_request)

    record = verify_openrouter_model.verify_preset(path)

    assert record["required_tool_choice_route_probe_required"] is True
    assert "supports_tool_choice" not in record["endpoint"]


def test_openrouter_key_preflight_is_authenticated_and_redacted(monkeypatch):
    captured = {}

    def fake_request(request, *, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        return {
            "data": {
                "label": "private-label",
                "usage": 1.25,
                "limit": 6.0,
                "limit_remaining": 4.75,
                "is_free_tier": False,
                "is_management_key": False,
                "expires_at": "2026-08-29T00:00:00Z",
            }
        }

    monkeypatch.setattr(verify_openrouter_model, "_request_json", fake_request)
    checked_at = verify_openrouter_model.datetime(
        2026,
        8,
        28,
        0,
        0,
        tzinfo=verify_openrouter_model.timezone.utc,
    )
    record = verify_openrouter_model.verify_api_key_capacity(
        "test-only-secret-key",
        min_remaining_usd="1.20",
        now=checked_at,
    )

    assert captured == {
        "url": verify_openrouter_model.API_KEY_URL,
        "authorization": "Bearer test-only-secret-key",
    }
    assert record["authenticated"] is True
    assert record["remaining_sufficient"] is True
    serialized = json.dumps(record, sort_keys=True)
    assert "test-only-secret-key" not in serialized
    assert "private-label" not in serialized
    assert "4.75" not in serialized


def test_openrouter_key_preflight_rejects_insufficient_credit(monkeypatch):
    monkeypatch.setattr(
        verify_openrouter_model,
        "_request_json",
        lambda request, *, timeout: {
            "data": {
                "limit": 6.0,
                "limit_remaining": 0.25,
                "is_free_tier": False,
                "is_management_key": False,
                "expires_at": None,
            }
        },
    )

    with pytest.raises(RuntimeError, match="below the required cap"):
        verify_openrouter_model.verify_api_key_capacity(
            "test-only-secret-key",
            min_remaining_usd="1.20",
        )


def test_openrouter_key_preflight_treats_null_independent_limit_as_unknown(
    monkeypatch,
):
    monkeypatch.setattr(
        verify_openrouter_model,
        "_request_json",
        lambda request, *, timeout: {
            "data": {
                "limit": None,
                "limit_remaining": None,
                "is_free_tier": False,
                "is_management_key": False,
                "expires_at": None,
                "label": "must-not-be-persisted",
            }
        },
    )

    record = verify_openrouter_model.verify_api_key_capacity(
        "test-only-secret-key",
        min_remaining_usd="1.20",
    )

    assert record["limit_configured"] is False
    assert record["remaining_credit_reported"] is False
    assert record["remaining_sufficient"] is None
    assert record["credit_status"] == (
        "independent_limit_not_configured_credit_unknown"
    )
    assert "must-not-be-persisted" not in json.dumps(record, sort_keys=True)


def test_openrouter_explicit_tool_choice_requires_exact_endpoint_tag(
    tmp_path: Path,
):
    preset = {
        "model_id": "xiaomi/mimo-v2.5",
        "canonical_model_id": "xiaomi/mimo-v2.5-20260422",
        "provider_slug": "xiaomi",
        "provider_name": "Xiaomi",
        "prompt_cost_per_token_usd": "0.00000014",
        "completion_cost_per_token_usd": "0.00000028",
        "context_length": 1_048_576,
        "max_completion_tokens": 131_072,
        "supports_tools": True,
        "tool_choice_mode": "required_single_tool",
        "tool_choice_verified_at": "2026-08-28T02:22:53+00:00",
    }
    path = tmp_path / "preset.json"
    path.write_text(json.dumps(preset), encoding="utf-8")

    with pytest.raises(RuntimeError, match="exact endpoint tag"):
        verify_openrouter_model.verify_preset(path)


def test_openrouter_probe_retries_transient_shared_pool_limit(monkeypatch):
    calls = 0

    def fake_urlopen(request, *, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "5"},
                io.BytesIO(
                    b'{"error":{"metadata":{"limit_source":'
                    b'"upstream_provider_shared_pool"},"user_id":"private"}}'
                ),
            )
        return io.BytesIO(b'{"status":"ok"}')

    delays = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(verify_openrouter_model.time, "sleep", delays.append)

    payload = verify_openrouter_model._request_json(
        urllib.request.Request("https://example.invalid"),
        timeout=1,
        max_attempts=2,
    )

    assert payload == {"status": "ok"}
    assert calls == 2
    assert delays == [5.0]
    assert "private" not in verify_openrouter_model._sanitize_error_body(
        '{"user_id":"private"}'
    )


def test_release_smoke_has_a_post_learning_task_and_uses_verified_plus_preset():
    workflow = Path(".github/workflows/skillevolbench-benchmark.yml").read_text(
        encoding="utf-8"
    )
    preset = Path("configs/skillevolbench/openrouter-qwen3-coder-plus.yaml").read_text(
        encoding="utf-8"
    )
    free_preset = Path("configs/skillevolbench/openrouter-glm-5.2-free.yaml").read_text(
        encoding="utf-8"
    )
    qwen37_preset = Path(
        "configs/skillevolbench/openrouter-qwen3.7-plus.yaml"
    ).read_text(encoding="utf-8")

    assert 'SMOKE_MAX_TASKS: "2"' in workflow
    assert '--max-tasks "$SMOKE_MAX_TASKS"' in workflow
    assert 'pip install -r "$task_root/environment/requirements.txt"' in workflow
    assert '"requirements_sha256"' in workflow
    assert "prepare_harbor_openrouter.py" in workflow
    assert "Verify Claude Code tool calls through OpenRouter" in workflow
    assert 'ANTHROPIC_BASE_URL="https://openrouter.ai/api"' in workflow
    assert "--dangerously-skip-permissions" in workflow
    assert "tool_effect_verified" in workflow
    assert "synthetic_code_test_passed" in workflow
    assert "summarize_harbor_trajectory.py" in workflow
    assert 'blocker_code="openrouter_insufficient_credits"' in workflow
    assert 'blocker_code="openrouter_free_capacity_or_rate_limit"' in workflow
    assert 'blocker_code="openrouter_free_shared_pool_rate_limited"' in workflow
    assert 'blocker_code="openrouter_tool_probe_rate_limited"' in workflow
    assert "TOOL_PROBE_BLOCKER.txt" in workflow
    assert "OpenRouter returned HTTP 402 Insufficient credits" in workflow
    assert 'rm -f "$run_log"' in workflow
    assert "OPENROUTER_MODEL_ID: qwen/qwen3-coder-plus" in workflow
    assert "model_preset:" in workflow
    assert "confirm_compare:" in workflow
    assert "explicit confirm_compare acknowledgement" in workflow
    assert "Optional model presets are smoke-only" in workflow
    assert "qwen3.7-plus" in workflow
    assert 'model_id="qwen/qwen3.7-plus"' in workflow
    assert "glm-5.2-free" in workflow
    assert 'model_id="z-ai/glm-5.2:free"' in workflow
    assert "test-stdout.txt" in workflow
    assert "raw agent trajectories" in workflow
    assert "agent_model_name: qwen/qwen3-coder-plus" in preset
    assert "harbor_agent_name: claude-code" in preset
    assert 'CLAUDE_CODE_MAX_TURNS: "64"' in preset
    assert 'CLAUDE_CODE_MAX_OUTPUT_TOKENS: "8192"' in preset
    assert 'CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS: "8192"' in preset
    assert 'CLAUDE_CODE_MAX_CONTEXT_TOKENS: "100000"' in preset
    assert 'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: "50"' in preset
    assert "agent_model_name: z-ai/glm-5.2:free" in free_preset
    assert "model: openai/z-ai/glm-5.2:free" in free_preset
    assert "harbor_agent_name: claude-code" in free_preset
    assert 'CLAUDE_CODE_MAX_TURNS: "64"' in free_preset
    assert 'CLAUDE_CODE_MAX_CONTEXT_TOKENS: "100000"' in free_preset
    assert 'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: "50"' in free_preset
    assert "agent_model_name: qwen/qwen3.7-plus" in qwen37_preset
    assert "model: openai/qwen/qwen3.7-plus" in qwen37_preset
    assert "harbor_agent_name: claude-code" in qwen37_preset
    assert 'CLAUDE_CODE_MAX_TURNS: "64"' in qwen37_preset
    assert 'CLAUDE_CODE_MAX_CONTEXT_TOKENS: "100000"' in qwen37_preset
    assert 'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: "50"' in qwen37_preset
    assert "--env CLAUDE_CODE_MAX_CONTEXT_TOKENS" in workflow
    assert 'CLAUDE_CODE_VERSION: "2.1.235"' in workflow
    assert "expected_agent_version" in workflow
    assert "declare -A seen_trajectory_sha" in workflow
    assert "--verbose 2>&1 | tee" not in workflow
    assert '"model_yaml_sha256"' in workflow
    assert workflow.count('"agent_scope": "skill_component"') == 2
    assert workflow.count('"full_agent_evidence": False') == 2


def test_comparison_control_identity_excludes_studied_baseline_policy():
    config = {
        "order_seed": "A",
        "baseline": {
            "name": "no_skill",
            "model_name": "qwen/qwen3-coder-flash",
            "harbor_agent_name": "codex",
            "agent_kwargs": {"provider": "openrouter"},
        },
        "strategy": {"name": "chain"},
    }

    assert comparison._control_identity(config) == {
        "order_seed": "A",
        "model_name": "qwen/qwen3-coder-flash",
        "harbor_agent_name": "codex",
        "agent_kwargs": {"provider": "openrouter"},
        "strategy": {"name": "chain"},
        "benchmark_skills_root": None,
        "benchmark_tasks_root": None,
        "harbor_orchestrator_type": None,
        "harbor_n_concurrent_trials": None,
        "api_base": None,
        "api_key_env_var": None,
        "dry_run": None,
        "max_tasks": None,
    }


def test_comparison_control_identity_includes_full_strategy_parameters():
    config = {
        "order_seed": "A",
        "baseline": {
            "model_name": "qwen/qwen3-coder-plus",
            "harbor_agent_name": "claude-code",
            "agent_kwargs": {"max_turns": 64},
        },
        "strategy": {"name": "chain", "author_max_tokens": 16384},
    }

    changed = comparison._control_identity(config)
    config["strategy"] = {"name": "chain", "author_max_tokens": 4096}

    assert changed != comparison._control_identity(config)


@pytest.mark.parametrize(
    ("baseline_name", "expected_tasks"),
    (("no_skill", 180), ("selfgen_experience_always", 270)),
)
def test_full_comparison_requires_exact_pinned_trial_count(
    baseline_name, expected_tasks
):
    report = SimpleNamespace(
        baseline_name=baseline_name,
        strategy_name="chain",
        order_seed="A",
        n_tasks_attempted=expected_tasks,
    )
    config = {
        "order_seed": "A",
        "dry_run": False,
        "max_tasks": None,
        "baseline": {"name": baseline_name},
        "strategy": {"name": "chain"},
    }

    comparison._verify_report_config_identity(
        report=report,
        config=config,
        partial_smoke=False,
    )
    report.n_tasks_attempted -= 1
    with pytest.raises(ValueError, match="incomplete report"):
        comparison._verify_report_config_identity(
            report=report,
            config=config,
            partial_smoke=False,
        )


def test_partial_comparison_requires_exact_positive_task_cap():
    report = SimpleNamespace(
        baseline_name="no_skill",
        strategy_name="chain",
        order_seed="A",
        n_tasks_attempted=2,
    )
    config = {
        "order_seed": "A",
        "dry_run": False,
        "max_tasks": 2,
        "baseline": {"name": "no_skill"},
        "strategy": {"name": "chain"},
    }

    comparison._verify_report_config_identity(
        report=report,
        config=config,
        partial_smoke=True,
    )
    config["max_tasks"] = None
    with pytest.raises(ValueError, match="positive max_tasks"):
        comparison._verify_report_config_identity(
            report=report,
            config=config,
            partial_smoke=True,
        )


def test_comparison_rejects_dry_run_config():
    report = SimpleNamespace(
        baseline_name="no_skill",
        strategy_name="chain",
        order_seed="A",
        n_tasks_attempted=2,
    )
    config = {
        "order_seed": "A",
        "dry_run": True,
        "max_tasks": 2,
        "baseline": {"name": "no_skill"},
        "strategy": {"name": "chain"},
    }

    with pytest.raises(ValueError, match="real, non-dry run"):
        comparison._verify_report_config_identity(
            report=report,
            config=config,
            partial_smoke=True,
        )
