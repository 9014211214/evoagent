import sqlite3

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
    ProgramConflictError,
    SQLiteEvolutionProgramRepository,
)


def _backup_database(source, destination):
    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def test_exact_authorize_start_submit_and_complete_retries_are_read_only(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="3" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    g1 = package.generations[1]
    assert g1.outcome is not None
    assert g1.plan is not None
    before_head = repository.head(package.final_head.program_id)
    before_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    submitted = controller.submit_generation(
        g1.plan,
        evaluation_actor_id="submission-retry",
        submitted_at=g1.updated_at,
    )
    authorized = controller.synchronize_authorization(
        program_id=g1.program_id,
        generation_id=g1.generation_id,
        campaign_id=package.generation_campaign.campaign_id,
        actor_id="authorization-retry",
    )
    started = controller.start_generation(
        program_id=g1.program_id,
        generation_id=g1.generation_id,
        campaign_id=package.generation_campaign.campaign_id,
        expected_revision=0,
        actor_id="start-retry",
    )
    completed = controller.complete_generation(
        package.passing_release_package,
        program_id=g1.program_id,
        generation_id=g1.generation_id,
        outcome_id=g1.outcome.outcome_id,
        expected_revision=0,
        actor_id="completion-retry",
        completed_at=g1.outcome.completed_at,
    )

    assert submitted.reused is True
    assert submitted.generation == g1
    assert submitted.campaign == package.generation_campaign
    assert authorized == g1
    assert started == g1
    assert completed == g1
    assert repository.head(g1.program_id) == before_head
    assert tuple(repository.events()) == before_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_partial_submission_recovers_without_campaign_reuse_events(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="8" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    g0, g1 = package.generations
    d0, d1 = package.decisions
    assert g0.outcome is not None
    assert g1.plan is not None
    campaign_id = package.generation_campaign.campaign_id
    evaluator = package.campaign_events[1].actor_id
    plan_event = next(
        item
        for item in package.program_events
        if item.event_type.value == "generation_planned"
    )

    phases = (
        (CampaignState.OPEN, 1, 0, True),
        (CampaignState.CANDIDATE_READY, 2, 1, False),
        (CampaignState.EVALUATION_PENDING, 3, 2, False),
        (CampaignState.APPROVAL_PENDING, 4, 3, False),
    )
    for state, retained_events, revision, clear_candidate in phases:
        program_database = tmp_path / f"program-{state.value}.db"
        campaign_database = tmp_path / f"campaign-{state.value}.db"
        _backup_database(lab.program_database, program_database)
        _backup_database(lab.campaign_database, campaign_database)

        with sqlite3.connect(program_database) as connection:
            connection.execute(
                "DELETE FROM program_decisions WHERE decision_id = ?",
                (d1.decision_id,),
            )
            connection.execute(
                "DELETE FROM program_audit_events WHERE sequence > ?",
                (plan_event.sequence,),
            )
            connection.execute(
                "UPDATE evolution_programs SET state = ?, updated_at = ? "
                "WHERE program_id = ?",
                ("running", d0.decided_at.isoformat(), g0.program_id),
            )
            connection.execute(
                "UPDATE program_heads SET state = ?, "
                "current_generation_index = 0, active_generation_id = ?, "
                "revision = 1, rollback_count = 1, hold_count = 0, "
                "generation_campaign_count = 0, total_pairs = ?, "
                "total_tokens = ?, total_cost_usd = ?, last_decision_id = ?, "
                "updated_at = ? WHERE program_id = ?",
                (
                    "running",
                    g0.generation_id,
                    g0.outcome.pair_count,
                    g0.outcome.total_tokens,
                    g0.outcome.total_cost_usd,
                    d0.decision_id,
                    d0.decided_at.isoformat(),
                    g0.program_id,
                ),
            )
            connection.execute(
                "UPDATE program_generations SET status = ?, outcome_json = NULL, "
                "campaign_id = NULL, updated_at = ? WHERE program_id = ? "
                "AND generation_id = ?",
                (
                    "planned",
                    g1.plan.created_at.isoformat(),
                    g1.program_id,
                    g1.generation_id,
                ),
            )
            connection.commit()

        with sqlite3.connect(campaign_database) as connection:
            connection.execute("DELETE FROM campaign_approvals")
            connection.execute(
                "DELETE FROM campaign_audit_events WHERE sequence > ?",
                (retained_events,),
            )
            if clear_candidate:
                connection.execute(
                    "UPDATE campaigns SET state = ?, revision = ?, "
                    "candidate_ref = NULL, artifact_json = NULL, "
                    "cooldown_until = NULL, updated_at = "
                    "(SELECT created_at FROM campaign_audit_events "
                    "ORDER BY sequence DESC LIMIT 1) WHERE campaign_id = ?",
                    (state.value, revision, campaign_id),
                )
            else:
                connection.execute(
                    "UPDATE campaigns SET state = ?, revision = ?, "
                    "cooldown_until = NULL, updated_at = "
                    "(SELECT created_at FROM campaign_audit_events "
                    "ORDER BY sequence DESC LIMIT 1) WHERE campaign_id = ?",
                    (state.value, revision, campaign_id),
                )
            connection.commit()

        repository = SQLiteEvolutionProgramRepository(program_database)
        campaigns = SQLiteCampaignRepository(campaign_database)
        controller = EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(campaigns),
        )
        assert repository.verify_state(g0.program_id) is True
        assert campaigns.verify_audit() is True
        before_program_events = tuple(repository.events())

        submission = controller.submit_generation(
            g1.plan,
            evaluation_actor_id=evaluator,
            submitted_at=g1.plan.created_at,
        )

        assert submission.reused is True
        assert submission.generation.campaign_id == campaign_id
        assert submission.campaign.state == CampaignState.APPROVAL_PENDING
        assert repository.head(g0.program_id).generation_campaign_count == 1
        assert repository.verify_state(g0.program_id) is True
        assert campaigns.verify_audit() is True
        campaign_events = tuple(campaigns.audit_events())
        assert tuple(item.event_type for item in campaign_events) == (
            "campaign_created",
            "candidate_attached",
            "campaign_transitioned",
            "campaign_transitioned",
        )
        assert {
            item.actor_id
            for item in campaign_events[1:]
        } == {evaluator}
        assert "campaign_reused" not in {
            item.event_type for item in campaign_events
        }
        program_events = tuple(repository.events())
        assert len(program_events) == len(before_program_events) + 1
        assert program_events[-1].event_type.value == "generation_campaign_bound"

        retry_program_events = tuple(repository.events())
        retry_campaign_events = tuple(campaigns.audit_events())
        second = controller.submit_generation(
            g1.plan,
            evaluation_actor_id=f"second-retry:{state.value}",
            submitted_at=g1.plan.created_at,
        )
        assert second.reused is True
        assert second.generation == submission.generation
        assert second.campaign == submission.campaign
        assert tuple(repository.events()) == retry_program_events
        assert tuple(campaigns.audit_events()) == retry_campaign_events


def test_exact_completion_recovers_partial_campaign_completion(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="7" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    g1 = package.generations[1]
    assert g1.outcome is not None
    campaign_id = package.generation_campaign.campaign_id

    with sqlite3.connect(lab.campaign_database) as connection:
        connection.execute(
            "DELETE FROM campaign_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM campaign_audit_events)"
        )
        connection.execute(
            "UPDATE campaigns SET state = ?, revision = revision - 1, "
            "updated_at = (SELECT created_at FROM campaign_audit_events "
            "ORDER BY sequence DESC LIMIT 1) WHERE campaign_id = ?",
            (CampaignState.AUTHORIZED.value, campaign_id),
        )
        connection.commit()

    assert campaigns.verify_audit() is True
    assert campaigns.get(campaign_id).state == CampaignState.AUTHORIZED
    before_head = repository.head(g1.program_id)
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    recovered = controller.complete_generation(
        package.passing_release_package,
        program_id=g1.program_id,
        generation_id=g1.generation_id,
        outcome_id=g1.outcome.outcome_id,
        expected_revision=0,
        actor_id="completion-recovery",
        completed_at=g1.outcome.completed_at,
    )

    assert recovered == g1
    assert campaigns.get(campaign_id).state == CampaignState.COMPLETED
    assert repository.head(g1.program_id) == before_head
    assert tuple(repository.events()) == before_program_events
    recovered_events = tuple(campaigns.audit_events())
    assert len(recovered_events) == len(before_campaign_events) + 1
    assert recovered_events[-1].event_type == "campaign_transitioned"
    assert recovered_events[-1].actor_id == "completion-recovery"
    assert recovered_events[-1].payload["from_state"] == "authorized"
    assert recovered_events[-1].payload["to_state"] == "completed"
    assert "partial cross-registry commit" in recovered_events[-1].payload["reason"]
    assert campaigns.verify_audit() is True

    second_before = tuple(campaigns.audit_events())
    second = controller.complete_generation(
        package.passing_release_package,
        program_id=g1.program_id,
        generation_id=g1.generation_id,
        outcome_id=g1.outcome.outcome_id,
        expected_revision=0,
        actor_id="second-completion-retry",
        completed_at=g1.outcome.completed_at,
    )
    assert second == g1
    assert tuple(campaigns.audit_events()) == second_before


def test_conflicting_completion_retry_fails_closed(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="4" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    g1 = package.generations[1]
    assert g1.outcome is not None

    with pytest.raises(ProgramConflictError, match="differs"):
        controller.complete_generation(
            package.passing_release_package,
            program_id=g1.program_id,
            generation_id=g1.generation_id,
            outcome_id="program-outcome:g1:forged-retry",
            expected_revision=0,
            actor_id="completion-retry",
            completed_at=g1.outcome.completed_at,
        )
