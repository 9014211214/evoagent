import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab import ShadowCanaryReleaseLab
from evoagent.release import (
    ReleaseEvidencePackageManager,
    ReleaseLifecycleService,
    ReleaseState,
    SQLiteReleaseRegistry,
    StaleReleaseRevision,
)


def _source_package(tmp_path):
    result = ShadowCanaryReleaseLab(
        tmp_path / "source-lab", source_commit="1" * 40
    ).run()
    return ReleaseEvidencePackageManager().load_file(result.drift.package_path)


def _fresh_lifecycle(tmp_path):
    campaigns = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    registry = SQLiteReleaseRegistry(tmp_path / "release.db")
    lifecycle = ReleaseLifecycleService(
        registry=registry,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    return lifecycle, registry, campaigns


def test_release_campaign_authorization_does_not_start_shadow(tmp_path):
    package = _source_package(tmp_path)
    lifecycle, registry, _ = _fresh_lifecycle(tmp_path / "fresh")
    submission = lifecycle.submit_plan(package.champion_package, package.plan)

    assert submission.head.state == ReleaseState.PLANNED
    assert submission.head.active_stage_id is None
    assert submission.head.candidate_allocation_percent == 0.0
    assert submission.head.primary_snapshot_id == package.plan.incumbent_snapshot_id

    with pytest.raises(ValueError, match="creator"):
        lifecycle.approve_release(
            submission.campaign.campaign_id,
            actor_id=package.plan.created_by,
            reason="self approval",
            expected_revision=submission.campaign.revision,
        )

    campaign = lifecycle.approve_release(
        submission.campaign.campaign_id,
        actor_id="fresh-release-reviewer-a",
        reason="release evidence contract reviewed",
        expected_revision=submission.campaign.revision,
    )
    campaign = lifecycle.approve_release(
        campaign.campaign_id,
        actor_id="fresh-release-reviewer-b",
        reason="release safety contract reviewed",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    authorized = lifecycle.synchronize_release_authorization(
        plan_id=package.plan.plan_id,
        campaign_id=campaign.campaign_id,
        actor_id="fresh-release-authorization-sync",
    )
    assert authorized.state == ReleaseState.AUTHORIZED
    assert authorized.active_stage_id is None
    assert authorized.candidate_allocation_percent == 0.0
    assert authorized.primary_snapshot_id == package.plan.incumbent_snapshot_id

    with pytest.raises(StaleReleaseRevision):
        lifecycle.start_shadow(
            plan_id=package.plan.plan_id,
            campaign_id=campaign.campaign_id,
            expected_revision=authorized.revision - 1,
            actor_id="fresh-stage-operator",
        )
    shadow = lifecycle.start_shadow(
        plan_id=package.plan.plan_id,
        campaign_id=campaign.campaign_id,
        expected_revision=authorized.revision,
        actor_id="fresh-stage-operator",
    )
    assert shadow.state == ReleaseState.SHADOW
    assert shadow.active_stage_id == "shadow"
    assert shadow.candidate_allocation_percent == 0.0
    assert registry.head(package.plan.plan_id) == shadow


def test_rollback_forbids_decision_actor_and_evidence_producer(tmp_path):
    package = _source_package(tmp_path)
    lifecycle, registry, campaigns = _fresh_lifecycle(tmp_path / "fresh")
    submission = lifecycle.submit_plan(package.champion_package, package.plan)
    campaign = lifecycle.approve_release(
        submission.campaign.campaign_id,
        actor_id="fresh-release-reviewer-a",
        reason="release evidence contract reviewed",
        expected_revision=submission.campaign.revision,
    )
    campaign = lifecycle.approve_release(
        campaign.campaign_id,
        actor_id="fresh-release-reviewer-b",
        reason="release safety contract reviewed",
        expected_revision=campaign.revision,
    )
    head = lifecycle.synchronize_release_authorization(
        plan_id=package.plan.plan_id,
        campaign_id=campaign.campaign_id,
        actor_id="fresh-release-authorization-sync",
    )
    head = lifecycle.start_shadow(
        plan_id=package.plan.plan_id,
        campaign_id=campaign.campaign_id,
        expected_revision=head.revision,
        actor_id="fresh-stage-operator",
    )

    batches = {item.stage_id: item for item in package.batches}
    stored_decisions = {item.stage_id: item for item in package.decisions}
    final_evaluation = None
    for stage in package.plan.stages:
        original = stored_decisions[stage.stage_id]
        evaluation = lifecycle.evaluate_stage(
            package.plan.plan_id,
            batches[stage.stage_id],
            assessment_id=f"fresh:{stage.stage_id}:assessment",
            decision_id=f"fresh:{stage.stage_id}:decision",
            decision_actor_id=original.decision_actor_id,
            decided_at=original.decided_at,
        )
        if stage.stage_id != package.plan.stages[-1].stage_id:
            head = lifecycle.advance(
                evaluation.decision.decision_id,
                expected_revision=head.revision,
                actor_id="fresh-stage-operator",
            )
        else:
            final_evaluation = evaluation

    assert final_evaluation is not None
    assert final_evaluation.decision.action.value == "rollback"
    rollback = lifecycle.submit_rollback(
        package.champion_package,
        final_evaluation.decision.decision_id,
        expected_revision=head.revision,
        actor_id=final_evaluation.decision.decision_actor_id,
    )

    with pytest.raises(ValueError, match="decision actor"):
        lifecycle.approve_rollback(
            rollback.campaign.campaign_id,
            actor_id=final_evaluation.decision.decision_actor_id,
            reason="self approval",
            expected_revision=rollback.campaign.revision,
        )
    with pytest.raises(ValueError, match="evidence producer"):
        lifecycle.approve_rollback(
            rollback.campaign.campaign_id,
            actor_id=final_evaluation.decision.evidence_producer_id,
            reason="producer approval",
            expected_revision=rollback.campaign.revision,
        )

    rollback_campaign = lifecycle.approve_rollback(
        rollback.campaign.campaign_id,
        actor_id="fresh-rollback-reviewer-a",
        reason="protected-segment evidence reviewed",
        expected_revision=rollback.campaign.revision,
    )
    rollback_campaign = lifecycle.approve_rollback(
        rollback_campaign.campaign_id,
        actor_id="fresh-rollback-reviewer-b",
        reason="rollback safety review passed",
        expected_revision=rollback_campaign.revision,
    )
    assert rollback_campaign.state == CampaignState.AUTHORIZED
    before = registry.head(package.plan.plan_id)
    assert before.state == ReleaseState.ROLLBACK_RECOMMENDED
    assert before.candidate_allocation_percent == 25.0

    with pytest.raises(StaleReleaseRevision):
        lifecycle.execute_rollback(
            plan_id=package.plan.plan_id,
            decision_id=final_evaluation.decision.decision_id,
            campaign_id=rollback_campaign.campaign_id,
            expected_revision=before.revision - 1,
            actor_id="fresh-rollback-operator",
        )
    rolled_back = lifecycle.execute_rollback(
        plan_id=package.plan.plan_id,
        decision_id=final_evaluation.decision.decision_id,
        campaign_id=rollback_campaign.campaign_id,
        expected_revision=before.revision,
        actor_id="fresh-rollback-operator",
    )
    assert rolled_back.state == ReleaseState.ROLLED_BACK
    assert rolled_back.primary_snapshot_id == package.plan.incumbent_snapshot_id
    assert rolled_back.active_stage_id is None
    assert rolled_back.candidate_allocation_percent == 0.0
    assert campaigns.get(rollback_campaign.campaign_id).state == CampaignState.COMPLETED