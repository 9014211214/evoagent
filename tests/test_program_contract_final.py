from datetime import timedelta

import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    ProgramConflictError,
    SQLiteEvolutionProgramRepository,
    build_attribution_receipt,
)


def _package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="2" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def _controller(tmp_path, prefix):
    repository = SQLiteEvolutionProgramRepository(
        tmp_path / f"{prefix}-program.db"
    )
    campaigns = SQLiteCampaignRepository(
        tmp_path / f"{prefix}-campaign.db"
    )
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    return controller, repository, campaigns


def _prepare_decision_evidence(package, tmp_path, prefix):
    controller, repository, campaigns = _controller(tmp_path, prefix)
    g0 = package.generations[0]
    d0 = package.decisions[0]
    assert g0.outcome is not None
    controller.register_from_release(
        package.drift_release_package,
        program_id=g0.program_id,
        policy=package.policy,
        generation_id=g0.generation_id,
        outcome_id=g0.outcome.outcome_id,
        created_by="program-owner",
        created_at=g0.created_at,
    )
    signal, reused = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id="independent-feedback-ingestor",
        created_at=package.signal.created_at,
    )
    assert reused is False
    attribution, reused = controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )
    assert reused is False
    return (
        controller,
        repository,
        campaigns,
        g0,
        d0,
        signal,
        attribution,
    )


def test_public_contract_allows_only_one_immutable_evidence_set(tmp_path):
    package = _package(tmp_path)
    (
        controller,
        repository,
        _,
        g0,
        d0,
        signal,
        attribution,
    ) = _prepare_decision_evidence(package, tmp_path, "unique")

    same_signal, reused = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=signal.signal_id,
        actor_id="independent-feedback-ingestor",
        created_at=signal.created_at,
    )
    assert reused is True
    assert same_signal == signal

    with pytest.raises(ProgramConflictError, match="different immutable feedback"):
        controller.store_feedback(
            package.drift_release_package,
            program_id=g0.program_id,
            generation_index=0,
            signal_id="program-signal:g0:second",
            actor_id="independent-feedback-ingestor",
            created_at=signal.created_at,
        )

    same_attribution, reused = controller.store_attribution(
        g0.program_id,
        attribution,
        actor_id=attribution.attributor_id,
        created_at=attribution.created_at,
    )
    assert reused is True
    assert same_attribution == attribution

    second_attribution = build_attribution_receipt(
        signal,
        receipt_id="program-attribution:g0:second",
        failure_layer=FailureLayer.CONTEXT,
        action=EvolutionAction.UPDATE_CONTEXT,
        confidence=1.0,
        supported_experiment_hashes=("e" * 64,),
        attributor_id="second-independent-attributor",
        created_at=attribution.created_at + timedelta(seconds=1),
    )
    with pytest.raises(
        ProgramConflictError,
        match="different immutable attribution",
    ):
        controller.store_attribution(
            g0.program_id,
            second_attribution,
            actor_id=second_attribution.attributor_id,
            created_at=second_attribution.created_at,
        )

    decision, reused = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=d0.decision_id,
        decided_by=d0.decided_by,
        decided_at=d0.decided_at,
        signal=signal,
        attribution=attribution,
    )
    assert reused is False
    assert decision == d0
    assert len(repository.list_signals(g0.program_id)) == 1
    assert len(repository.list_attributions(g0.program_id)) == 1


def test_public_contract_partial_approval_retry_is_read_only(tmp_path):
    package = _package(tmp_path)
    (
        controller,
        repository,
        campaigns,
        g0,
        d0,
        signal,
        attribution,
    ) = _prepare_decision_evidence(package, tmp_path, "approval")
    plan = package.generations[1].plan
    assert plan is not None
    controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=d0.decision_id,
        decided_by=d0.decided_by,
        decided_at=d0.decided_at,
        signal=signal,
        attribution=attribution,
    )
    submission = controller.submit_generation(
        plan,
        evaluation_actor_id="independent-generation-evaluator",
        submitted_at=plan.created_at,
    )
    campaign = controller.approve_generation(
        submission.campaign.campaign_id,
        actor_id="independent-reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=submission.campaign.revision,
    )
    assert campaign.state == CampaignState.APPROVAL_PENDING
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    retried = controller.approve_generation(
        campaign.campaign_id,
        actor_id="independent-reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=0,
    )
    assert retried == campaign
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events

    with pytest.raises(ValueError, match="approval retry conflicts"):
        controller.approve_generation(
            campaign.campaign_id,
            actor_id="independent-reviewer-a",
            reason="forged retry reason",
            expected_revision=0,
        )
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events
