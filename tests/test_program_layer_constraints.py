from datetime import datetime, timezone

import pytest

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import (
    EvolutionProgramGate,
    EvolutionProgramPackageManager,
    EvolutionProgramPolicy,
    ProgramAction,
    ProgramHead,
    ProgramState,
    build_program_policy,
)


@pytest.mark.parametrize(
    "layer",
    (
        FailureLayer.MODEL,
        FailureLayer.ENVIRONMENT,
        FailureLayer.SAFETY,
        FailureLayer.UNKNOWN,
        FailureLayer.NONE,
    ),
)
def test_policy_builder_rejects_non_bounded_automatic_layers(layer):
    with pytest.raises(ValueError, match="limited to Skill"):
        build_program_policy(
            policy_id=f"policy:unsupported:{layer.value}",
            allowed_automatic_layers=(layer,),
        )


def _forged_safety_policy(package):
    policy_payload = package.policy.model_dump(mode="json", exclude={"policy_hash"})
    policy_payload["allowed_automatic_layers"] = [
        *policy_payload["allowed_automatic_layers"],
        FailureLayer.SAFETY.value,
    ]
    return EvolutionProgramPolicy(
        **policy_payload,
        policy_hash=canonical_sha256(policy_payload),
    )


def test_gate_rejects_forged_safety_attribution_even_if_policy_is_widened(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="5" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    g0 = package.generations[0]
    assert g0.outcome is not None

    forged_policy = _forged_safety_policy(package)
    forged_attribution = package.attribution.model_copy(
        update={
            "failure_layer": FailureLayer.SAFETY,
            "action": EvolutionAction.QUARANTINE,
        }
    )
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
        updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="limited to Skill"):
        EvolutionProgramGate().decide(
            policy=forged_policy,
            head=head,
            outcome=g0.outcome,
            decision_id="decision:forged-safety-layer",
            decided_by="policy-controller",
            decided_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            signal=package.signal,
            attribution=forged_attribution,
            consecutive_non_improving_count=1,
        )


def test_package_policy_boundary_rejects_rehashed_allowlist_widening(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="6" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    forged_policy = _forged_safety_policy(package)

    with pytest.raises(ValueError, match="widens automatic intervention"):
        EvolutionProgramPackageManager._verify_policy_boundary(forged_policy)


def test_package_rejects_unrepresentable_release_package_count(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="9" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    g0, g1 = package.generations
    assert g1.plan is not None
    forged_budget = g1.plan.budget.model_copy(
        update={"max_child_packages": 2}
    )
    forged_plan = g1.plan.model_copy(update={"budget": forged_budget})
    forged_generation = g1.model_copy(update={"plan": forged_plan})
    forged_package = package.model_copy(
        update={"generations": (g0, forged_generation)}
    )

    with pytest.raises(ValueError, match="unrepresentable package count"):
        EvolutionProgramPackageManager._verify_generation_budget_boundary(
            forged_package
        )
