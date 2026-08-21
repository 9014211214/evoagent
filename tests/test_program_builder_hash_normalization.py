from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.program import (
    GenerationBudget,
    ProgramLearningSignal,
    build_attribution_receipt,
    build_generation_plan,
)
from evoagent.program.hashing import program_payload_hash
from evoagent.release.models import ReleaseDecisionAction, ReleaseState


SIGNAL_TIME = datetime(2026, 8, 12, tzinfo=timezone.utc)
ATTRIBUTION_TIME = SIGNAL_TIME + timedelta(minutes=1)
TARGET_TIME = SIGNAL_TIME + timedelta(minutes=1, seconds=30)
PLAN_TIME = SIGNAL_TIME + timedelta(minutes=2)


def _signal():
    payload = {
        "signal_id": "signal:hash-normalization",
        "program_id": "program:hash-normalization",
        "generation_index": 0,
        "source_release_package_hash": "1" * 64,
        "source_release_plan_hash": "2" * 64,
        "source_batch_hash": "3" * 64,
        "source_assessment_hash": "4" * 64,
        "source_decision_hash": "5" * 64,
        "source_stage_id": "canary-25",
        "family_id": "agent-family",
        "incumbent_snapshot_id": "a0",
        "challenger_snapshot_id": "a1",
        "runtime_config_sha256": "6" * 64,
        "tool_contract_sha256": "7" * 64,
        "terminal_action": ReleaseDecisionAction.ROLLBACK,
        "terminal_state": ReleaseState.ROLLED_BACK,
        "reasons": ("protected_segment_regression:protected",),
        "affected_segments": ("protected",),
        "protected_segments": ("protected",),
        "safety_violation_count": 1,
        "evidence_producer_id": "evidence-producer",
        "created_at": SIGNAL_TIME,
        "trust_level": "verified",
        "causal_attribution_claimed": False,
    }
    return ProgramLearningSignal(
        **payload,
        signal_hash=program_payload_hash(payload),
    )


def _attribution(signal):
    return build_attribution_receipt(
        signal,
        receipt_id="attribution:hash-normalization",
        failure_layer=FailureLayer.CONTEXT,
        action=EvolutionAction.UPDATE_CONTEXT,
        confidence=1.0,
        supported_experiment_hashes=("8" * 64,),
        attributor_id="independent-attributor",
        created_at=ATTRIBUTION_TIME,
    )


def _target(*, created_at=TARGET_TIME):
    return SimpleNamespace(
        package_hash="9" * 64,
        created_at=created_at,
        champion_package=SimpleNamespace(package_hash="a" * 64),
        plan=SimpleNamespace(
            plan_hash="b" * 64,
            challenger_snapshot_id="a1",
            runtime_config_sha256="c" * 64,
            tool_contract_sha256="d" * 64,
        ),
    )


def _build_plan(**updates):
    signal = updates.pop("signal", _signal())
    attribution = updates.pop("attribution", _attribution(signal))
    arguments = {
        "program_id": signal.program_id,
        "generation_id": "generation:g1",
        "generation_index": 1,
        "parent_generation_id": "generation:g0",
        "signal": signal,
        "attribution": attribution,
        "parent_agent_identity_hash": "e" * 64,
        "target_release_package": _target(),
        "budget": GenerationBudget(
            max_child_packages=1,
            max_pairs=10,
            max_tokens=100,
            max_cost_usd=0.0,
        ),
        "created_by": "decision-planner",
        "created_at": PLAN_TIME,
    }
    arguments.update(updates)
    return build_generation_plan(**arguments)


def test_attribution_and_generation_plan_hashes_include_normalized_defaults():
    signal = _signal()
    attribution = _attribution(signal)
    assert attribution.independent is True
    assert attribution.receipt_hash == program_payload_hash(
        attribution.model_dump(mode="json", exclude={"receipt_hash"})
    )

    plan = _build_plan(signal=signal, attribution=attribution)
    assert plan.external_execution_authorized is False
    assert plan.production_deployment_authorized is False
    assert plan.plan_hash == program_payload_hash(
        plan.model_dump(mode="json", exclude={"plan_hash"})
    )


def test_attribution_builder_rejects_time_before_signal():
    signal = _signal()
    with pytest.raises(ValueError, match="precedes its learning signal"):
        build_attribution_receipt(
            signal,
            receipt_id="attribution:premature-builder-control",
            failure_layer=FailureLayer.CONTEXT,
            action=EvolutionAction.UPDATE_CONTEXT,
            confidence=1.0,
            supported_experiment_hashes=("8" * 64,),
            attributor_id="independent-attributor",
            created_at=SIGNAL_TIME - timedelta(seconds=1),
        )


def test_generation_plan_builder_requires_one_release_package():
    with pytest.raises(ValueError, match="exactly one release evidence package"):
        _build_plan(
            generation_id="generation:g1:multi-package",
            budget=GenerationBudget(
                max_child_packages=2,
                max_pairs=10,
                max_tokens=100,
                max_cost_usd=0.0,
            ),
        )


def test_generation_plan_builder_rejects_cross_program_signal():
    with pytest.raises(ValueError, match="Program differs"):
        _build_plan(program_id="program:another-program")


def test_generation_plan_builder_rejects_skipped_generation():
    with pytest.raises(ValueError, match="immediate successor"):
        _build_plan(generation_index=2)


def test_generation_plan_builder_rejects_premature_plan_time():
    with pytest.raises(ValueError, match="precedes its signal, attribution, or target"):
        _build_plan(
            target_release_package=_target(
                created_at=PLAN_TIME + timedelta(seconds=1)
            )
        )
