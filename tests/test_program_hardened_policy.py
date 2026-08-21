from datetime import datetime, timezone

import pytest

from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramGate,
    EvolutionProgramPackageManager,
    ProgramAction,
    ProgramBudget,
    ProgramHead,
    ProgramState,
    build_program_policy,
)


def _package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "lab",
        source_commit="2" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def test_ready_respects_explicit_stop_on_ready_policy(tmp_path):
    package = _package(tmp_path)
    outcome = package.generations[1].outcome
    assert outcome is not None
    policy = build_program_policy(
        policy_id="policy:ready-pause",
        budget=ProgramBudget(
            max_generations=3,
            max_rollbacks=2,
            max_holds=1,
            max_generation_campaigns=2,
            max_total_pairs=10000,
            max_total_tokens=10000000,
            max_total_cost_usd=100.0,
        ),
        stop_on_ready=False,
    )
    decision = EvolutionProgramGate().decide(
        policy=policy,
        head=package.final_head,
        outcome=outcome,
        decision_id="decision:ready-pause",
        decided_by="policy-controller",
        decided_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    assert decision.action == ProgramAction.PAUSE


def test_consecutive_non_improvement_and_rollback_caps_stop_budget(tmp_path):
    package = _package(tmp_path)
    g0 = package.generations[0]
    assert g0.outcome is not None
    head = ProgramHead(
        program_id=g0.program_id,
        state=ProgramState.RUNNING,
        current_generation_index=0,
        active_generation_id=g0.generation_id,
        revision=0,
        rollback_count=1,
        hold_count=0,
        generation_campaign_count=0,
        total_pairs=g0.outcome.pair_count,
        total_tokens=g0.outcome.total_tokens,
        total_cost_usd=g0.outcome.total_cost_usd,
        updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    policy = build_program_policy(
        policy_id="policy:non-improving-stop",
        budget=ProgramBudget(
            max_generations=3,
            max_rollbacks=1,
            max_holds=1,
            max_generation_campaigns=2,
            max_total_pairs=10000,
            max_total_tokens=10000000,
            max_total_cost_usd=100.0,
        ),
        maximum_consecutive_non_improving=0,
    )
    decision = EvolutionProgramGate().decide(
        policy=policy,
        head=head,
        outcome=g0.outcome,
        decision_id="decision:non-improving-stop",
        decided_by="policy-controller",
        decided_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        signal=package.signal,
        attribution=package.attribution,
        consecutive_non_improving_count=1,
    )
    assert decision.action == ProgramAction.STOP_BUDGET


def test_high_risk_generation_cannot_disable_approvals(tmp_path):
    package = _package(tmp_path)
    g0 = package.generations[0]
    assert g0.outcome is not None
    policy = build_program_policy(
        policy_id="policy:no-approvals",
        budget=ProgramBudget(
            max_generations=3,
            max_rollbacks=2,
            max_holds=1,
            max_generation_campaigns=2,
            max_total_pairs=10000,
            max_total_tokens=10000000,
            max_total_cost_usd=100.0,
        ),
        require_generation_approvals=False,
    )
    with pytest.raises(ValueError, match="cannot disable"):
        EvolutionProgramGate().decide(
            policy=policy,
            head=package.final_head,
            outcome=g0.outcome,
            decision_id="decision:no-approvals",
            decided_by="policy-controller",
            decided_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            signal=package.signal,
            attribution=package.attribution,
            consecutive_non_improving_count=1,
        )
