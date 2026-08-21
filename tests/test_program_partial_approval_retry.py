import sqlite3

import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramPackageManager,
    SQLiteEvolutionProgramRepository,
)
from evoagent.program.controller_public_final import (
    RetryHardenedEvolutionProgramController,
)


def _prepared_submission(package, tmp_path, prefix=""):
    g0 = package.generations[0]
    d0 = package.decisions[0]
    plan = package.generations[1].plan
    assert g0.outcome is not None
    assert plan is not None
    repository = SQLiteEvolutionProgramRepository(
        tmp_path / f"{prefix}program.db"
    )
    campaigns = SQLiteCampaignRepository(
        tmp_path / f"{prefix}campaign.db"
    )
    controller = RetryHardenedEvolutionProgramController(
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
    attribution, _ = controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )
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
        evaluation_actor_id="generation-evaluator",
        submitted_at=plan.created_at,
    )
    return controller, repository, campaigns, submission


def test_first_approval_exact_retry_is_read_only(tmp_path):
    source = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="9" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(source.package_path)
    controller, repository, campaigns, submission = _prepared_submission(
        package,
        tmp_path,
    )
    first = controller.approve_generation(
        submission.campaign.campaign_id,
        actor_id="reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=submission.campaign.revision,
    )
    assert first.state == CampaignState.APPROVAL_PENDING
    assert len(campaigns.approvals(first.campaign_id)) == 1
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    retried = controller.approve_generation(
        first.campaign_id,
        actor_id="reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=0,
    )

    assert retried == first
    assert len(campaigns.approvals(first.campaign_id)) == 1
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_first_approval_conflicting_retry_fails_without_writes(tmp_path):
    source = MultiGenerationEvolutionProgramLab(
        tmp_path / "conflict-source-lab",
        source_commit="a" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(source.package_path)
    controller, repository, campaigns, submission = _prepared_submission(
        package,
        tmp_path,
    )
    first = controller.approve_generation(
        submission.campaign.campaign_id,
        actor_id="reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=submission.campaign.revision,
    )
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    with pytest.raises(ValueError, match="approval retry conflicts"):
        controller.approve_generation(
            first.campaign_id,
            actor_id="reviewer-a",
            reason="forged retry rationale",
            expected_revision=0,
        )
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_first_approval_rejects_coherently_rehashed_forged_evaluation(
    tmp_path,
):
    source = MultiGenerationEvolutionProgramLab(
        tmp_path / "forged-source-lab",
        source_commit="b" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(source.package_path)
    controller, repository, campaigns, submission = _prepared_submission(
        package,
        tmp_path,
        prefix="forged-",
    )
    events = tuple(campaigns.audit_events())
    target = next(
        item
        for item in events
        if item.campaign_id == submission.campaign.campaign_id
        and item.event_type == "campaign_transitioned"
        and item.payload.get("to_state") == "approval_pending"
    )

    previous_hash = "0" * 64
    with sqlite3.connect(campaigns.path) as connection:
        for event in events:
            payload = dict(event.payload)
            if event.sequence == target.sequence:
                payload["reason"] = "coherently forged evaluation result"
            event_hash = SQLiteCampaignRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                campaign_id=event.campaign_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                payload=payload,
                created_at=event.created_at,
                previous_hash=previous_hash,
            )
            connection.execute(
                "UPDATE campaign_audit_events SET payload_json = ?, "
                "previous_hash = ?, event_hash = ? WHERE sequence = ?",
                (
                    SQLiteCampaignRepository._json(payload),
                    previous_hash,
                    event_hash,
                    event.sequence,
                ),
            )
            previous_hash = event_hash
        connection.commit()

    assert campaigns.verify_audit() is True
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())
    with pytest.raises(ValueError, match="evaluation-result audit was substituted"):
        controller.approve_generation(
            submission.campaign.campaign_id,
            actor_id="reviewer-a",
            reason="Independent evidence review passed.",
            expected_revision=submission.campaign.revision,
        )
    assert campaigns.approvals(submission.campaign.campaign_id) == []
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events
