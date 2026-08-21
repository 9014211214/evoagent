from datetime import timedelta

import pytest

from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramPackageManager,
    EvolutionProgramPackageError,
)


_RECOVERY_REASON = (
    "Recovered exact completed generation after partial cross-registry commit."
)


def _package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="3" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def _recovered_package(package, *, offset_seconds=1):
    generation_completion = package.program_events[9]
    completion = package.campaign_events[6]
    recovery_time = generation_completion.created_at + timedelta(
        seconds=offset_seconds
    )
    recovered_completion = completion.model_copy(
        update={
            "actor_id": "independent-campaign-recovery",
            "created_at": recovery_time,
            "payload": {
                **completion.payload,
                "reason": _RECOVERY_REASON,
            },
        }
    )
    events = (*package.campaign_events[:6], recovered_completion)
    campaign = package.generation_campaign.model_copy(
        update={"updated_at": recovery_time}
    )
    return package.model_copy(
        update={
            "campaign_events": events,
            "generation_campaign": campaign,
        }
    )


def test_package_role_gate_accepts_independent_recovery_actor(tmp_path):
    package = _package(tmp_path)
    recovered_package = _recovered_package(package)

    EvolutionProgramPackageManager._verify_evaluator_role_separation(
        recovered_package
    )


def test_package_chronology_accepts_recovery_before_final_decision(tmp_path):
    package = _package(tmp_path)
    recovered_package = _recovered_package(package)

    EvolutionProgramPackageManager._verify_causal_chronology(
        recovered_package
    )


def test_package_role_gate_rejects_recovery_before_generation_completion(tmp_path):
    package = _package(tmp_path)
    recovered_package = _recovered_package(package, offset_seconds=-1)

    with pytest.raises(
        EvolutionProgramPackageError,
        match="predates Generation completion",
    ):
        EvolutionProgramPackageManager._verify_evaluator_role_separation(
            recovered_package
        )


def test_package_chronology_rejects_recovery_after_final_decision(tmp_path):
    package = _package(tmp_path)
    completion = package.campaign_events[6]
    recovery_time = package.decisions[1].decided_at + timedelta(seconds=1)
    recovered_completion = completion.model_copy(
        update={
            "actor_id": "independent-campaign-recovery",
            "created_at": recovery_time,
            "payload": {
                **completion.payload,
                "reason": _RECOVERY_REASON,
            },
        }
    )
    recovered_package = package.model_copy(
        update={
            "campaign_events": (
                *package.campaign_events[:6],
                recovered_completion,
            ),
            "generation_campaign": package.generation_campaign.model_copy(
                update={"updated_at": recovery_time}
            ),
        }
    )

    with pytest.raises(
        EvolutionProgramPackageError,
        match="decision predates recovered",
    ):
        EvolutionProgramPackageManager._verify_causal_chronology(
            recovered_package
        )


def test_package_role_gate_requires_one_exact_evaluator(tmp_path):
    package = _package(tmp_path)
    substituted = package.campaign_events[2].model_copy(
        update={"actor_id": "second-evaluator"}
    )
    forged = package.model_copy(
        update={
            "campaign_events": (
                package.campaign_events[0],
                package.campaign_events[1],
                substituted,
                *package.campaign_events[3:],
            )
        }
    )

    with pytest.raises(
        EvolutionProgramPackageError,
        match="one exact independent evaluator",
    ):
        EvolutionProgramPackageManager._verify_evaluator_role_separation(
            forged
        )


def test_package_role_gate_binds_program_campaign_event_to_evaluator(tmp_path):
    package = _package(tmp_path)
    substituted = package.program_events[6].model_copy(
        update={"actor_id": "substituted-binding-actor"}
    )
    forged = package.model_copy(
        update={
            "program_events": (
                *package.program_events[:6],
                substituted,
                *package.program_events[7:],
            )
        }
    )

    with pytest.raises(
        EvolutionProgramPackageError,
        match="binding actor differs",
    ):
        EvolutionProgramPackageManager._verify_evaluator_role_separation(
            forged
        )
