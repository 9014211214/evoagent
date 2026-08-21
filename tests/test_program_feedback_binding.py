from datetime import timedelta

import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    AttributionReceipt,
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    SQLiteEvolutionProgramRepository,
    build_attribution_receipt,
)
from evoagent.program.hashing import program_payload_hash


def _controller(tmp_path, prefix):
    repository = SQLiteEvolutionProgramRepository(
        tmp_path / f"{prefix}-program.db"
    )
    campaigns = SQLiteCampaignRepository(
        tmp_path / f"{prefix}-campaign.db"
    )
    return (
        EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(campaigns),
        ),
        repository,
        campaigns,
    )


def _package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="1" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def test_registration_time_cannot_precede_release_package(tmp_path):
    package = _package(tmp_path)
    controller, repository, _ = _controller(tmp_path, "registration")
    program_id = "program:registration-time-control"

    with pytest.raises(ValueError, match="precedes its verified release package"):
        controller.register_from_release(
            package.drift_release_package,
            program_id=program_id,
            policy=package.policy,
            generation_id="generation:registration-time-control:g0",
            outcome_id="outcome:registration-time-control:g0",
            created_by="program-owner",
            created_at=(
                package.drift_release_package.created_at
                - timedelta(seconds=1)
            ),
        )

    with pytest.raises(KeyError):
        repository.get_program(program_id)
    assert repository.events() == []


def test_feedback_requires_exact_observed_release_package(tmp_path):
    package = _package(tmp_path)
    controller, repository, _ = _controller(tmp_path, "feedback")
    program_id = "program:feedback-binding-control"
    generation_id = "generation:feedback-binding-control:g0"
    observed_at = package.generations[0].created_at

    controller.register_from_release(
        package.drift_release_package,
        program_id=program_id,
        policy=package.policy,
        generation_id=generation_id,
        outcome_id="outcome:feedback-binding-control:g0",
        created_by="program-owner",
        created_at=observed_at,
    )
    before_events = tuple(repository.events())

    with pytest.raises(ValueError, match="differs from the observed generation"):
        controller.store_feedback(
            package.passing_release_package,
            program_id=program_id,
            generation_index=0,
            signal_id="signal:wrong-release-package",
            actor_id="feedback-ingestor",
            created_at=observed_at + timedelta(seconds=1),
        )
    assert repository.list_signals(program_id) == []
    assert tuple(repository.events()) == before_events

    with pytest.raises(ValueError, match="precedes its observed release evidence"):
        controller.store_feedback(
            package.drift_release_package,
            program_id=program_id,
            generation_index=0,
            signal_id="signal:premature-feedback",
            actor_id="feedback-ingestor",
            created_at=observed_at - timedelta(seconds=1),
        )
    assert repository.list_signals(program_id) == []
    assert tuple(repository.events()) == before_events

    signal, reused = controller.store_feedback(
        package.drift_release_package,
        program_id=program_id,
        generation_index=0,
        signal_id="signal:exact-observed-release",
        actor_id="feedback-ingestor",
        created_at=observed_at + timedelta(seconds=1),
    )
    assert reused is False
    assert signal.source_release_package_hash == (
        package.drift_release_package.package_hash
    )


def test_attribution_time_and_storage_time_are_immutable(tmp_path):
    package = _package(tmp_path)
    controller, repository, _ = _controller(tmp_path, "attribution")
    program_id = "program:attribution-time-control"
    observed_at = package.generations[0].created_at

    controller.register_from_release(
        package.drift_release_package,
        program_id=program_id,
        policy=package.policy,
        generation_id="generation:attribution-time-control:g0",
        outcome_id="outcome:attribution-time-control:g0",
        created_by="program-owner",
        created_at=observed_at,
    )
    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=program_id,
        generation_index=0,
        signal_id="signal:attribution-time-control",
        actor_id="feedback-ingestor",
        created_at=observed_at + timedelta(seconds=1),
    )
    attribution = build_attribution_receipt(
        signal,
        receipt_id="attribution:time-control",
        failure_layer=FailureLayer.CONTEXT,
        action=EvolutionAction.UPDATE_CONTEXT,
        confidence=1.0,
        supported_experiment_hashes=("a" * 64,),
        attributor_id="independent-attributor",
        created_at=signal.created_at + timedelta(seconds=1),
    )
    before_events = tuple(repository.events())

    with pytest.raises(ValueError, match="storage time differs"):
        controller.store_attribution(
            program_id,
            attribution,
            actor_id=attribution.attributor_id,
            created_at=attribution.created_at + timedelta(seconds=1),
        )
    assert repository.list_attributions(program_id) == []
    assert tuple(repository.events()) == before_events

    premature_payload = attribution.model_dump(
        mode="python",
        exclude={"receipt_hash"},
    )
    premature_payload.update(
        {
            "receipt_id": "attribution:premature-control",
            "supported_experiment_hashes": ("b" * 64,),
            "created_at": signal.created_at - timedelta(seconds=1),
        }
    )
    premature = AttributionReceipt(
        **premature_payload,
        receipt_hash=program_payload_hash(premature_payload),
    )
    with pytest.raises(ValueError, match="precedes its persisted learning signal"):
        controller.store_attribution(
            program_id,
            premature,
            actor_id=premature.attributor_id,
            created_at=premature.created_at,
        )
    assert repository.list_attributions(program_id) == []
    assert tuple(repository.events()) == before_events

    stored, reused = controller.store_attribution(
        program_id,
        attribution,
        actor_id=attribution.attributor_id,
        created_at=attribution.created_at,
    )
    assert reused is False
    assert stored == attribution
