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


def _controller(tmp_path, prefix):
    repository = SQLiteEvolutionProgramRepository(
        tmp_path / f"{prefix}-program.db"
    )
    campaigns = SQLiteCampaignRepository(
        tmp_path / f"{prefix}-campaign.db"
    )
    return (
        EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(campaigns),
        ),
        repository,
    )


def _package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="7" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def test_feedback_ingestor_cannot_become_causal_attributor(tmp_path):
    package = _package(tmp_path)
    controller, repository = _controller(tmp_path, "overlap")
    g0 = package.generations[0]
    assert g0.outcome is not None

    controller.register_from_release(
        package.drift_release_package,
        program_id=g0.program_id,
        policy=package.policy,
        generation_id=g0.generation_id,
        outcome_id=g0.outcome.outcome_id,
        created_by=package.program_events[0].actor_id,
        created_at=g0.created_at,
    )
    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id=package.attribution.attributor_id,
        created_at=package.signal.created_at,
    )
    assert signal == package.signal
    before_events = tuple(repository.events())

    with pytest.raises(ValueError, match="must differ from feedback ingestor"):
        controller.store_attribution(
            g0.program_id,
            package.attribution,
            actor_id=package.attribution.attributor_id,
            created_at=package.attribution.created_at,
        )

    assert repository.list_attributions(g0.program_id) == []
    assert tuple(repository.events()) == before_events


def test_independent_feedback_ingestor_and_attributor_are_accepted(tmp_path):
    package = _package(tmp_path)
    controller, repository = _controller(tmp_path, "independent")
    g0 = package.generations[0]
    assert g0.outcome is not None

    controller.register_from_release(
        package.drift_release_package,
        program_id=g0.program_id,
        policy=package.policy,
        generation_id=g0.generation_id,
        outcome_id=g0.outcome.outcome_id,
        created_by=package.program_events[0].actor_id,
        created_at=g0.created_at,
    )
    controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id="independent-feedback-ingestor",
        created_at=package.signal.created_at,
    )
    attribution, reused = controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )

    assert reused is False
    assert attribution == package.attribution
    assert repository.list_attributions(g0.program_id) == [
        package.attribution
    ]
