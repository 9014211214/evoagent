import json
import shutil
from datetime import datetime

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
    ProgramEventType,
    SQLiteEvolutionProgramRepository,
)


def _source(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="e" * 40,
    )
    result = lab.run()
    return lab, EvolutionProgramPackageManager().load_file(result.package_path)


def _fresh_controller(package, tmp_path):
    g0 = package.generations[0]
    d0 = package.decisions[0]
    plan = package.generations[1].plan
    assert g0.outcome is not None
    assert plan is not None
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
    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id="feedback-ingestor",
        created_at=package.signal.created_at,
    )
    controller.store_attribution(
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
        attribution=package.attribution,
    )
    return controller, repository, campaigns, plan


def test_evaluator_cannot_authorize_start_or_complete(tmp_path):
    _, package = _source(tmp_path)
    controller, repository, campaigns, plan = _fresh_controller(
        package,
        tmp_path,
    )
    outcome = package.generations[1].outcome
    assert outcome is not None
    evaluator = "independent-evaluator"
    submission = controller.submit_generation(
        plan,
        evaluation_actor_id=evaluator,
        submitted_at=plan.created_at,
    )
    campaign = controller.approve_generation(
        submission.campaign.campaign_id,
        actor_id="reviewer-a",
        reason="Independent evidence review passed.",
        expected_revision=submission.campaign.revision,
    )
    campaign = controller.approve_generation(
        campaign.campaign_id,
        actor_id="reviewer-b",
        reason="Independent budget review passed.",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    before_head = repository.head(plan.program_id)
    before_events = tuple(repository.events())
    with pytest.raises(ValueError, match="independent evaluator"):
        controller.synchronize_authorization(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            campaign_id=campaign.campaign_id,
            actor_id=evaluator,
        )
    assert repository.head(plan.program_id) == before_head
    assert tuple(repository.events()) == before_events

    controller.synchronize_authorization(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        actor_id="authorization-actor",
    )
    authorized_head = repository.head(plan.program_id)
    before_events = tuple(repository.events())
    with pytest.raises(ValueError, match="independent evaluator"):
        controller.start_generation(
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            campaign_id=campaign.campaign_id,
            expected_revision=authorized_head.revision,
            actor_id=evaluator,
        )
    assert repository.head(plan.program_id) == authorized_head
    assert tuple(repository.events()) == before_events

    controller.start_generation(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        expected_revision=authorized_head.revision,
        actor_id="generation-operator",
    )
    running_head = repository.head(plan.program_id)
    before_events = tuple(repository.events())
    with pytest.raises(ValueError, match="independent evaluator"):
        controller.complete_generation(
            package.passing_release_package,
            program_id=plan.program_id,
            generation_id=plan.generation_id,
            outcome_id=outcome.outcome_id,
            expected_revision=running_head.revision,
            actor_id=evaluator,
            completed_at=outcome.completed_at,
        )
    assert repository.head(plan.program_id) == running_head
    assert tuple(repository.events()) == before_events
    assert campaigns.get(campaign.campaign_id).state == CampaignState.AUTHORIZED


def _copy_and_rewrite(source, destination, mutate):
    shutil.copyfile(source, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    mutate(payload)
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    destination.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _rehash_program_chain(payload):
    previous_hash = "0" * 64
    for event in payload["program_events"]:
        event["previous_hash"] = previous_hash
        event["event_hash"] = SQLiteEvolutionProgramRepository._event_hash(
            sequence=event["sequence"],
            event_id=event["event_id"],
            program_id=event["program_id"],
            generation_id=event["generation_id"],
            event_type=ProgramEventType(event["event_type"]),
            actor_id=event["actor_id"],
            reason=event["reason"],
            payload=event["payload"],
            created_at=_parse_time(event["created_at"]),
            previous_hash=previous_hash,
        )
        previous_hash = event["event_hash"]
    payload["program_checkpoint"] = {
        "event_count": len(payload["program_events"]),
        "head_hash": previous_hash,
    }


def _rehash_campaign_chain(payload):
    previous_hash = "0" * 64
    for event in payload["campaign_events"]:
        event["previous_hash"] = previous_hash
        event["event_hash"] = SQLiteCampaignRepository._event_hash(
            sequence=event["sequence"],
            event_id=event["event_id"],
            campaign_id=event["campaign_id"],
            event_type=event["event_type"],
            actor_id=event["actor_id"],
            payload=event["payload"],
            created_at=_parse_time(event["created_at"]),
            previous_hash=previous_hash,
        )
        previous_hash = event["event_hash"]
    payload["campaign_checkpoint"] = {
        "event_count": len(payload["campaign_events"]),
        "head_hash": previous_hash,
    }


def test_package_rejects_rehashed_evaluator_as_approver(tmp_path):
    _, package = _source(tmp_path)
    source_path = tmp_path / "source-package.json"
    source_path.write_text(package.model_dump_json(), encoding="utf-8")
    target = tmp_path / "evaluator-approver.json"

    def mutate(payload):
        evaluator = payload["campaign_events"][1]["actor_id"]
        payload["generation_approvals"][0]["actor_id"] = evaluator
        payload["campaign_events"][4]["actor_id"] = evaluator
        _rehash_campaign_chain(payload)

    _copy_and_rewrite(source_path, target, mutate)
    with pytest.raises(
        ValueError,
        match="evaluator also approved|execution actor violates role separation",
    ):
        EvolutionProgramPackageManager().load_file(target)


def test_package_rejects_rehashed_evaluator_as_executor(tmp_path):
    _, package = _source(tmp_path)
    source_path = tmp_path / "source-package.json"
    source_path.write_text(package.model_dump_json(), encoding="utf-8")
    target = tmp_path / "evaluator-executor.json"

    def mutate(payload):
        evaluator = payload["campaign_events"][1]["actor_id"]
        payload["program_events"][7]["actor_id"] = evaluator
        _rehash_program_chain(payload)

    _copy_and_rewrite(source_path, target, mutate)
    with pytest.raises(ValueError, match="evaluator also authorized"):
        EvolutionProgramPackageManager().load_file(target)
