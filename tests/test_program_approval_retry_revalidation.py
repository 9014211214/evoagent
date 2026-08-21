import sqlite3

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


def _completed_lab(tmp_path, name, source_commit):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / name,
        source_commit=source_commit,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    return lab, package, repository, campaigns, controller


def test_exact_approval_retry_revalidates_completed_campaign(tmp_path):
    lab, package, repository, campaigns, controller = _completed_lab(
        tmp_path,
        "program-lab",
        "8" * 40,
    )
    approval = package.generation_approvals[0]

    with sqlite3.connect(lab.campaign_database) as connection:
        connection.execute(
            "UPDATE campaigns SET artifact_json = ? WHERE campaign_id = ?",
            ("{}", approval.campaign_id),
        )
        connection.commit()

    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())
    with pytest.raises(ValueError, match="lacks Evolution Generation evidence"):
        controller.approve_generation(
            approval.campaign_id,
            actor_id=approval.actor_id,
            reason=approval.reason,
            expected_revision=0,
        )
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_exact_approval_retry_rejects_coherently_rehashed_audit_reason(tmp_path):
    lab, package, repository, campaigns, controller = _completed_lab(
        tmp_path,
        "audit-rehash-lab",
        "9" * 40,
    )
    approval = package.generation_approvals[0]
    events = tuple(campaigns.audit_events())
    target = next(
        item
        for item in events
        if item.campaign_id == approval.campaign_id
        and item.event_type == "approval_recorded"
        and item.actor_id == approval.actor_id
    )

    previous_hash = "0" * 64
    with sqlite3.connect(lab.campaign_database) as connection:
        for event in events:
            payload = dict(event.payload)
            if event.sequence == target.sequence:
                payload["reason"] = "coherently forged approval rationale"
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
    with pytest.raises(ValueError, match="approval audit identity, reason"):
        controller.approve_generation(
            approval.campaign_id,
            actor_id=approval.actor_id,
            reason=approval.reason,
            expected_revision=0,
        )
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_completed_generation_retry_rejects_campaign_audit_tail_truncation(
    tmp_path,
):
    lab, package, repository, campaigns, controller = _completed_lab(
        tmp_path,
        "tail-truncation-lab",
        "a" * 40,
    )
    g1 = package.generations[1]
    assert g1.outcome is not None

    with sqlite3.connect(lab.campaign_database) as connection:
        connection.execute(
            "DELETE FROM campaign_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM campaign_audit_events)"
        )
        connection.commit()

    assert campaigns.verify_audit() is True
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())
    with pytest.raises(ValueError, match="audit lifecycle is missing"):
        controller.complete_generation(
            package.passing_release_package,
            program_id=g1.program_id,
            generation_id=g1.generation_id,
            outcome_id=g1.outcome.outcome_id,
            expected_revision=0,
            actor_id="completion-retry",
            completed_at=g1.outcome.completed_at,
        )
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_completed_retry_rejects_coherently_rehashed_program_completion_payload(
    tmp_path,
):
    lab, package, repository, campaigns, controller = _completed_lab(
        tmp_path,
        "program-completion-rehash-lab",
        "b" * 40,
    )
    g1 = package.generations[1]
    assert g1.outcome is not None
    events = tuple(repository.events())
    target = next(
        item
        for item in events
        if item.event_type.value == "generation_completed"
        and item.generation_id == g1.generation_id
    )

    previous_hash = "0" * 64
    with sqlite3.connect(lab.program_database) as connection:
        for event in events:
            payload = dict(event.payload)
            if event.sequence == target.sequence:
                payload["release_package_hash"] = "0" * 64
            event_hash = SQLiteEvolutionProgramRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                program_id=event.program_id,
                generation_id=event.generation_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                reason=event.reason,
                payload=payload,
                created_at=event.created_at,
                previous_hash=previous_hash,
            )
            connection.execute(
                "UPDATE program_audit_events SET payload_json = ?, "
                "previous_hash = ?, event_hash = ? WHERE sequence = ?",
                (
                    SQLiteEvolutionProgramRepository._json(payload),
                    previous_hash,
                    event_hash,
                    event.sequence,
                ),
            )
            previous_hash = event_hash
        connection.commit()

    assert repository.verify_audit() is True
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())
    with pytest.raises(ValueError, match="completion audit differs"):
        controller.complete_generation(
            package.passing_release_package,
            program_id=g1.program_id,
            generation_id=g1.generation_id,
            outcome_id=g1.outcome.outcome_id,
            expected_revision=0,
            actor_id="completion-retry",
            completed_at=g1.outcome.completed_at,
        )
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events
