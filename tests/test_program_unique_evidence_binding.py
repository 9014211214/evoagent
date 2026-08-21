import sqlite3

import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import (
    EvolutionProgramPackageManager,
    ProgramConflictError,
    SQLiteEvolutionProgramRepository,
    build_attribution_receipt,
)
from evoagent.program.controller_evidence_hardened_final import (
    RetryHardenedEvolutionProgramController,
)
from evoagent.program.hashing import program_payload_hash


def _source_package(tmp_path, name):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / name,
        source_commit="6" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def _fresh_controller(package, tmp_path, name):
    g0 = package.generations[0]
    assert g0.outcome is not None
    repository = SQLiteEvolutionProgramRepository(
        tmp_path / f"{name}-program.db"
    )
    campaigns = SQLiteCampaignRepository(
        tmp_path / f"{name}-campaign.db"
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
    return controller, repository, campaigns, g0, signal, attribution


def test_controller_rejects_second_signal_before_decision(tmp_path):
    package = _source_package(tmp_path, "signal-source")
    controller, repository, _, g0, _, _ = _fresh_controller(
        package,
        tmp_path,
        "signal",
    )
    before_events = tuple(repository.events())

    with pytest.raises(
        ProgramConflictError,
        match="different immutable feedback",
    ):
        controller.store_feedback(
            package.drift_release_package,
            program_id=g0.program_id,
            generation_index=0,
            signal_id="program-signal:g0:second",
            actor_id="second-feedback-ingestor",
            created_at=package.signal.created_at,
        )
    assert tuple(repository.events()) == before_events


def test_controller_rejects_new_feedback_or_attribution_after_decision(tmp_path):
    package = _source_package(tmp_path, "post-decision-source")
    controller, repository, _, g0, signal, attribution = _fresh_controller(
        package,
        tmp_path,
        "post-decision",
    )
    decision, _ = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=package.decisions[0].decision_id,
        decided_by=package.decisions[0].decided_by,
        decided_at=package.decisions[0].decided_at,
        signal=signal,
        attribution=attribution,
    )
    assert decision == package.decisions[0]
    before_events = tuple(repository.events())

    with pytest.raises(ValueError, match="cannot accept new feedback after"):
        controller.store_feedback(
            package.drift_release_package,
            program_id=g0.program_id,
            generation_index=0,
            signal_id="program-signal:g0:late",
            actor_id="late-feedback-ingestor",
            created_at=package.decisions[0].decided_at,
        )

    late_attribution = build_attribution_receipt(
        signal,
        receipt_id="program-attribution:g0:late",
        failure_layer=FailureLayer.CONTEXT,
        action=EvolutionAction.UPDATE_CONTEXT,
        confidence=1.0,
        supported_experiment_hashes=(
            canonical_sha256({"experiment": "late-context"}),
        ),
        attributor_id="late-independent-attributor",
        created_at=package.decisions[0].decided_at,
    )
    with pytest.raises(ValueError, match="cannot accept new attribution after"):
        controller.store_attribution(
            g0.program_id,
            late_attribution,
            actor_id=late_attribution.attributor_id,
            created_at=late_attribution.created_at,
        )
    assert tuple(repository.events()) == before_events


def test_decision_rejects_repository_bypass_with_second_signal(tmp_path):
    package = _source_package(tmp_path, "bypass-signal-source")
    controller, repository, _, g0, signal, attribution = _fresh_controller(
        package,
        tmp_path,
        "bypass-signal",
    )
    second = controller.feedback.extract(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id="program-signal:g0:repository-bypass",
        created_at=package.signal.created_at,
    )
    repository.store_signal(
        second,
        actor_id="repository-bypass-ingestor",
        reason="direct Registry bypass fixture",
        now=second.created_at,
    )
    before_decisions = tuple(repository.list_decisions(g0.program_id))

    with pytest.raises(ValueError, match="one immutable generation signal"):
        controller.decide(
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            decision_id=package.decisions[0].decision_id,
            decided_by=package.decisions[0].decided_by,
            decided_at=package.decisions[0].decided_at,
            signal=signal,
            attribution=attribution,
        )
    assert tuple(repository.list_decisions(g0.program_id)) == before_decisions


def test_generation_plan_rejects_repository_bypass_with_second_attribution(
    tmp_path,
):
    package = _source_package(tmp_path, "bypass-attribution-source")
    controller, repository, campaigns, g0, signal, attribution = _fresh_controller(
        package,
        tmp_path,
        "bypass-attribution",
    )
    decision, _ = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=package.decisions[0].decision_id,
        decided_by=package.decisions[0].decided_by,
        decided_at=package.decisions[0].decided_at,
        signal=signal,
        attribution=attribution,
    )
    assert decision == package.decisions[0]
    second = build_attribution_receipt(
        signal,
        receipt_id="program-attribution:g0:repository-bypass",
        failure_layer=FailureLayer.CONTEXT,
        action=EvolutionAction.UPDATE_CONTEXT,
        confidence=1.0,
        supported_experiment_hashes=(
            canonical_sha256({"experiment": "bypass-context"}),
        ),
        attributor_id="repository-bypass-attributor",
        created_at=package.attribution.created_at,
    )
    repository.store_attribution(
        g0.program_id,
        second,
        actor_id=second.attributor_id,
        reason="direct Registry bypass fixture",
        now=second.created_at,
    )
    plan = package.generations[1].plan
    assert plan is not None
    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())

    with pytest.raises(ValueError, match="unique decision evidence set"):
        controller.submit_generation(
            plan,
            evaluation_actor_id="independent-generation-evaluator",
            submitted_at=plan.created_at,
        )
    with pytest.raises(KeyError):
        repository.get_generation(plan.program_id, plan.generation_id)
    assert tuple(repository.events()) == before_program_events
    assert tuple(campaigns.audit_events()) == before_campaign_events


def test_completed_generation_retry_revalidates_completed_campaign(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "completed-campaign-lab",
        source_commit="7" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    g1 = package.generations[1]
    assert g1.outcome is not None
    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    controller = RetryHardenedEvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )

    with sqlite3.connect(lab.campaign_database) as connection:
        connection.execute(
            "UPDATE campaigns SET metadata_json = ? WHERE campaign_id = ?",
            ("{}", package.generation_campaign.campaign_id),
        )
        connection.commit()

    before_program_events = tuple(repository.events())
    before_campaign_events = tuple(campaigns.audit_events())
    with pytest.raises(ValueError, match="differs from exact Program evidence"):
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
