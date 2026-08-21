import pytest

from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramGate,
    EvolutionProgramPackageError,
    EvolutionProgramPackageManager,
    EvolutionProgramPolicy,
    ProgramHead,
    ProgramState,
    SQLiteEvolutionProgramRepository,
)
from evoagent.program.hashing import program_payload_hash


@pytest.fixture()
def package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "source-lab",
        source_commit="2" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def _disabled_policy(source, field):
    payload = source.model_dump(mode="python", exclude={"policy_hash"})
    payload[field] = False
    return EvolutionProgramPolicy(
        **payload,
        policy_hash=program_payload_hash(payload),
    )


@pytest.mark.parametrize(
    "field",
    (
        "require_independent_attributor",
        "require_single_supported_experiment",
        "require_generation_approvals",
        "safety_feedback_requires_attribution",
    ),
)
def test_public_controller_rejects_disabled_safeguard_before_write(
    package,
    tmp_path,
    field,
):
    repository = SQLiteEvolutionProgramRepository(
        tmp_path / f"{field}-program.db"
    )
    campaigns = SQLiteCampaignRepository(
        tmp_path / f"{field}-campaign.db"
    )
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    program_id = f"program:disabled-policy:{field}"
    g0 = package.generations[0]

    with pytest.raises(ValueError, match="cannot disable"):
        controller.register_from_release(
            package.drift_release_package,
            program_id=program_id,
            policy=_disabled_policy(package.policy, field),
            generation_id=f"generation:disabled-policy:{field}:g0",
            outcome_id=f"outcome:disabled-policy:{field}:g0",
            created_by="program-owner",
            created_at=g0.created_at,
        )

    with pytest.raises(KeyError):
        repository.get_program(program_id)
    assert repository.events() == []


@pytest.mark.parametrize(
    "field",
    (
        "require_independent_attributor",
        "require_single_supported_experiment",
        "require_generation_approvals",
        "safety_feedback_requires_attribution",
    ),
)
def test_public_gate_rejects_rehashed_disabled_safeguard(package, field):
    g0 = package.generations[0]
    assert g0.outcome is not None
    head = ProgramHead(
        program_id=g0.program_id,
        state=ProgramState.RUNNING,
        current_generation_index=0,
        active_generation_id=g0.generation_id,
        revision=0,
        rollback_count=1,
        hold_count=0,
        generation_campaign_count=0,
        total_pairs=g0.outcome.pair_count,
        total_tokens=g0.outcome.total_tokens,
        total_cost_usd=g0.outcome.total_cost_usd,
        updated_at=package.decisions[0].decided_at,
    )

    with pytest.raises(ValueError, match="cannot disable"):
        EvolutionProgramGate().decide(
            policy=_disabled_policy(package.policy, field),
            head=head,
            outcome=g0.outcome,
            decision_id=f"decision:disabled-policy:{field}",
            decided_by="policy-controller",
            decided_at=package.decisions[0].decided_at,
            signal=package.signal,
            attribution=package.attribution,
            consecutive_non_improving_count=1,
        )


@pytest.mark.parametrize(
    "field",
    (
        "require_independent_attributor",
        "require_single_supported_experiment",
        "require_generation_approvals",
        "safety_feedback_requires_attribution",
    ),
)
def test_package_rejects_rehashed_disabled_main_policy(package, field):
    forged = package.model_copy(
        update={"policy": _disabled_policy(package.policy, field)}
    )
    with pytest.raises(
        EvolutionProgramPackageError,
        match="disables a required safeguard",
    ):
        EvolutionProgramPackageManager._verify_hardened_policies(forged)


def test_package_rejects_disabled_control_policy(package):
    forged_control = package.budget_control.model_copy(
        update={
            "policy": _disabled_policy(
                package.budget_control.policy,
                "require_independent_attributor",
            )
        }
    )
    forged = package.model_copy(update={"budget_control": forged_control})

    with pytest.raises(
        EvolutionProgramPackageError,
        match="budget control.*disables a required safeguard",
    ):
        EvolutionProgramPackageManager._verify_hardened_policies(forged)
