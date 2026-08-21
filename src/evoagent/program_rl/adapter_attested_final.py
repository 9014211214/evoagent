from __future__ import annotations

from datetime import datetime
from typing import Any

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.adapter_final import ProgramLocalRLAdapter as _ScopedAdapter
from evoagent.program_rl.models import ProgramLocalRLIntent


_ATTESTED_LOCAL_RL_INTERVENTIONS = {
    FailureLayer.ROUTER: EvolutionAction.UPDATE_ROUTER,
    FailureLayer.CONTEXT: EvolutionAction.UPDATE_CONTEXT,
    FailureLayer.VERIFIER: EvolutionAction.REPAIR_VERIFIER,
}


class ProgramLocalRLAdapter(_ScopedAdapter):
    """Final adapter: pure-object path plus production attestation path."""

    def build_intent_from_attestation(
        self,
        attestation: Any,
        *,
        local_rl_run_id: str,
        optimizer_config_hash: str,
        training_task_set_hash: str,
        heldout_task_set_hash: str,
        created_by: str,
        created_at: datetime,
        intent_id: str | None = None,
    ) -> ProgramLocalRLIntent:
        if not hasattr(attestation, "model_dump"):
            raise TypeError(
                "Running Generation attestation must be an immutable Pydantic record."
            )
        attestation_payload = attestation.model_dump(
            mode="json",
            exclude={"attestation_hash"},
        )
        if (
            getattr(attestation, "attestation_hash", None)
            != program_payload_hash(attestation_payload)
        ):
            raise ValueError("Running Generation attestation hash mismatch.")
        if (
            getattr(attestation, "program_state", None)
            != "generation_running"
            or getattr(attestation, "campaign_state", None) != "authorized"
            or getattr(attestation, "optimizer_execution_authorized", None)
            is not False
            or getattr(attestation, "checkpoint_promotion_authorized", None)
            is not False
            or getattr(attestation, "production_activation_authorized", None)
            is not False
        ):
            raise ValueError(
                "Running Generation attestation widens or differs from the offline intent boundary."
            )
        layer = attestation.intervention_layer
        expected_action = _ATTESTED_LOCAL_RL_INTERVENTIONS.get(layer)
        if expected_action is None:
            raise ValueError(
                "Attested Program intervention is not eligible for local Agent-policy RL."
            )
        if attestation.intervention_action != expected_action:
            raise ValueError(
                "Attested local-RL intervention action differs from its policy layer."
            )
        if created_at < attestation.attested_at:
            raise ValueError(
                "Local-RL intent time precedes the running Generation attestation."
            )
        roles = attestation.roles
        if not callable(getattr(roles, "all_actor_ids", None)):
            raise TypeError(
                "Running Generation attestation lacks governed role enumeration."
            )
        governed_actor_ids = tuple(
            sorted({*roles.all_actor_ids(), attestation.attested_by})
        )
        if created_by in set(governed_actor_ids):
            raise ValueError(
                "Local-RL intent author overlaps the attested Program lifecycle."
            )
        payload = {
            "intent_id": intent_id
            or (
                "program-local-rl-intent:"
                f"{attestation.program_id}:{attestation.generation_index}"
            ),
            "program_id": attestation.program_id,
            "generation_id": attestation.generation_id,
            "generation_index": attestation.generation_index,
            "program_head_revision": attestation.program_head_revision,
            "program_checkpoint": attestation.program_checkpoint.model_dump(
                mode="json"
            ),
            "campaign_id": attestation.campaign_id,
            "plan_id": attestation.plan_id,
            "plan_hash": attestation.plan_hash,
            "source_signal_id": attestation.source_signal_id,
            "source_signal_hash": attestation.source_signal_hash,
            "attribution_receipt_id": attestation.attribution_receipt_id,
            "attribution_receipt_hash": attestation.attribution_receipt_hash,
            "intervention_layer": layer,
            "intervention_action": attestation.intervention_action,
            "parent_agent_identity_hash": (
                attestation.parent_agent_identity_hash
            ),
            "target_agent_identity_hash": (
                attestation.target_agent_identity_hash
            ),
            "expected_release_package_hash": (
                attestation.expected_release_package_hash
            ),
            "expected_release_plan_hash": (
                attestation.expected_release_plan_hash
            ),
            "local_rl_run_id": local_rl_run_id,
            "optimizer_config_hash": optimizer_config_hash,
            "training_task_set_hash": training_task_set_hash,
            "heldout_task_set_hash": heldout_task_set_hash,
            "governed_actor_ids": governed_actor_ids,
            "created_by": created_by,
            "created_at": created_at,
            "optimizer_execution_authorized": False,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return ProgramLocalRLIntent(
            **payload,
            intent_hash=program_payload_hash(payload),
        )


__all__ = ["ProgramLocalRLAdapter"]
