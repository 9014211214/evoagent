from __future__ import annotations

from evoagent.program_rl.evidence_verified_final import (
    FullyAttestedProgramLocalRLBindingPackage,
    FullyAttestedProgramLocalRLPackageError,
    FullyAttestedProgramLocalRLPackageManager as _RecursiveManager,
)


class FullyAttestedProgramLocalRLPackageManager(_RecursiveManager):
    """Final public verifier with complete evidence-chain role separation."""

    @staticmethod
    def verify(package: FullyAttestedProgramLocalRLBindingPackage) -> bool:
        _RecursiveManager.verify(package)
        runtime = package.runtime_attested_package
        schema_package = runtime.schema_attested_package
        attested = schema_package.attested_package
        base = attested.base_package
        native = runtime.runtime_attestation
        intent = base.intent
        contract = native.runtime_contract

        reviewer_forbidden = {
            *intent.governed_actor_ids,
            intent.created_by,
            base.authorization.authorized_by,
            base.result.executed_by,
            native.runtime_receipt.verified_by,
            attested.attested_result.bound_by,
            native.schema_attestation.projection_spec.created_by,
        }
        if contract.reviewed_by in reviewer_forbidden:
            raise FullyAttestedProgramLocalRLPackageError(
                "Native runtime contract reviewer overlaps a governed role."
            )

        running_binding = package.running_attested_package.intent_binding
        if running_binding.running_attestor_id not in set(
            intent.governed_actor_ids
        ):
            raise FullyAttestedProgramLocalRLPackageError(
                "Running Generation attestor is absent from optimizer governance."
            )
        final_acceptor_forbidden = {
            *reviewer_forbidden,
            contract.reviewed_by,
            runtime.accepted_by,
            running_binding.bound_by,
            running_binding.running_attestor_id,
        }
        if package.accepted_by in final_acceptor_forbidden:
            raise FullyAttestedProgramLocalRLPackageError(
                "Final local-RL evidence acceptor overlaps a governed role."
            )
        return True

    def build(self, **kwargs) -> FullyAttestedProgramLocalRLBindingPackage:
        package = super().build(**kwargs)
        self.verify(package)
        return package


__all__ = [
    "FullyAttestedProgramLocalRLBindingPackage",
    "FullyAttestedProgramLocalRLPackageError",
    "FullyAttestedProgramLocalRLPackageManager",
]
