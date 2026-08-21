from __future__ import annotations

import importlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.local_rl.package import (
    LocalRLPackageManager,
    LocalRLPackageManifest,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attestation import NativeLocalRLProjection
from evoagent.program_rl.models import LocalRLExecutionUsage
from evoagent.program_rl.schema_attestation import (
    NativeLocalRLProjectionSpec,
    PydanticNativeLocalRLProjector,
    SchemaBoundNativeLocalRLAttestor,
    SchemaBoundNativeLocalRLPackageAttestation,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_LOCAL_RL_MODULE_PREFIX = "evoagent.local_rl"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _qualified_name(value: type[Any]) -> tuple[str, str]:
    return value.__module__, value.__qualname__


def _resolve(module_name: str, qualified_name: str) -> type[Any]:
    if not (
        module_name == _LOCAL_RL_MODULE_PREFIX
        or module_name.startswith(_LOCAL_RL_MODULE_PREFIX + ".")
    ):
        raise ValueError("Native local-RL type must be defined under evoagent.local_rl.")
    module = importlib.import_module(module_name)
    value: Any = module
    for component in qualified_name.split("."):
        if component == "<locals>":
            raise ValueError("Native local-RL contract cannot bind a local class.")
        value = getattr(value, component)
    if not isinstance(value, type):
        raise TypeError("Native local-RL contract did not resolve a class.")
    return value


def _task_set_hash(tasks: tuple[Any, ...]) -> str:
    return canonical_sha256(
        [item.model_dump(mode="json") for item in tasks]
    )


def _mean_task_reward(report: Any) -> float:
    if not report.task_results:
        raise ValueError("Native local-RL evaluation report has no Tasks.")
    return sum(item.total_reward for item in report.task_results) / len(
        report.task_results
    )


class EvoagentLocalRLPackageProjector:
    """Project only a verified native ``LocalRLPackageManifest``.

    No caller supplies optimizer metrics, evaluation deltas, checkpoint hashes, or
    budget usage. The exact native package manager first recomputes the optimizer,
    all held-out evaluations, deterministic checkpoint selection, package hash,
    and audit chain. This projector then derives the governed Program projection
    exclusively from those verified immutable records.
    """

    def __init__(self, manager: LocalRLPackageManager | None = None):
        self.manager = manager or LocalRLPackageManager()
        if type(self.manager) is not LocalRLPackageManager:
            raise TypeError(
                "Native evoagent projection requires the exact LocalRLPackageManager."
            )

    @staticmethod
    def task_set_hash(tasks: tuple[Any, ...]) -> str:
        return _task_set_hash(tasks)

    @staticmethod
    def optimizer_config_hash(package: LocalRLPackageManifest) -> str:
        return canonical_sha256(
            {
                "environment_contract_hash": (
                    package.manifest.environment.contract_hash
                ),
                "hyperparameter_hash": (
                    package.manifest.hyperparameters.hyperparameter_hash
                ),
                "training_budget_hash": package.manifest.budget.budget_hash,
            }
        )

    def project(self, package: LocalRLPackageManifest) -> NativeLocalRLProjection:
        if type(package) is not LocalRLPackageManifest:
            raise TypeError(
                "Native evoagent projection requires the exact LocalRLPackageManifest."
            )
        if self.manager.verify(package) is not True:
            raise ValueError("Native Local RL package verification did not pass.")

        selected_reports = tuple(
            report
            for report in package.candidate_evaluations
            if report.checkpoint_hash
            == package.decision.selected_checkpoint_hash
            and report.report_hash == package.decision.selected_report_hash
        )
        if len(selected_reports) != 1:
            raise ValueError(
                "Native Local RL package lacks one exact selected evaluation report."
            )
        selected_report = selected_reports[0]

        selected_assessments = tuple(
            assessment
            for assessment in package.decision.assessments
            if assessment.checkpoint_id
            == package.decision.selected_checkpoint_id
            and assessment.checkpoint_hash
            == package.decision.selected_checkpoint_hash
            and assessment.iteration == package.decision.selected_iteration
            and assessment.eligible
        )
        if len(selected_assessments) != 1:
            raise ValueError(
                "Native Local RL package lacks one exact selected assessment."
            )
        selected_assessment = selected_assessments[0]

        training_task_set_hash = self.task_set_hash(
            package.manifest.training_tasks
        )
        heldout_task_set_hash = self.task_set_hash(
            package.manifest.held_out_tasks
        )
        if (
            heldout_task_set_hash
            != package.baseline_evaluation.task_manifest_hash
            or any(
                report.task_manifest_hash != heldout_task_set_hash
                for report in package.candidate_evaluations
            )
        ):
            raise ValueError(
                "Native Local RL held-out Task evidence differs from the manifest."
            )

        baseline_by_task = {
            item.task_id: item
            for item in package.baseline_evaluation.task_results
        }
        selected_by_task = {
            item.task_id: item for item in selected_report.task_results
        }
        if set(baseline_by_task) != set(selected_by_task):
            raise ValueError(
                "Native Local RL selected evaluation uses another Task set."
            )
        regression_count = sum(
            baseline_by_task[task_id].success
            and not selected_by_task[task_id].success
            for task_id in baseline_by_task
        )
        if (
            regression_count != selected_assessment.regression_count
            or selected_report.unsafe_action_count
            != selected_assessment.unsafe_action_count
        ):
            raise ValueError(
                "Native Local RL selected assessment differs from evaluation evidence."
            )

        heldout_reward_delta = _mean_task_reward(
            selected_report
        ) - _mean_task_reward(package.baseline_evaluation)
        heldout_success_delta = (
            selected_report.overall_score
            - package.baseline_evaluation.overall_score
        )

        return NativeLocalRLProjection(
            local_rl_package_id=package.package_id,
            local_rl_package_hash=package.package_hash,
            local_rl_run_id=package.manifest.run_id,
            optimizer_config_hash=self.optimizer_config_hash(package),
            training_task_set_hash=training_task_set_hash,
            heldout_task_set_hash=heldout_task_set_hash,
            initial_checkpoint_hash=(
                package.training.initial_checkpoint.checkpoint_hash
            ),
            selected_checkpoint_hash=(
                package.decision.selected_checkpoint_hash
            ),
            optimizer_evidence_hash=package.training.result_hash,
            heldout_evaluation_hash=selected_report.report_hash,
            usage=LocalRLExecutionUsage(
                iterations=package.training.usage.iterations,
                rollouts=package.training.usage.rollouts,
                tokens=0,
                cost_usd=0.0,
            ),
            heldout_reward_delta=heldout_reward_delta,
            heldout_success_delta=heldout_success_delta,
            unsafe_action_count=selected_report.unsafe_action_count,
            regression_count=regression_count,
        )


class NativeLocalRLRuntimeContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_id: str = Field(pattern=_SAFE_ID_PATTERN)
    package_module: str
    package_qualified_name: str
    package_schema_hash: str = Field(pattern=_SHA256_PATTERN)
    manager_module: str
    manager_qualified_name: str
    verification_method: Literal["verify"] = "verify"
    projection_spec_id: str = Field(pattern=_SAFE_ID_PATTERN)
    projection_spec_hash: str = Field(pattern=_SHA256_PATTERN)
    reviewed_by: str = Field(pattern=_SAFE_ID_PATTERN)
    reviewed_at: datetime
    contract_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("reviewed_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Native local-RL runtime contract time")

    @model_validator(mode="after")
    def validate_contract(self):
        for module_name in (self.package_module, self.manager_module):
            if not (
                module_name == _LOCAL_RL_MODULE_PREFIX
                or module_name.startswith(_LOCAL_RL_MODULE_PREFIX + ".")
            ):
                raise ValueError(
                    "Native local-RL runtime contract must bind evoagent.local_rl types."
                )
        payload = self.model_dump(mode="json", exclude={"contract_hash"})
        validate_safe_content(payload)
        if self.contract_hash != canonical_sha256(payload):
            raise ValueError("Native local-RL runtime contract hash mismatch.")
        return self


class NativeLocalRLRuntimeReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    contract_id: str = Field(pattern=_SAFE_ID_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)
    package_type_verified: Literal[True] = True
    package_schema_verified: Literal[True] = True
    manager_type_verified: Literal[True] = True
    native_package_verified: Literal[True] = True
    verified_by: str = Field(pattern=_SAFE_ID_PATTERN)
    verified_at: datetime
    receipt_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("verified_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _timezone(value, "Native local-RL runtime receipt time")

    @model_validator(mode="after")
    def validate_receipt(self):
        payload = self.model_dump(mode="json", exclude={"receipt_hash"})
        validate_safe_content(payload)
        if self.receipt_hash != canonical_sha256(payload):
            raise ValueError("Native local-RL runtime receipt hash mismatch.")
        return self


class RuntimeBoundNativeLocalRLPackageAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    attestation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    runtime_contract: NativeLocalRLRuntimeContract
    runtime_receipt: NativeLocalRLRuntimeReceipt
    schema_attestation: SchemaBoundNativeLocalRLPackageAttestation
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    attestation_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_attestation(self):
        if (
            self.runtime_receipt.contract_id != self.runtime_contract.contract_id
            or self.runtime_receipt.contract_hash != self.runtime_contract.contract_hash
            or self.schema_attestation.attestation_id != self.attestation_id
            or self.schema_attestation.projection_spec.spec_id
            != self.runtime_contract.projection_spec_id
            or self.schema_attestation.projection_spec.spec_hash
            != self.runtime_contract.projection_spec_hash
            or self.runtime_receipt.verified_at
            != self.schema_attestation.base_attestation.verified_at
        ):
            raise ValueError(
                "Runtime-bound native local-RL attestation lineage differs."
            )
        payload = self.model_dump(mode="json", exclude={"attestation_hash"})
        validate_safe_content(payload)
        if self.attestation_hash != canonical_sha256(payload):
            raise ValueError("Runtime-bound native local-RL attestation hash mismatch.")
        return self


class NativeLocalRLRuntimeContractBuilder:
    """Freeze exact native package/manager types and package JSON Schema."""

    def build(
        self,
        *,
        package_type: type[Any],
        manager_type: type[Any],
        projection_spec: NativeLocalRLProjectionSpec,
        reviewed_by: str,
        reviewed_at: datetime,
        contract_id: str,
    ) -> NativeLocalRLRuntimeContract:
        package_module, package_name = _qualified_name(package_type)
        manager_module, manager_name = _qualified_name(manager_type)
        _resolve(package_module, package_name)
        _resolve(manager_module, manager_name)
        if not hasattr(package_type, "model_json_schema"):
            raise TypeError(
                "Native local-RL package class must expose model_json_schema()."
            )
        if not callable(getattr(manager_type, "verify", None)):
            raise TypeError("Native local-RL manager class must expose verify().")
        schema_hash = program_payload_hash(package_type.model_json_schema())
        payload = {
            "contract_id": contract_id,
            "package_module": package_module,
            "package_qualified_name": package_name,
            "package_schema_hash": schema_hash,
            "manager_module": manager_module,
            "manager_qualified_name": manager_name,
            "verification_method": "verify",
            "projection_spec_id": projection_spec.spec_id,
            "projection_spec_hash": projection_spec.spec_hash,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
        }
        return NativeLocalRLRuntimeContract(
            **payload,
            contract_hash=program_payload_hash(payload),
        )


class _ContractVerifier:
    def __init__(
        self,
        contract: NativeLocalRLRuntimeContract,
        manager: Any,
    ):
        self.contract = contract
        self.manager = manager

    def verify(self, package: Any) -> bool:
        package_type = _resolve(
            self.contract.package_module,
            self.contract.package_qualified_name,
        )
        manager_type = _resolve(
            self.contract.manager_module,
            self.contract.manager_qualified_name,
        )
        if type(package) is not package_type:
            raise TypeError("Native local-RL package type differs from reviewed contract.")
        if type(self.manager) is not manager_type:
            raise TypeError("Native local-RL manager type differs from reviewed contract.")
        schema_hash = program_payload_hash(package_type.model_json_schema())
        if schema_hash != self.contract.package_schema_hash:
            raise ValueError(
                "Native local-RL package JSON Schema differs from reviewed contract."
            )
        return self.manager.verify(package) is True


class RuntimeBoundNativeLocalRLAttestor:
    """Verify exact native runtime identities before schema projection."""

    def attest(
        self,
        package: Any,
        *,
        manager: Any,
        contract: NativeLocalRLRuntimeContract,
        projection_spec: NativeLocalRLProjectionSpec,
        verified_by: str,
        verified_at: datetime,
        attestation_id: str,
        runtime_receipt_id: str,
        projection_receipt_id: str,
    ) -> RuntimeBoundNativeLocalRLPackageAttestation:
        if (
            projection_spec.spec_id != contract.projection_spec_id
            or projection_spec.spec_hash != contract.projection_spec_hash
        ):
            raise ValueError(
                "Native local-RL projection spec differs from runtime contract."
            )
        verifier = _ContractVerifier(contract, manager)
        schema_attestation = SchemaBoundNativeLocalRLAttestor().attest(
            package,
            verifier=verifier,
            projector=PydanticNativeLocalRLProjector(projection_spec),
            verified_by=verified_by,
            verified_at=verified_at,
            attestation_id=attestation_id,
            projection_receipt_id=projection_receipt_id,
        )
        receipt_payload = {
            "receipt_id": runtime_receipt_id,
            "contract_id": contract.contract_id,
            "contract_hash": contract.contract_hash,
            "package_type_verified": True,
            "package_schema_verified": True,
            "manager_type_verified": True,
            "native_package_verified": True,
            "verified_by": verified_by,
            "verified_at": verified_at,
        }
        runtime_receipt = NativeLocalRLRuntimeReceipt(
            **receipt_payload,
            receipt_hash=program_payload_hash(receipt_payload),
        )
        payload = {
            "attestation_id": attestation_id,
            "runtime_contract": contract,
            "runtime_receipt": runtime_receipt,
            "schema_attestation": schema_attestation,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return RuntimeBoundNativeLocalRLPackageAttestation(
            **payload,
            attestation_hash=program_payload_hash(payload),
        )


__all__ = [
    "EvoagentLocalRLPackageProjector",
    "NativeLocalRLRuntimeContract",
    "NativeLocalRLRuntimeContractBuilder",
    "NativeLocalRLRuntimeReceipt",
    "RuntimeBoundNativeLocalRLAttestor",
    "RuntimeBoundNativeLocalRLPackageAttestation",
]
