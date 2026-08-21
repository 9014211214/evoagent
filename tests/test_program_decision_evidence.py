import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    SQLiteEvolutionProgramRepository,
    build_attribution_receipt,
)


def test_continue_decision_requires_exact_persisted_evidence(tmp_path):
    source = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="b" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(source.package_path)
    g0 = package.generations[0]
    expected_decision = package.decisions[0]
    assert g0.outcome is not None

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
    registration_events = tuple(repository.events())

    with pytest.raises(ValueError, match="exact persisted generation evidence"):
        controller.decide(
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            decision_id=expected_decision.decision_id,
            decided_by=expected_decision.decided_by,
            decided_at=expected_decision.decided_at,
            signal=package.signal,
            attribution=package.attribution,
        )
    assert repository.list_decisions(g0.program_id) == []
    assert tuple(repository.events()) == registration_events

    with pytest.raises(ValueError, match="requires its learning signal"):
        controller.decide(
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            decision_id=expected_decision.decision_id,
            decided_by=expected_decision.decided_by,
            decided_at=expected_decision.decided_at,
            attribution=package.attribution,
        )
    assert repository.list_decisions(g0.program_id) == []
    assert tuple(repository.events()) == registration_events

    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id="independent-feedback-ingestor",
        created_at=package.signal.created_at,
    )
    signal_events = tuple(repository.events())

    with pytest.raises(ValueError, match="exact persisted signal binding"):
        controller.decide(
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            decision_id=expected_decision.decision_id,
            decided_by=expected_decision.decided_by,
            decided_at=expected_decision.decided_at,
            signal=signal,
            attribution=package.attribution,
        )
    assert repository.list_decisions(g0.program_id) == []
    assert tuple(repository.events()) == signal_events

    stored_attribution, _ = controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )
    evidence_events = tuple(repository.events())
    forged_attribution = build_attribution_receipt(
        signal,
        receipt_id="program-attribution:g0:unpersisted-forgery",
        failure_layer=FailureLayer.CONTEXT,
        action=EvolutionAction.UPDATE_CONTEXT,
        confidence=1.0,
        supported_experiment_hashes=("f" * 64,),
        attributor_id="unpersisted-attributor",
        created_at=package.attribution.created_at,
    )

    with pytest.raises(ValueError, match="exact persisted signal binding"):
        controller.decide(
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            decision_id=expected_decision.decision_id,
            decided_by=expected_decision.decided_by,
            decided_at=expected_decision.decided_at,
            signal=signal,
            attribution=forged_attribution,
        )
    assert repository.list_decisions(g0.program_id) == []
    assert tuple(repository.events()) == evidence_events

    with pytest.raises(ValueError, match="decision actor must differ"):
        controller.decide(
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            decision_id=expected_decision.decision_id,
            decided_by=stored_attribution.attributor_id,
            decided_at=expected_decision.decided_at,
            signal=signal,
            attribution=stored_attribution,
        )
    assert repository.list_decisions(g0.program_id) == []
    assert tuple(repository.events()) == evidence_events

    decision, reused = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=expected_decision.decision_id,
        decided_by=expected_decision.decided_by,
        decided_at=expected_decision.decided_at,
        signal=signal,
        attribution=stored_attribution,
    )
    assert reused is False
    assert decision == expected_decision
