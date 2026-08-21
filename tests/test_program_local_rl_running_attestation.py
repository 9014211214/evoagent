from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import ProgramCheckpoint
from evoagent.program_rl import ProgramLocalRLAdapter
from tests.test_program_local_rl_adapter import _running_program


class _Roles(BaseModel):
    model_config = ConfigDict(frozen=True)

    actors: tuple[str, ...]

    def all_actor_ids(self) -> tuple[str, ...]:
        return self.actors


class _RunningAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_id: str
    generation_id: str
    generation_index: int
    program_state: str
    program_head_revision: int
    program_checkpoint: ProgramCheckpoint
    campaign_id: str
    campaign_state: str
    plan_id: str
    plan_hash: str
    source_signal_id: str
    source_signal_hash: str
    attribution_receipt_id: str
    attribution_receipt_hash: str
    intervention_layer: FailureLayer
    intervention_action: EvolutionAction
    parent_agent_identity_hash: str
    target_agent_identity_hash: str
    expected_release_package_hash: str
    expected_release_plan_hash: str
    roles: _Roles
    attested_by: str
    attested_at: object
    optimizer_execution_authorized: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    attestation_hash: str


def _attestation(tmp_path):
    package, generation, head, checkpoint, governed = _running_program(tmp_path)
    plan = generation.plan
    assert plan is not None
    payload = {
        "program_id": generation.program_id,
        "generation_id": generation.generation_id,
        "generation_index": generation.generation_index,
        "program_state": "generation_running",
        "program_head_revision": head.revision,
        "program_checkpoint": checkpoint,
        "campaign_id": generation.campaign_id,
        "campaign_state": "authorized",
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "source_signal_id": plan.source_signal_id,
        "source_signal_hash": plan.source_signal_hash,
        "attribution_receipt_id": plan.attribution_receipt_id,
        "attribution_receipt_hash": plan.attribution_receipt_hash,
        "intervention_layer": plan.intervention_layer,
        "intervention_action": plan.intervention_action,
        "parent_agent_identity_hash": plan.parent_agent_identity_hash,
        "target_agent_identity_hash": plan.target_agent_identity_hash,
        "expected_release_package_hash": plan.expected_release_package_hash,
        "expected_release_plan_hash": plan.expected_release_plan_hash,
        "roles": _Roles(actors=governed),
        "attested_by": "independent-running-generation-attestor",
        "attested_at": head.updated_at,
        "optimizer_execution_authorized": False,
        "checkpoint_promotion_authorized": False,
        "production_activation_authorized": False,
    }
    return _RunningAttestation(
        **payload,
        attestation_hash=program_payload_hash(payload),
    )


def _intent(adapter, attestation):
    return adapter.build_intent_from_attestation(
        attestation,
        local_rl_run_id="local-rl-run:attested:g1",
        optimizer_config_hash="1" * 64,
        training_task_set_hash="2" * 64,
        heldout_task_set_hash="3" * 64,
        created_by="local-rl-intent-builder",
        created_at=attestation.attested_at,
    )


def _rehashed_copy(attestation, **updates):
    payload = attestation.model_dump(mode="json", exclude={"attestation_hash"})
    payload.update(updates)
    return attestation.model_copy(
        update={
            **updates,
            "attestation_hash": program_payload_hash(payload),
        }
    )


def test_local_rl_intent_is_derived_from_running_attestation(tmp_path):
    attestation = _attestation(tmp_path)
    intent = _intent(ProgramLocalRLAdapter(), attestation)

    assert intent.program_id == attestation.program_id
    assert intent.generation_id == attestation.generation_id
    assert intent.plan_hash == attestation.plan_hash
    assert intent.program_checkpoint == attestation.program_checkpoint
    assert attestation.attested_by in set(intent.governed_actor_ids)
    assert intent.optimizer_execution_authorized is False
    assert intent.checkpoint_promotion_authorized is False
    assert intent.production_activation_authorized is False


def test_tampered_running_attestation_is_rejected(tmp_path):
    attestation = _attestation(tmp_path)
    forged = attestation.model_copy(update={"plan_hash": "0" * 64})

    with pytest.raises(ValueError, match="attestation hash mismatch"):
        _intent(ProgramLocalRLAdapter(), forged)


def test_attestation_cannot_pre_authorize_optimizer_or_activation(tmp_path):
    attestation = _attestation(tmp_path)
    forged = _rehashed_copy(
        attestation,
        optimizer_execution_authorized=True,
    )

    with pytest.raises(ValueError, match="widens or differs"):
        _intent(ProgramLocalRLAdapter(), forged)


def test_attested_non_policy_layer_cannot_enter_local_rl(tmp_path):
    attestation = _attestation(tmp_path)
    forged = _rehashed_copy(
        attestation,
        intervention_layer=FailureLayer.MODEL,
        intervention_action=EvolutionAction.TRAIN_MODEL,
    )

    with pytest.raises(ValueError, match="not eligible"):
        _intent(ProgramLocalRLAdapter(), forged)
