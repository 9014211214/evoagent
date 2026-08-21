from __future__ import annotations

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attestation import (
    NativeLocalRLPackageAttestation,
    NativeLocalRLProjection,
)
from evoagent.program_rl.attested_package import (
    AttestedProgramLocalRLBindingPackage,
    AttestedProgramLocalRLResultBinding,
)
from evoagent.program_rl.fully_attested_package import (
    FullyAttestedProgramLocalRLBindingPackage,
    FullyAttestedProgramLocalRLPackageError,
)
from evoagent.program_rl.intent_binding import (
    RunningAttestedProgramLocalRLBindingPackage,
    RunningGenerationIntentBinding,
)
from evoagent.program_rl.intent_binding_verified_final import (
    RunningAttestedProgramLocalRLPackageManager,
    RunningGenerationIntentBindingManager,
)
from evoagent.program_rl.native_contract import (
    NativeLocalRLRuntimeContract,
    NativeLocalRLRuntimeReceipt,
    RuntimeBoundNativeLocalRLPackageAttestation,
)
from evoagent.program_rl.package_verified_final import (
    ProgramLocalRLPackageManager,
)
from evoagent.program_rl.runtime_attested_package import (
    RuntimeAttestedProgramLocalRLBindingPackage,
)
from evoagent.program_rl.schema_attestation import (
    NativeLocalRLProjectionSpec,
    SchemaBoundNativeLocalRLPackageAttestation,
    SchemaBoundNativeLocalRLProjectionReceipt,
)
from evoagent.program_rl.schema_attested_package import (
    SchemaAttestedProgramLocalRLBindingPackage,
)


_REQUIRED_PROJECTION_PATHS = {
    "local_rl_package_id",
    "local_rl_package_hash",
    "local_rl_run_id",
    "optimizer_config_hash",
    "training_task_set_hash",
    "heldout_task_set_hash",
    "initial_checkpoint_hash",
    "selected_checkpoint_hash",
    "optimizer_evidence_hash",
    "heldout_evaluation_hash",
    "iterations",
    "rollouts",
    "tokens",
    "cost_usd",
    "heldout_reward_delta",
    "heldout_success_delta",
    "unsafe_action_count",
    "regression_count",
}


def _verify_projection(projection: NativeLocalRLProjection) -> None:
    if projection.training_task_set_hash == projection.heldout_task_set_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL training and held-out task sets overlap."
        )
    if projection.initial_checkpoint_hash == projection.selected_checkpoint_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection contains no policy update."
        )
    if (
        projection.heldout_reward_delta <= 0.0
        or projection.heldout_success_delta <= 0.0
        or projection.unsafe_action_count != 0
        or projection.regression_count != 0
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection lacks strict safe held-out improvement."
        )
    if not all(
        (
            projection.native_package_hash_recomputed,
            projection.optimizer_recomputed,
            projection.heldout_evaluation_recomputed,
            projection.training_heldout_disjoint,
            projection.checkpoint_selection_recomputed,
        )
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection lacks required recomputation attestations."
        )


def _verify_native_attestation(
    attestation: NativeLocalRLPackageAttestation,
) -> None:
    _verify_projection(attestation.projection)
    if not attestation.native_package_verified:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL package was not verified."
        )
    if (
        attestation.checkpoint_promotion_authorized
        or attestation.production_activation_authorized
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL attestation widens promotion or activation authority."
        )
    expected_hash = program_payload_hash(
        attestation.model_dump(mode="json", exclude={"attestation_hash"})
    )
    if attestation.attestation_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL package attestation hash mismatch."
        )


def _verify_projection_spec(spec: NativeLocalRLProjectionSpec) -> None:
    if set(spec.paths) != _REQUIRED_PROJECTION_PATHS:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection spec field set differs."
        )
    if any(not path for path in spec.paths.values()):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection spec contains an empty path."
        )
    expected_hash = program_payload_hash(
        spec.model_dump(mode="json", exclude={"spec_hash"})
    )
    if spec.spec_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection spec hash mismatch."
        )


def _verify_projection_receipt(
    receipt: SchemaBoundNativeLocalRLProjectionReceipt,
    spec: NativeLocalRLProjectionSpec,
) -> None:
    _verify_projection(receipt.projection)
    if receipt.spec_id != spec.spec_id or receipt.spec_hash != spec.spec_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection receipt differs from its reviewed spec."
        )
    expected_hash = program_payload_hash(
        receipt.model_dump(mode="json", exclude={"projection_hash"})
    )
    if receipt.projection_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection receipt hash mismatch."
        )


def _verify_schema_attestation(
    attestation: SchemaBoundNativeLocalRLPackageAttestation,
) -> None:
    _verify_native_attestation(attestation.base_attestation)
    _verify_projection_spec(attestation.projection_spec)
    _verify_projection_receipt(
        attestation.projection_receipt,
        attestation.projection_spec,
    )
    if (
        attestation.base_attestation.attestation_id
        != attestation.attestation_id
        or attestation.base_attestation.projection
        != attestation.projection_receipt.projection
        or attestation.projection_receipt.projected_at
        > attestation.base_attestation.verified_at
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Schema-bound native local-RL attestation lineage differs."
        )
    if (
        attestation.checkpoint_promotion_authorized
        or attestation.production_activation_authorized
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Schema-bound native attestation widens authority."
        )
    expected_hash = program_payload_hash(
        attestation.model_dump(mode="json", exclude={"attestation_hash"})
    )
    if attestation.attestation_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Schema-bound native local-RL attestation hash mismatch."
        )


def _verify_runtime_contract(contract: NativeLocalRLRuntimeContract) -> None:
    if not (
        contract.package_module == "evoagent.local_rl"
        or contract.package_module.startswith("evoagent.local_rl.")
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL package type is outside evoagent.local_rl."
        )
    if not (
        contract.manager_module == "evoagent.local_rl"
        or contract.manager_module.startswith("evoagent.local_rl.")
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL manager type is outside evoagent.local_rl."
        )
    if contract.verification_method != "verify":
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL runtime contract changed its verification method."
        )
    expected_hash = program_payload_hash(
        contract.model_dump(mode="json", exclude={"contract_hash"})
    )
    if contract.contract_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL runtime contract hash mismatch."
        )


def _verify_runtime_receipt(
    receipt: NativeLocalRLRuntimeReceipt,
    contract: NativeLocalRLRuntimeContract,
) -> None:
    if (
        receipt.contract_id != contract.contract_id
        or receipt.contract_hash != contract.contract_hash
        or not receipt.package_type_verified
        or not receipt.package_schema_verified
        or not receipt.manager_type_verified
        or not receipt.native_package_verified
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL runtime receipt lacks exact contract verification."
        )
    if receipt.verified_at < contract.reviewed_at:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL runtime verification predates contract review."
        )
    expected_hash = program_payload_hash(
        receipt.model_dump(mode="json", exclude={"receipt_hash"})
    )
    if receipt.receipt_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL runtime receipt hash mismatch."
        )


def _verify_runtime_attestation(
    attestation: RuntimeBoundNativeLocalRLPackageAttestation,
) -> None:
    _verify_runtime_contract(attestation.runtime_contract)
    _verify_runtime_receipt(
        attestation.runtime_receipt,
        attestation.runtime_contract,
    )
    _verify_schema_attestation(attestation.schema_attestation)
    if (
        attestation.schema_attestation.attestation_id
        != attestation.attestation_id
        or attestation.runtime_contract.projection_spec_id
        != attestation.schema_attestation.projection_spec.spec_id
        or attestation.runtime_contract.projection_spec_hash
        != attestation.schema_attestation.projection_spec.spec_hash
        or attestation.runtime_receipt.verified_at
        != attestation.schema_attestation.base_attestation.verified_at
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime-bound native local-RL attestation lineage differs."
        )
    if (
        attestation.checkpoint_promotion_authorized
        or attestation.production_activation_authorized
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime-bound native attestation widens authority."
        )
    expected_hash = program_payload_hash(
        attestation.model_dump(mode="json", exclude={"attestation_hash"})
    )
    if attestation.attestation_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime-bound native local-RL attestation hash mismatch."
        )


def _verify_attested_result(
    binding: AttestedProgramLocalRLResultBinding,
    base_package,
    native_attestation: NativeLocalRLPackageAttestation,
) -> None:
    if (
        binding.result != base_package.result
        or binding.native_attestation_id
        != native_attestation.attestation_id
        or binding.native_attestation_hash
        != native_attestation.attestation_hash
        or binding.bound_at < native_attestation.verified_at
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Attested Program local-RL result binding lineage differs."
        )
    forbidden = {
        *base_package.intent.governed_actor_ids,
        base_package.intent.created_by,
        base_package.authorization.authorized_by,
        base_package.result.executed_by,
        native_attestation.verified_by,
    }
    if binding.bound_by in forbidden:
        raise FullyAttestedProgramLocalRLPackageError(
            "Attested Program local-RL result binder overlaps a governed role."
        )
    if (
        binding.checkpoint_promotion_authorized
        or binding.production_activation_authorized
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Attested Program local-RL result widens authority."
        )
    expected_hash = program_payload_hash(
        binding.model_dump(mode="json", exclude={"binding_hash"})
    )
    if binding.binding_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Attested Program local-RL result binding hash mismatch."
        )


def _verify_attested_package(
    package: AttestedProgramLocalRLBindingPackage,
) -> None:
    ProgramLocalRLPackageManager.verify(package.base_package)
    _verify_native_attestation(package.native_attestation)
    _verify_attested_result(
        package.attested_result,
        package.base_package,
        package.native_attestation,
    )
    projection = package.native_attestation.projection
    result = package.base_package.result
    intent = package.base_package.intent
    if (
        projection.local_rl_run_id != intent.local_rl_run_id
        or projection.optimizer_config_hash != intent.optimizer_config_hash
        or projection.training_task_set_hash != intent.training_task_set_hash
        or projection.heldout_task_set_hash != intent.heldout_task_set_hash
        or projection.local_rl_package_id != result.local_rl_package_id
        or projection.local_rl_package_hash != result.local_rl_package_hash
        or projection.initial_checkpoint_hash != result.initial_checkpoint_hash
        or projection.selected_checkpoint_hash != result.selected_checkpoint_hash
        or projection.optimizer_evidence_hash != result.optimizer_evidence_hash
        or projection.heldout_evaluation_hash != result.heldout_evaluation_hash
        or projection.usage != result.usage
        or projection.heldout_reward_delta != result.heldout_reward_delta
        or projection.heldout_success_delta != result.heldout_success_delta
        or projection.unsafe_action_count != result.unsafe_action_count
        or projection.regression_count != result.regression_count
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Native local-RL projection differs from Program result evidence."
        )
    if package.created_at < package.attested_result.bound_at:
        raise FullyAttestedProgramLocalRLPackageError(
            "Attested Program local-RL package predates its result binding."
        )
    if (
        package.checkpoint_promotion_performed
        or package.production_activation_performed
        or package.external_rollout_performed_by_evoagent
        or package.upload_performed
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Attested Program local-RL package widens its evidence boundary."
        )
    expected_hash = program_payload_hash(
        package.model_dump(mode="json", exclude={"package_hash"})
    )
    if package.package_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Attested Program local-RL package hash mismatch."
        )


def _verify_schema_package(
    package: SchemaAttestedProgramLocalRLBindingPackage,
) -> None:
    _verify_attested_package(package.attested_package)
    _verify_schema_attestation(package.schema_attestation)
    if (
        package.schema_attestation.base_attestation
        != package.attested_package.native_attestation
        or package.schema_attestation.projection_spec.created_at
        > package.schema_attestation.projection_receipt.projected_at
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Schema-attested Program local-RL package lineage differs."
        )
    intent = package.attested_package.base_package.intent
    forbidden = {
        *intent.governed_actor_ids,
        intent.created_by,
        package.attested_package.base_package.authorization.authorized_by,
        package.attested_package.base_package.result.executed_by,
        package.schema_attestation.base_attestation.verified_by,
        package.attested_package.attested_result.bound_by,
    }
    if package.schema_attestation.projection_spec.created_by in forbidden:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native projection schema author overlaps a governed role."
        )
    if package.created_at < max(
        package.attested_package.created_at,
        package.schema_attestation.base_attestation.verified_at,
        package.schema_attestation.projection_receipt.projected_at,
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Schema-attested Program local-RL package predates its inputs."
        )
    if (
        package.checkpoint_promotion_performed
        or package.production_activation_performed
        or package.upload_performed
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Schema-attested Program local-RL package widens authority."
        )
    expected_hash = program_payload_hash(
        package.model_dump(mode="json", exclude={"package_hash"})
    )
    if package.package_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Schema-attested Program local-RL package hash mismatch."
        )


def _verify_runtime_package(
    package: RuntimeAttestedProgramLocalRLBindingPackage,
) -> None:
    _verify_schema_package(package.schema_attested_package)
    _verify_runtime_attestation(package.runtime_attestation)
    schema = package.schema_attested_package.schema_attestation
    if package.runtime_attestation.schema_attestation != schema:
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime identity attestation differs from accepted schema evidence."
        )
    base = package.schema_attested_package.attested_package.base_package
    intent = base.intent
    contract = package.runtime_attestation.runtime_contract
    forbidden = {
        *intent.governed_actor_ids,
        intent.created_by,
        base.authorization.authorized_by,
        base.result.executed_by,
        schema.base_attestation.verified_by,
        package.schema_attested_package.attested_package.attested_result.bound_by,
        schema.projection_spec.created_by,
        contract.reviewed_by,
    }
    if package.accepted_by in forbidden:
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime-attested package acceptor overlaps a governed role."
        )
    reviewer_forbidden = forbidden - {contract.reviewed_by}
    if contract.reviewed_by in reviewer_forbidden:
        raise FullyAttestedProgramLocalRLPackageError(
            "Native runtime contract reviewer overlaps a governed role."
        )
    if package.accepted_at < max(
        package.schema_attested_package.created_at,
        package.runtime_attestation.runtime_receipt.verified_at,
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime-attested package acceptance predates verified inputs."
        )
    if (
        package.checkpoint_promotion_performed
        or package.production_activation_performed
        or package.external_rollout_performed_by_evoagent
        or package.upload_performed
    ):
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime-attested package widens its offline evidence boundary."
        )
    expected_hash = program_payload_hash(
        package.model_dump(mode="json", exclude={"package_hash"})
    )
    if package.package_hash != expected_hash:
        raise FullyAttestedProgramLocalRLPackageError(
            "Runtime-attested Program local-RL package hash mismatch."
        )


class FullyAttestedProgramLocalRLPackageManager:
    """Final recursive verifier before any future promotion process."""

    @staticmethod
    def verify(package: FullyAttestedProgramLocalRLBindingPackage) -> bool:
        RunningGenerationIntentBindingManager.verify(
            package.running_attested_package.intent_binding
        )
        RunningAttestedProgramLocalRLPackageManager.verify(
            package.running_attested_package
        )
        _verify_runtime_package(package.runtime_attested_package)
        runtime_base = (
            package.runtime_attested_package
            .schema_attested_package
            .attested_package
            .base_package
        )
        if package.running_attested_package.base_package != runtime_base:
            raise FullyAttestedProgramLocalRLPackageError(
                "Running Program and native runtime evidence bind different optimizer packages."
            )
        intent = runtime_base.intent
        runtime = package.runtime_attested_package
        native = runtime.runtime_attestation
        forbidden = {
            *intent.governed_actor_ids,
            intent.created_by,
            runtime_base.authorization.authorized_by,
            runtime_base.result.executed_by,
            native.runtime_contract.reviewed_by,
            native.runtime_receipt.verified_by,
            runtime.schema_attested_package.attested_package.attested_result.bound_by,
            native.schema_attestation.projection_spec.created_by,
            runtime.accepted_by,
        }
        if package.accepted_by in forbidden:
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested package acceptor overlaps a governed role."
            )
        if package.accepted_at < max(
            package.running_attested_package.created_at,
            runtime.accepted_at,
        ):
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested package acceptance predates its evidence chains."
            )
        if (
            package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
            or package.official_benchmark_claimed
        ):
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested package widens its evidence-only boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise FullyAttestedProgramLocalRLPackageError(
                "Fully attested Program local-RL package hash mismatch."
            )
        return True

    def build(self, **kwargs) -> FullyAttestedProgramLocalRLBindingPackage:
        from evoagent.program_rl.fully_attested_package import (
            FullyAttestedProgramLocalRLPackageManager as _Builder,
        )

        package = _Builder().build(**kwargs)
        self.verify(package)
        return package


__all__ = [
    "FullyAttestedProgramLocalRLBindingPackage",
    "FullyAttestedProgramLocalRLPackageError",
    "FullyAttestedProgramLocalRLPackageManager",
]
