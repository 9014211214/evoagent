from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApprovalError,
    CampaignAuditIntegrityError,
    CampaignConflictError,
    CampaignCooldownError,
    CampaignGovernanceService,
    CampaignRisk,
    CampaignState,
    CampaignType,
    InvalidCampaignTransition,
    SQLiteCampaignRepository,
    StaleCampaignRevision,
)


def reserve_skill(governance, *, target="skill:demo@1.0.0", payload=None, generated_by="generator"):
    return governance.reserve(
        campaign_type=CampaignType.SKILL,
        target_key=target,
        fingerprint_source=payload or {"add_rule": "reject_unsafe"},
        risk=CampaignRisk.LOW,
        generated_by=generated_by,
    )


def test_campaign_persists_and_duplicate_fingerprint_reuses_open_campaign(tmp_path):
    path = tmp_path / "campaigns.db"
    first_repo = SQLiteCampaignRepository(path)
    first_governance = CampaignGovernanceService(first_repo)
    reservation = reserve_skill(first_governance)
    attached = first_governance.attach_candidate(
        reservation.campaign,
        candidate_ref="skill:demo@1.1.0",
        artifact_payload={"kind": "skill_candidate", "candidate": {"version": "1.1.0"}},
    )

    reopened = SQLiteCampaignRepository(path)
    second_governance = CampaignGovernanceService(reopened)
    reused = reserve_skill(second_governance)

    assert reused.reused is True
    assert reused.campaign.campaign_id == attached.campaign_id
    assert reused.campaign.artifact_payload["candidate"]["version"] == "1.1.0"
    assert reopened.verify_audit() is True


def test_open_target_rejects_conflicting_fingerprint(tmp_path):
    governance = CampaignGovernanceService(SQLiteCampaignRepository(tmp_path / "campaigns.db"))
    reserve_skill(governance, payload={"rule": "a"})
    with pytest.raises(CampaignConflictError):
        reserve_skill(governance, payload={"rule": "b"})


def test_state_machine_and_optimistic_revision_are_enforced(tmp_path):
    governance = CampaignGovernanceService(SQLiteCampaignRepository(tmp_path / "campaigns.db"))
    reservation = reserve_skill(governance)
    candidate = governance.attach_candidate(
        reservation.campaign,
        candidate_ref="skill:demo@1.1.0",
        artifact_payload={"kind": "skill_candidate"},
    )

    with pytest.raises(StaleCampaignRevision):
        governance.repository.transition(
            candidate.campaign_id,
            to_state=CampaignState.EVALUATION_PENDING,
            expected_revision=0,
            actor_id="evaluator",
            reason="stale",
        )
    with pytest.raises(InvalidCampaignTransition):
        governance.repository.transition(
            candidate.campaign_id,
            to_state=CampaignState.COMPLETED,
            expected_revision=candidate.revision,
            actor_id="evaluator",
            reason="skip gates",
        )


def test_high_risk_model_requires_two_distinct_non_generator_approvers(tmp_path):
    governance = CampaignGovernanceService(SQLiteCampaignRepository(tmp_path / "campaigns.db"))
    reservation = governance.reserve(
        campaign_type=CampaignType.MODEL,
        target_key="model:public/model-v0:planning",
        fingerprint_source={"method": "sft", "budget": 10},
        risk=CampaignRisk.HIGH,
        generated_by="model-generator",
    )
    candidate = governance.attach_candidate(
        reservation.campaign,
        candidate_ref="model-candidate://planning",
        artifact_payload={"kind": "model_candidate"},
    )
    pending = governance.submit_evaluation(
        candidate.campaign_id,
        passed=True,
        expected_revision=candidate.revision,
        actor_id="independent-evaluator",
        reason="held-out and regression suites passed",
    )

    assert pending.state == CampaignState.APPROVAL_PENDING
    assert pending.required_approvals == 2
    with pytest.raises(CampaignApprovalError):
        governance.approve(
            pending.campaign_id,
            actor_id="model-generator",
            decision=ApprovalDecision.APPROVE,
            reason="self approval",
            expected_revision=pending.revision,
        )

    first = governance.approve(
        pending.campaign_id,
        actor_id="reviewer-a",
        decision=ApprovalDecision.APPROVE,
        reason="risk review passed",
        expected_revision=pending.revision,
    )
    assert first.state == CampaignState.APPROVAL_PENDING

    with pytest.raises(CampaignApprovalError):
        governance.approve(
            first.campaign_id,
            actor_id="reviewer-a",
            decision=ApprovalDecision.APPROVE,
            reason="duplicate",
            expected_revision=first.revision,
        )

    second = governance.approve(
        first.campaign_id,
        actor_id="reviewer-b",
        decision=ApprovalDecision.APPROVE,
        reason="security review passed",
        expected_revision=first.revision,
    )
    assert second.state == CampaignState.AUTHORIZED
    assert second.state != CampaignState.COMPLETED
    assert len(governance.repository.approvals(second.campaign_id)) == 2


def test_rejected_campaign_enforces_cooldown(tmp_path):
    repo = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    reservation = repo.reserve_campaign(
        campaign_type=CampaignType.SKILL,
        target_key="skill:cooldown@1.0.0",
        fingerprint="f" * 64,
        risk=CampaignRisk.LOW,
        generated_by="generator",
        required_approvals=1,
        now=now,
    )
    candidate = repo.attach_candidate(
        reservation.campaign.campaign_id,
        candidate_ref="skill:cooldown@1.1.0",
        artifact_payload={"kind": "skill_candidate"},
        expected_revision=reservation.campaign.revision,
        actor_id="generator",
        now=now,
    )
    repo.transition(
        candidate.campaign_id,
        to_state=CampaignState.REJECTED,
        expected_revision=candidate.revision,
        actor_id="evaluator",
        reason="regression",
        cooldown_seconds=60,
        now=now,
    )

    with pytest.raises(CampaignCooldownError):
        repo.reserve_campaign(
            campaign_type=CampaignType.SKILL,
            target_key="skill:cooldown@1.0.0",
            fingerprint="f" * 64,
            risk=CampaignRisk.LOW,
            generated_by="generator",
            required_approvals=1,
            now=now + timedelta(seconds=30),
        )

    reopened = repo.reserve_campaign(
        campaign_type=CampaignType.SKILL,
        target_key="skill:cooldown@1.0.0",
        fingerprint="f" * 64,
        risk=CampaignRisk.LOW,
        generated_by="generator",
        required_approvals=1,
        now=now + timedelta(seconds=61),
    )
    assert reopened.reused is False


def test_model_evidence_survives_restart_and_requires_distinct_tasks(tmp_path):
    path = tmp_path / "campaigns.db"
    repo = SQLiteCampaignRepository(path)
    for index in range(1, 3):
        snapshot = repo.add_model_evidence(
            base_model_id="public/model-v0",
            problem_cluster="planning",
            trace_id=f"trace:{index}",
            task_id="same-task",
            trust_level="verified",
            minimum_traces=3,
            minimum_distinct_tasks=3,
        )
    assert snapshot.ready is False
    assert len(snapshot.task_ids) == 1

    restarted = SQLiteCampaignRepository(path)
    restarted.add_model_evidence(
        base_model_id="public/model-v0",
        problem_cluster="planning",
        trace_id="trace:3",
        task_id="task:2",
        trust_level="verified",
        minimum_traces=3,
        minimum_distinct_tasks=3,
    )
    final = restarted.add_model_evidence(
        base_model_id="public/model-v0",
        problem_cluster="planning",
        trace_id="trace:4",
        task_id="task:3",
        trust_level="verified",
        minimum_traces=3,
        minimum_distinct_tasks=3,
    )
    assert final.ready is True
    assert final.trace_ids == ("trace:1", "trace:2", "trace:3", "trace:4")
    assert final.task_ids == ("same-task", "task:2", "task:3")


def test_audit_tampering_and_tail_truncation_are_detected(tmp_path):
    path = tmp_path / "campaigns.db"
    repo = SQLiteCampaignRepository(path)
    governance = CampaignGovernanceService(repo)
    reservation = reserve_skill(governance)
    governance.attach_candidate(
        reservation.campaign,
        candidate_ref="skill:demo@1.1.0",
        artifact_payload={"kind": "skill_candidate"},
    )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE campaign_audit_events SET payload_json = ? WHERE sequence = 1",
            ('{"tampered":true}',),
        )
        connection.commit()
    with pytest.raises(CampaignAuditIntegrityError):
        repo.verify_audit()

    second_path = tmp_path / "tail.db"
    second = SQLiteCampaignRepository(second_path)
    second_governance = CampaignGovernanceService(second)
    second_reservation = reserve_skill(second_governance)
    second_governance.attach_candidate(
        second_reservation.campaign,
        candidate_ref="skill:demo@1.1.0",
        artifact_payload={"kind": "skill_candidate"},
    )
    checkpoint = second.checkpoint()
    with sqlite3.connect(second_path) as connection:
        connection.execute(
            "DELETE FROM campaign_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM campaign_audit_events)"
        )
        connection.commit()
    assert second.verify_audit() is True
    with pytest.raises(CampaignAuditIntegrityError):
        second.verify_audit(checkpoint)
