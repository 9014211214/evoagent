import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    SQLiteEvolutionProgramRepository,
)


def test_submission_recovery_preserves_persisted_evaluator_identity(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="5" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    plan = package.generations[1].plan
    assert plan is not None

    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    candidate_ready = package.generation_campaign.model_copy(
        update={"state": CampaignState.CANDIDATE_READY}
    )
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    with pytest.raises(ValueError, match="preserve its evaluator identity"):
        controller._advance_or_validate_submission_campaign(
            candidate_ready,
            policy=package.policy,
            signal=package.signal,
            attribution=package.attribution,
            plan=plan,
            evaluation_actor_id="substituted-evaluator",
            phase_at=plan.created_at,
        )

    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_read_only_submission_retry_does_not_rebind_caller_as_evaluator(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "read-only-lab",
        source_commit="6" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    plan = package.generations[1].plan
    assert plan is not None

    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    retried = controller.submit_generation(
        plan,
        evaluation_actor_id="read-only-retry-caller",
        submitted_at=plan.created_at,
    )

    assert retried.reused is True
    assert retried.campaign == package.generation_campaign
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events
