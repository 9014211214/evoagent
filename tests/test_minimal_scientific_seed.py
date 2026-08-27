from __future__ import annotations

import json
from pathlib import Path

import pytest

from evoagent.continual import ContinualTaskRole
from evoagent.integrations.minimal_scientific_seed import (
    EXPECTED_LOCAL_SCORES,
    MinimalScientificSeedLock,
    build_minimal_scientific_seed_plan,
    execute_minimal_scientific_seed,
    run_zero_cost_scientific_dry_run,
    verify_minimal_scientific_seed_lock,
)
from evoagent.integrations.openrouter import (
    OpenRouterIntegrationError,
    OpenRouterModelPreset,
    OpenRouterUsageLedger,
)


ROOT = Path(__file__).parents[1]
PRESET_PATH = ROOT / "configs/full_agent/openrouter-mimo-v2.5-xiaomi.json"
LOCK_PATH = ROOT / "configs/full_agent/minimal-scientific-seed-A.lock.json"
QWEN_PRESET_PATH = ROOT / "configs/full_agent/openrouter-qwen3.8-flash-alibaba.json"
QWEN_LOCK_PATH = (
    ROOT / "configs/full_agent/minimal-scientific-seed-A-qwen3.8-flash.lock.json"
)


def _preset() -> OpenRouterModelPreset:
    return OpenRouterModelPreset.model_validate_json(
        PRESET_PATH.read_text(encoding="utf-8")
    )


def _qwen_preset() -> OpenRouterModelPreset:
    return OpenRouterModelPreset.model_validate_json(
        QWEN_PRESET_PATH.read_text(encoding="utf-8")
    )


def _matching_transport(payload, _api_key):
    requested = json.loads(payload["messages"][1]["content"])
    return {
        "model": "xiaomi/mimo-v2.5-20260422",
        "provider": "Xiaomi",
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": requested["required_tool"],
                                "arguments": json.dumps(
                                    requested["required_arguments"],
                                    sort_keys=True,
                                    separators=(",", ":"),
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


def test_frozen_lock_binds_exact_12_task_five_snapshot_plan(tmp_path: Path):
    plan, _ = build_minimal_scientific_seed_plan(tmp_path, preset=_preset())
    lock = MinimalScientificSeedLock.model_validate_json(
        LOCK_PATH.read_text(encoding="utf-8")
    )

    verify_minimal_scientific_seed_lock(plan, lock)
    assert len(plan.manifest.tasks) == 12
    assert {role: sum(item.role == role for item in plan.manifest.tasks) for role in ContinualTaskRole} == {
        role: 3 for role in ContinualTaskRole
    }
    assert plan.budget.evaluation_episodes == 60
    assert plan.budget.max_model_cost_usd == 0.6
    assert plan.budget.authorization_cap_usd == 1.2


def test_qwen_frozen_lock_binds_reasoning_disabled_preset(tmp_path: Path):
    preset = _qwen_preset()
    plan, _ = build_minimal_scientific_seed_plan(tmp_path, preset=preset)
    lock = MinimalScientificSeedLock.model_validate_json(
        QWEN_LOCK_PATH.read_text(encoding="utf-8")
    )

    verify_minimal_scientific_seed_lock(plan, lock)
    mimo_plan, _ = build_minimal_scientific_seed_plan(
        tmp_path / "mimo",
        preset=_preset(),
    )
    assert preset.reasoning_enabled is False
    assert plan.model_preset_hash != mimo_plan.model_preset_hash
    assert plan.budget.evaluation_episodes == 60


def test_zero_cost_dry_run_has_frozen_causal_progression(tmp_path: Path):
    evidence = run_zero_cost_scientific_dry_run(tmp_path, preset=_preset())

    assert tuple(evidence["overall_scores"]) == EXPECTED_LOCAL_SCORES
    assert evidence["final_role_scores"] == {
        ContinualTaskRole.RETENTION: 1.0,
        ContinualTaskRole.TRANSFER: 1.0,
        ContinualTaskRole.ADVERSARIAL: 1.0,
        ContinualTaskRole.COMPOSITION: 1.0,
    }
    assert evidence["external_model_called"] is False
    assert evidence["benchmark_score_claimed"] is False


def test_external_seed_derives_scores_and_persists_no_raw_trajectory(tmp_path: Path):
    preset = _preset()
    plan, snapshots = build_minimal_scientific_seed_plan(
        tmp_path / "plan",
        preset=preset,
    )

    result = execute_minimal_scientific_seed(
        tmp_path / "run",
        plan=plan,
        snapshots=snapshots,
        preset=preset,
        api_key="test-only-key",
        source_commit="1" * 40,
        requester_id="requester",
        approver_ids=("owner", "static-budget-policy"),
        authorization_anchor="github-actions://example/run/1",
        transport=_matching_transport,
    )

    assert result.status == "passed"
    assert tuple(item.overall_score for item in result.reports) == EXPECTED_LOCAL_SCORES
    assert result.overall_score_delta == 1.0
    assert result.final_retention_drop_from_first_passing_round == 0.0
    assert result.total_regression_count == 0
    assert result.final_safety_violation_count == 0
    assert 60 < result.usage.requests <= 180
    assert result.usage.cost_usd == pytest.approx(result.usage.requests * 0.0001)
    serialized = result.model_dump_json()
    assert "test-only-key" not in serialized
    assert "public synthetic scientific content" not in serialized
    assert result.raw_trajectories_persisted is False
    assert result.external_benchmark is False


def test_shared_ledger_reserves_before_network_and_stops_at_exact_cap():
    ledger = OpenRouterUsageLedger(
        preset=_preset(),
        max_requests=2,
        max_prompt_bytes_per_request=4096,
        max_output_tokens_per_request=128,
        max_cost_usd=0.01,
    )
    ledger.reserve(prompt_bytes=100, max_output_tokens=20)
    ledger.reserve(prompt_bytes=100, max_output_tokens=20)

    with pytest.raises(OpenRouterIntegrationError, match="request-count cap"):
        ledger.reserve(prompt_bytes=100, max_output_tokens=20)
    assert ledger.usage.requests == 2
