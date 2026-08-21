import json
import shutil

import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    GenerationStatus,
    SQLiteEvolutionProgramRepository,
)


def _source_package(tmp_path):
    return MultiGenerationEvolutionProgramLab(
        tmp_path / "lab",
        source_commit="1" * 40,
    ).run().package_path


def _prepare_pre_submission(package, tmp_path, prefix):
    g0 = package.generations[0]
    d0 = package.decisions[0]
    plan = package.generations[1].plan
    assert g0.outcome is not None
    assert plan is not None

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
    controller.store_attribution(
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
        attribution=package.attribution,
    )
    assert decision == d0
    return controller, repository, campaigns, plan


def test_decision_planning_actor_is_not_an_approver(tmp_path):
    path = _source_package(tmp_path)
    package = EvolutionProgramPackageManager().load_file(path)
    continue_decision = package.decisions[0]
    plan = package.generations[1].plan

    assert continue_decision.action.value == "continue"
    assert plan.created_by == continue_decision.decided_by
    assert continue_decision.decided_by not in {
        item.actor_id for item in package.generation_approvals
    }


def test_feedback_ingestor_and_attribution_writer_are_authenticated(tmp_path):
    path = _source_package(tmp_path)
    package = EvolutionProgramPackageManager().load_file(path)
    g0 = package.generations[0]
    assert g0.outcome is not None

    repository = SQLiteEvolutionProgramRepository(tmp_path / "ingress-program.db")
    campaigns = SQLiteCampaignRepository(tmp_path / "ingress-campaign.db")
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
    before_feedback_events = tuple(repository.events())

    with pytest.raises(ValueError, match="cannot ingest its own"):
        controller.store_feedback(
            package.drift_release_package,
            program_id=g0.program_id,
            generation_index=0,
            signal_id=package.signal.signal_id,
            actor_id=package.signal.evidence_producer_id,
            created_at=package.signal.created_at,
        )
    assert repository.list_signals(g0.program_id) == []
    assert tuple(repository.events()) == before_feedback_events

    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id="independent-feedback-ingestor",
        created_at=package.signal.created_at,
    )
    assert signal == package.signal
    before_attribution_events = tuple(repository.events())

    with pytest.raises(ValueError, match="declared attributor"):
        controller.store_attribution(
            g0.program_id,
            package.attribution,
            actor_id="proxy-attribution-writer",
            created_at=package.attribution.created_at,
        )
    assert repository.list_attributions(g0.program_id) == []
    assert tuple(repository.events()) == before_attribution_events

    stored, reused = controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )
    assert reused is False
    assert stored == package.attribution


def test_generation_evaluator_must_be_independent_before_any_write(tmp_path):
    path = _source_package(tmp_path)
    package = EvolutionProgramPackageManager().load_file(path)
    controller, repository, campaigns, plan = _prepare_pre_submission(
        package,
        tmp_path,
        "evaluator",
    )
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    forbidden = {
        package.signal.evidence_producer_id,
        package.attribution.attributor_id,
        plan.created_by,
        package.decisions[0].decided_by,
    }
    for actor_id in forbidden:
        with pytest.raises(ValueError, match="evaluator must differ"):
            controller.submit_generation(
                plan,
                evaluation_actor_id=actor_id,
                submitted_at=plan.created_at,
            )
        with pytest.raises(KeyError):
            repository.get_generation(plan.program_id, plan.generation_id)
        assert tuple(repository.events()) == before_program_events
        assert tuple(campaigns.audit_events()) == before_campaign_events


def test_evaluator_cannot_approve_and_approvers_cannot_execute(tmp_path):
    path = _source_package(tmp_path)
    package = EvolutionProgramPackageManager().load_file(path)
    controller, repository, campaigns, plan = _prepare_pre_submission(
        package,
        tmp_path,
        "roles",
    )
    g1_outcome = package.generations[1].outcome
    assert g1_outcome is not None
    evaluator = "independent-generation-evaluator"
    submission = controller.submit_generation(
        plan,
        evaluation_actor_id=evaluator,
        submitted_at=plan.created_at,
    )
    assert submission.campaign.state == CampaignState.APPROVAL_PENDING
    before_approval_events = tuple(campaigns.audit_events())

    with pytest.raises(ValueError, match="evaluator.*cannot approve"):
        controller.approve_generation(
            submission.campaign.campaign_id,
            actor_id=evaluator,
            reason="Evaluator must not approve its own result.",
            expected_revision=submission.campaign.revision,
        )
    assert campaigns.approvals(submission.campaign.campaign_id) == []
    assert tuple(campaigns.audit_events()) == before_approval_events

    campaign = controller.approve_generation(
        submission.campaign.campaign_id,
        actor_id="independent-reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=submission.campaign.revision,
    )
    campaign = controller.approve_generation(
        campaign.campaign_id,
        actor_id="independent-reviewer-b",
        reason="Independent budget review passed.",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    before_head = repository.head(plan.program_id)
    before_program_events = tuple(repository.events())
    with pytest.raises(ValueError, match="execution actor must differ"):
        controller.synchronize_authorization(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            campaign_id=campaign.campaign_id,
            actor_id="independent-reviewer-a",
        )
    assert repository.head(plan.program_id) == before_head
    assert tuple(repository.events()) == before_program_events

    controller.synchronize_authorization(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        actor_id="authorization-sync-actor",
    )
    authorized_head = repository.head(plan.program_id)
    before_start_events = tuple(repository.events())
    with pytest.raises(ValueError, match="execution actor must differ"):
        controller.start_generation(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            campaign_id=campaign.campaign_id,
            expected_revision=authorized_head.revision,
            actor_id="independent-reviewer-b",
        )
    assert repository.head(plan.program_id) == authorized_head
    assert tuple(repository.events()) == before_start_events

    controller.start_generation(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        expected_revision=authorized_head.revision,
        actor_id="generation-operator",
    )
    running_head = repository.head(plan.program_id)
    before_completion_events = tuple(repository.events())
    with pytest.raises(ValueError, match="execution actor must differ"):
        controller.complete_generation(
            package.passing_release_package,
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            outcome_id=g1_outcome.outcome_id,
            expected_revision=running_head.revision,
            actor_id="independent-reviewer-a",
            completed_at=g1_outcome.completed_at,
        )
    assert repository.head(plan.program_id) == running_head
    assert tuple(repository.events()) == before_completion_events
    assert repository.get_generation(
        plan.program_id,
        plan.generation_id,
    ).status == GenerationStatus.RUNNING


def test_exact_submission_retry_revalidates_evaluator_independence(tmp_path):
    path = _source_package(tmp_path)
    package = EvolutionProgramPackageManager().load_file(path)
    plan = package.generations[1].plan
    assert plan is not None

    lab_root = tmp_path / "lab"
    repository = SQLiteEvolutionProgramRepository(
        lab_root / "program-registry.db"
    )
    campaigns = SQLiteCampaignRepository(
        lab_root / "program-campaigns.db"
    )
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    for actor_id in {
        package.signal.evidence_producer_id,
        package.attribution.attributor_id,
        plan.created_by,
    }:
        with pytest.raises(ValueError, match="evaluator must differ"):
            controller.submit_generation(
                plan,
                evaluation_actor_id=actor_id,
                submitted_at=plan.created_at,
            )
        assert tuple(repository.events()) == before_program_events
        assert tuple(campaigns.audit_events()) == before_campaign_events


def test_rehashed_decision_actor_substitution_is_rejected(tmp_path):
    source = _source_package(tmp_path)
    destination = tmp_path / "decision-actor.json"
    shutil.copyfile(source, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    decision = payload["decisions"][0]
    decision["decided_by"] = "substituted-decision-actor"
    decision["decision_hash"] = canonical_sha256(
        {key: value for key, value in decision.items() if key != "decision_hash"}
    )
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    destination.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="decision|CONTINUE|plan"):
        EvolutionProgramPackageManager().load_file(destination)
