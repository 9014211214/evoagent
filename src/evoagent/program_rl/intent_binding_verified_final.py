from __future__ import annotations

from datetime import datetime

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.intent_binding import (
    RunningAttestedProgramLocalRLBindingPackage,
    RunningAttestedProgramLocalRLPackageError,
    RunningAttestedProgramLocalRLPackageManager as _RunningPackageManager,
    RunningGenerationIntentBinding,
    RunningGenerationIntentBindingManager as _BindingManager,
)
from evoagent.program_rl.package import ProgramLocalRLPackageManager


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise RunningAttestedProgramLocalRLPackageError(
            "Embedded running Generation attestation time is invalid."
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RunningAttestedProgramLocalRLPackageError(
            "Embedded running Generation attestation time lacks a timezone."
        )
    return parsed


class RunningGenerationIntentBindingManager(_BindingManager):
    """Final nested verifier with normalized immutable attestation semantics."""

    @staticmethod
    def verify(binding: RunningGenerationIntentBinding) -> bool:
        expected_intent_hash = program_payload_hash(
            binding.intent.model_dump(mode="json", exclude={"intent_hash"})
        )
        if binding.intent.intent_hash != expected_intent_hash:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested local-RL intent hash mismatch."
            )
        if (
            binding.bound_by != binding.intent.created_by
            or binding.bound_at != binding.intent.created_at
            or binding.bound_at < binding.running_attested_at
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested binding actor or time differs from intent lineage."
            )
        if binding.running_attestor_id not in set(
            binding.intent.governed_actor_ids
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running Generation attestor is absent from governed intent actors."
            )
        embedded = binding.running_attestation_payload
        if "attestation_hash" not in embedded:
            raise RunningAttestedProgramLocalRLPackageError(
                "Embedded running Generation attestation lacks its hash."
            )
        recomputed_attestation_hash = program_payload_hash(
            {
                key: value
                for key, value in embedded.items()
                if key != "attestation_hash"
            }
        )
        if (
            embedded.get("attestation_id") != binding.running_attestation_id
            or embedded.get("attestation_hash")
            != binding.running_attestation_hash
            or recomputed_attestation_hash != binding.running_attestation_hash
            or embedded.get("attested_by") != binding.running_attestor_id
            or _parse_datetime(embedded.get("attested_at"))
            != binding.running_attested_at
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Embedded running Generation attestation identity, time or hash differs."
            )
        expected = {
            "program_id": binding.intent.program_id,
            "generation_id": binding.intent.generation_id,
            "generation_index": binding.intent.generation_index,
            "program_head_revision": binding.intent.program_head_revision,
            "campaign_id": binding.intent.campaign_id,
            "plan_id": binding.intent.plan_id,
            "plan_hash": binding.intent.plan_hash,
            "source_signal_id": binding.intent.source_signal_id,
            "source_signal_hash": binding.intent.source_signal_hash,
            "attribution_receipt_id": binding.intent.attribution_receipt_id,
            "attribution_receipt_hash": binding.intent.attribution_receipt_hash,
            "intervention_layer": binding.intent.intervention_layer.value,
            "intervention_action": binding.intent.intervention_action.value,
            "parent_agent_identity_hash": (
                binding.intent.parent_agent_identity_hash
            ),
            "target_agent_identity_hash": (
                binding.intent.target_agent_identity_hash
            ),
            "expected_release_package_hash": (
                binding.intent.expected_release_package_hash
            ),
            "expected_release_plan_hash": (
                binding.intent.expected_release_plan_hash
            ),
        }
        if any(embedded.get(field) != value for field, value in expected.items()):
            raise RunningAttestedProgramLocalRLPackageError(
                "Embedded running Generation attestation differs from optimizer intent."
            )
        if embedded.get("program_checkpoint") != (
            binding.intent.program_checkpoint.model_dump(mode="json")
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Embedded Program checkpoint differs from optimizer intent."
            )
        if embedded.get("campaign_checkpoint") != (
            binding.campaign_checkpoint.model_dump(mode="json")
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Embedded Campaign checkpoint differs from intent binding."
            )
        if (
            embedded.get("program_state") != "generation_running"
            or embedded.get("campaign_state") != "authorized"
            or embedded.get("optimizer_execution_authorized") is not False
            or embedded.get("checkpoint_promotion_authorized") is not False
            or embedded.get("production_activation_authorized") is not False
            or binding.optimizer_execution_authorized
            or binding.checkpoint_promotion_authorized
            or binding.production_activation_authorized
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested evidence widens execution authority."
            )
        expected_binding_hash = program_payload_hash(
            binding.model_dump(mode="json", exclude={"binding_hash"})
        )
        if binding.binding_hash != expected_binding_hash:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running Generation intent binding hash mismatch."
            )
        return True

    def build(self, *args, **kwargs) -> RunningGenerationIntentBinding:
        binding = super().build(*args, **kwargs)
        self.verify(binding)
        return binding


class RunningAttestedProgramLocalRLPackageManager(_RunningPackageManager):
    """Final running-attested package manager with nested revalidation."""

    @staticmethod
    def verify(package: RunningAttestedProgramLocalRLBindingPackage) -> bool:
        ProgramLocalRLPackageManager.verify(package.base_package)
        RunningGenerationIntentBindingManager.verify(package.intent_binding)
        if package.base_package.intent != package.intent_binding.intent:
            raise RunningAttestedProgramLocalRLPackageError(
                "Optimizer package intent differs from running-attestation binding."
            )
        if package.created_at < max(
            package.base_package.created_at,
            package.intent_binding.bound_at,
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested package predates its immutable inputs."
            )
        if (
            package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
        ):
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested package widens its offline non-promotion boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise RunningAttestedProgramLocalRLPackageError(
                "Running-attested local-RL package hash mismatch."
            )
        return True

    def build(self, **kwargs) -> RunningAttestedProgramLocalRLBindingPackage:
        package = super().build(**kwargs)
        self.verify(package)
        return package


__all__ = [
    "RunningAttestedProgramLocalRLBindingPackage",
    "RunningAttestedProgramLocalRLPackageError",
    "RunningAttestedProgramLocalRLPackageManager",
    "RunningGenerationIntentBinding",
    "RunningGenerationIntentBindingManager",
]
