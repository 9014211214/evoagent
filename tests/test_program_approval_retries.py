import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    SQLiteEvolutionProgramRepository,
)


def test_exact_approval_retry_is_read_only_and_conflicts_fail(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="d" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    approval = package.generation_approvals[0]
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    retried = controller.approve_generation(
        approval.campaign_id,
        actor_id=approval.actor_id,
        reason=approval.reason,
        expected_revision=0,
    )

    assert retried == package.generation_campaign
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events

    with pytest.raises(ValueError, match="approval retry conflicts"):
        controller.approve_generation(
            approval.campaign_id,
            actor_id=approval.actor_id,
            reason="forged retry rationale",
            expected_revision=0,
        )
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events
