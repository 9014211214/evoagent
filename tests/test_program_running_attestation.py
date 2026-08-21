import sqlite3
from datetime import timedelta

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
    ProgramExecutionCheckpoint,
    ProgramState,
    SQLiteEvolutionProgramRepository,
)


def _running_lifecycle(tmp_path):
    source = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="8" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(source.package_path)
    g0, g1 = package.generations
    d0 = package.decisions[0]
    plan = g1.plan
    assert g0.outcome is not None
    assert plan is not None

    program_database = tmp_path / "program.db"
    campaign_database = tmp_path / "campaign.db"
    repository = SQLiteEvolutionProgramRepository(program_database)
    campaigns = SQLiteCampaignRepository(campaign_database)
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
        created_by=package.program_events[0].actor_id,
        created_at=g0.created_at,
    )
    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id=package.program_events[2].actor_id,
        created_at=package.signal.created_at,
    )
    attribution, _ = controller.store_attribution(
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
        attribution=attribution,
    )
    assert decision == d0

    evaluator = package.campaign_events[1].actor_id
    submission = controller.submit_generation(
        plan,
        evaluation_actor_id=evaluator,
        submitted_at=plan.created_at,
    )
    campaign = submission.campaign
    for approval in package.generation_approvals:
        campaign = controller.approve_generation(
            campaign.campaign_id,
            actor_id=approval.actor_id,
            reason=approval.reason,
            expected_revision=campaign.revision,
        )
    assert campaign.state == CampaignState.AUTHORIZED

    controller.synchronize_authorization(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        actor_id=package.program_events[7].actor_id,
    )
    authorized_head = repository.head(plan.program_id)
    controller.start_generation(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        expected_revision=authorized_head.revision,
        actor_id=package.program_events[8].actor_id,
    )
    running_head = repository.head(plan.program_id)
    assert running_head.state == ProgramState.GENERATION_RUNNING
    program_checkpoint = repository.checkpoint()
    campaign_checkpoint = campaigns.checkpoint()
    program_anchor = ProgramExecutionCheckpoint(
        event_count=program_checkpoint.event_count,
        head_hash=program_checkpoint.head_hash,
    )
    campaign_anchor = ProgramExecutionCheckpoint(
        event_count=campaign_checkpoint.event_count,
        head_hash=campaign_checkpoint.head_hash,
    )
    attested_at = max(
        running_head.updated_at,
        campaigns.get(campaign.campaign_id).updated_at,
    ) + timedelta(seconds=1)
    return (
        package,
        controller,
        repository,
        campaigns,
        program_database,
        campaign_database,
        plan,
        program_anchor,
        campaign_anchor,
        attested_at,
    )


def test_running_generation_attestation_binds_both_registries(tmp_path):
    (
        package,
        controller,
        repository,
        campaigns,
        _,
        _,
        plan,
        program_anchor,
        campaign_anchor,
        attested_at,
    ) = _running_lifecycle(tmp_path)

    attestation = controller.attest_running_generation(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        expected_program_checkpoint=program_anchor,
        expected_campaign_checkpoint=campaign_anchor,
        attested_by="independent-running-generation-attestor",
        attested_at=attested_at,
    )

    assert attestation.program_checkpoint == program_anchor
    assert attestation.campaign_checkpoint == campaign_anchor
    assert attestation.program_state == "generation_running"
    assert attestation.campaign_state == "authorized"
    assert attestation.plan_hash == plan.plan_hash
    assert attestation.source_signal_hash == package.signal.signal_hash
    assert attestation.attribution_receipt_hash == (
        package.attribution.receipt_hash
    )
    assert attestation.optimizer_execution_authorized is False
    assert attestation.checkpoint_promotion_authorized is False
    assert attestation.production_activation_authorized is False
    assert repository.verify_state(plan.program_id) is True
    assert campaigns.verify_audit(campaign_anchor) is True


def test_forged_external_anchor_is_rejected_before_attestation(tmp_path):
    (
        _,
        controller,
        _,
        _,
        _,
        _,
        plan,
        program_anchor,
        campaign_anchor,
        attested_at,
    ) = _running_lifecycle(tmp_path)
    forged = program_anchor.model_copy(update={"head_hash": "0" * 64})

    with pytest.raises(ValueError, match="Program audit tail differs"):
        controller.attest_running_generation(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            expected_program_checkpoint=forged,
            expected_campaign_checkpoint=campaign_anchor,
            attested_by="independent-running-generation-attestor",
            attested_at=attested_at,
        )


def test_program_audit_tail_truncation_is_detected_by_external_anchor(tmp_path):
    (
        _,
        controller,
        _,
        _,
        program_database,
        _,
        plan,
        program_anchor,
        campaign_anchor,
        attested_at,
    ) = _running_lifecycle(tmp_path)
    with sqlite3.connect(program_database) as connection:
        connection.execute(
            "DELETE FROM program_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM program_audit_events)"
        )
        connection.commit()

    with pytest.raises(ValueError, match="Program audit tail differs"):
        controller.attest_running_generation(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            expected_program_checkpoint=program_anchor,
            expected_campaign_checkpoint=campaign_anchor,
            attested_by="independent-running-generation-attestor",
            attested_at=attested_at,
        )


def test_campaign_audit_tail_truncation_is_detected_by_external_anchor(tmp_path):
    (
        _,
        controller,
        _,
        _,
        _,
        campaign_database,
        plan,
        program_anchor,
        campaign_anchor,
        attested_at,
    ) = _running_lifecycle(tmp_path)
    with sqlite3.connect(campaign_database) as connection:
        connection.execute(
            "DELETE FROM campaign_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM campaign_audit_events)"
        )
        connection.commit()

    with pytest.raises(ValueError, match="Campaign audit tail differs"):
        controller.attest_running_generation(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            expected_program_checkpoint=program_anchor,
            expected_campaign_checkpoint=campaign_anchor,
            attested_by="independent-running-generation-attestor",
            attested_at=attested_at,
        )


def test_running_generation_attestor_must_be_independent(tmp_path):
    (
        package,
        controller,
        _,
        _,
        _,
        _,
        plan,
        program_anchor,
        campaign_anchor,
        attested_at,
    ) = _running_lifecycle(tmp_path)
    evaluator = package.campaign_events[1].actor_id

    with pytest.raises(ValueError, match="attestor overlaps"):
        controller.attest_running_generation(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            expected_program_checkpoint=program_anchor,
            expected_campaign_checkpoint=campaign_anchor,
            attested_by=evaluator,
            attested_at=attested_at,
        )
