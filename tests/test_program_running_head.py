from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    ProgramState,
    SQLiteEvolutionProgramRepository,
)


def test_running_head_tracks_the_active_successor_generation(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="a" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    g0 = package.generations[0]
    d0 = package.decisions[0]
    plan = package.generations[1].plan
    assert g0.outcome is not None
    assert plan is not None

    repository = SQLiteEvolutionProgramRepository(tmp_path / "program.db")
    campaigns = SQLiteCampaignRepository(tmp_path / "campaign.db")
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
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
    controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )
    decision, _ = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=d0.decision_id,
        decided_by=d0.decided_by,
        decided_at=d0.decided_at,
        signal=signal,
        attribution=package.attribution,
    )
    assert decision == d0

    submission = controller.submit_generation(
        plan,
        evaluation_actor_id="generation-evaluator",
        submitted_at=plan.created_at,
    )
    campaign = controller.approve_generation(
        submission.campaign.campaign_id,
        actor_id="generation-reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=submission.campaign.revision,
    )
    campaign = controller.approve_generation(
        campaign.campaign_id,
        actor_id="generation-reviewer-b",
        reason="Independent budget review passed.",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    controller.synchronize_authorization(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        actor_id="authorization-sync",
    )
    authorized_head = repository.head(plan.program_id)
    assert authorized_head.current_generation_index == 0
    assert authorized_head.active_generation_id == g0.generation_id

    controller.start_generation(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        expected_revision=authorized_head.revision,
        actor_id="generation-operator",
    )
    running_head = repository.head(plan.program_id)
    assert running_head.state == ProgramState.GENERATION_RUNNING
    assert running_head.current_generation_index == plan.generation_index
    assert running_head.active_generation_id == plan.generation_id
    assert repository.verify_state(plan.program_id) is True
