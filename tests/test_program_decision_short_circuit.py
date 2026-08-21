from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    ProgramAction,
    ProgramBudget,
    SQLiteEvolutionProgramRepository,
    build_program_policy,
)


def _controller(tmp_path, name):
    repository = SQLiteEvolutionProgramRepository(tmp_path / f"{name}-program.db")
    campaigns = SQLiteCampaignRepository(tmp_path / f"{name}-campaign.db")
    return (
        EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(campaigns),
        ),
        repository,
    )


def test_budget_decision_can_short_circuit_a_signal_without_attribution(tmp_path):
    source = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="1" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(source.package_path)
    g0 = package.generations[0]
    assert g0.outcome is not None
    controller, repository = _controller(tmp_path, "budget")
    policy = build_program_policy(
        policy_id="policy:budget-short-circuit",
        budget=ProgramBudget(
            max_generations=1,
            max_rollbacks=2,
            max_holds=1,
            max_generation_campaigns=1,
            max_total_pairs=10_000,
            max_total_tokens=10_000_000,
            max_total_cost_usd=100.0,
        ),
    )
    controller.register_from_release(
        package.drift_release_package,
        program_id="program:budget-short-circuit",
        policy=policy,
        generation_id="generation:budget:g0",
        outcome_id="outcome:budget:g0",
        created_by="program-owner",
        created_at=g0.created_at,
    )
    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id="program:budget-short-circuit",
        generation_index=0,
        signal_id="signal:budget:g0",
        actor_id="feedback-ingestor",
        created_at=package.signal.created_at,
    )
    decision, reused = controller.decide(
        program_id="program:budget-short-circuit",
        generation_id="generation:budget:g0",
        decision_id="decision:budget:stop",
        decided_by="policy-controller",
        decided_at=package.decisions[0].decided_at,
        signal=signal,
    )
    assert reused is False
    assert decision.action == ProgramAction.STOP_BUDGET
    assert repository.list_attributions("program:budget-short-circuit") == []


def test_exact_continue_decision_retry_is_read_only(tmp_path):
    source = MultiGenerationEvolutionProgramLab(
        tmp_path / "retry-source-lab",
        source_commit="2" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(source.package_path)
    g0 = package.generations[0]
    expected = package.decisions[0]
    assert g0.outcome is not None
    controller, repository = _controller(tmp_path, "retry")
    controller.register_from_release(
        package.drift_release_package,
        program_id=g0.program_id,
        policy=package.policy,
        generation_id=g0.generation_id,
        outcome_id=g0.outcome.outcome_id,
        created_by="program-owner",
        created_at=g0.created_at,
    )
    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id="feedback-ingestor",
        created_at=package.signal.created_at,
    )
    attribution, _ = controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )
    first, first_reused = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=expected.decision_id,
        decided_by=expected.decided_by,
        decided_at=expected.decided_at,
        signal=signal,
        attribution=attribution,
    )
    before_events = tuple(repository.events())
    second, second_reused = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=expected.decision_id,
        decided_by=expected.decided_by,
        decided_at=expected.decided_at,
        signal=signal,
        attribution=attribution,
    )
    assert first_reused is False
    assert second_reused is True
    assert first == second == expected
    assert tuple(repository.events()) == before_events
