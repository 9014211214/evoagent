from datetime import timedelta

import pytest
from pydantic import BaseModel, ConfigDict

import evoagent.local_rl as local_rl_module
from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import (
    LocalRLPackageError,
    LocalRLPackageManager,
    LocalRLPackageManifest,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attested_package import (
    AttestedProgramLocalRLPackageManager,
)
from evoagent.program_rl.native_contract import (
    EvoagentLocalRLPackageProjector,
    NativeLocalRLRuntimeContractBuilder,
    RuntimeBoundNativeLocalRLAttestor,
)
from evoagent.program_rl.runtime_attested_package import (
    RuntimeAttestedProgramLocalRLPackageError,
)
from evoagent.program_rl.runtime_attested_package_final import (
    RuntimeAttestedProgramLocalRLPackageManager,
)
from evoagent.program_rl.schema_attestation import (
    NativeLocalRLProjectionSpec,
)
from evoagent.program_rl.schema_attested_package import (
    SchemaAttestedProgramLocalRLPackageManager,
)
from tests.test_program_local_rl_adapter import _binding


class _NativePackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    local_rl_package_id: str
    local_rl_package_hash: str
    local_rl_run_id: str
    optimizer_config_hash: str
    training_task_set_hash: str
    heldout_task_set_hash: str
    initial_checkpoint_hash: str
    selected_checkpoint_hash: str
    optimizer_evidence_hash: str
    heldout_evaluation_hash: str
    iterations: int
    rollouts: int
    tokens: int
    cost_usd: float
    heldout_reward_delta: float
    heldout_success_delta: float
    unsafe_action_count: int
    regression_count: int


class _NativeManager:
    def verify(self, package):
        return isinstance(package, _NativePackage)


_NativePackage.__module__ = "evoagent.local_rl"
_NativeManager.__module__ = "evoagent.local_rl"


def _install_runtime_types(monkeypatch):
    monkeypatch.setattr(
        local_rl_module,
        "_NativePackage",
        _NativePackage,
        raising=False,
    )
    monkeypatch.setattr(
        local_rl_module,
        "_NativeManager",
        _NativeManager,
        raising=False,
    )


def _spec(binding, *, created_by="native-projection-schema-reviewer"):
    paths = {
        "local_rl_package_id": ("local_rl_package_id",),
        "local_rl_package_hash": ("local_rl_package_hash",),
        "local_rl_run_id": ("local_rl_run_id",),
        "optimizer_config_hash": ("optimizer_config_hash",),
        "training_task_set_hash": ("training_task_set_hash",),
        "heldout_task_set_hash": ("heldout_task_set_hash",),
        "initial_checkpoint_hash": ("initial_checkpoint_hash",),
        "selected_checkpoint_hash": ("selected_checkpoint_hash",),
        "optimizer_evidence_hash": ("optimizer_evidence_hash",),
        "heldout_evaluation_hash": ("heldout_evaluation_hash",),
        "iterations": ("iterations",),
        "rollouts": ("rollouts",),
        "tokens": ("tokens",),
        "cost_usd": ("cost_usd",),
        "heldout_reward_delta": ("heldout_reward_delta",),
        "heldout_success_delta": ("heldout_success_delta",),
        "unsafe_action_count": ("unsafe_action_count",),
        "regression_count": ("regression_count",),
    }
    payload = {
        "spec_id": "native-local-rl-runtime-projection:v1",
        "schema_name": "native-local-rl-runtime-fixture",
        "schema_version": "1.0",
        "paths": paths,
        "created_by": created_by,
        "created_at": binding.result.completed_at - timedelta(minutes=2),
    }
    return NativeLocalRLProjectionSpec(
        **payload,
        spec_hash=program_payload_hash(payload),
    )


def _native(binding):
    intent = binding.intent
    result = binding.result
    return _NativePackage(
        local_rl_package_id=result.local_rl_package_id,
        local_rl_package_hash=result.local_rl_package_hash,
        local_rl_run_id=intent.local_rl_run_id,
        optimizer_config_hash=intent.optimizer_config_hash,
        training_task_set_hash=intent.training_task_set_hash,
        heldout_task_set_hash=intent.heldout_task_set_hash,
        initial_checkpoint_hash=result.initial_checkpoint_hash,
        selected_checkpoint_hash=result.selected_checkpoint_hash,
        optimizer_evidence_hash=result.optimizer_evidence_hash,
        heldout_evaluation_hash=result.heldout_evaluation_hash,
        iterations=result.usage.iterations,
        rollouts=result.usage.rollouts,
        tokens=result.usage.tokens,
        cost_usd=result.usage.cost_usd,
        heldout_reward_delta=result.heldout_reward_delta,
        heldout_success_delta=result.heldout_success_delta,
        unsafe_action_count=result.unsafe_action_count,
        regression_count=result.regression_count,
    )


def _runtime_attested(
    tmp_path,
    monkeypatch,
    *,
    runtime_reviewer="native-runtime-contract-reviewer",
):
    _install_runtime_types(monkeypatch)
    _, _, _, binding = _binding(tmp_path)
    spec = _spec(binding)
    contract = NativeLocalRLRuntimeContractBuilder().build(
        package_type=_NativePackage,
        manager_type=_NativeManager,
        projection_spec=spec,
        reviewed_by=runtime_reviewer,
        reviewed_at=binding.result.completed_at - timedelta(minutes=1),
        contract_id="native-local-rl-runtime-contract:v1",
    )
    runtime = RuntimeBoundNativeLocalRLAttestor().attest(
        _native(binding),
        manager=_NativeManager(),
        contract=contract,
        projection_spec=spec,
        verified_by="native-local-rl-runtime-verifier",
        verified_at=binding.result.completed_at + timedelta(seconds=1),
        attestation_id="native-local-rl-runtime-attestation:v1",
        runtime_receipt_id="native-local-rl-runtime-receipt:v1",
        projection_receipt_id="native-local-rl-runtime-projection-receipt:v1",
    )
    attested = AttestedProgramLocalRLPackageManager().build(
        package_id="attested-program-local-rl-package:runtime",
        base_package=binding,
        native_attestation=runtime.schema_attestation.base_attestation,
        bound_by="program-local-rl-result-binder",
        bound_at=runtime.runtime_receipt.verified_at + timedelta(seconds=1),
        created_at=runtime.runtime_receipt.verified_at + timedelta(seconds=2),
    )
    schema_attested = SchemaAttestedProgramLocalRLPackageManager().build(
        package_id="schema-attested-program-local-rl-package:runtime",
        attested_package=attested,
        schema_attestation=runtime.schema_attestation,
        created_at=attested.created_at + timedelta(seconds=1),
    )
    manager = RuntimeAttestedProgramLocalRLPackageManager()
    final = manager.build(
        package_id="runtime-attested-program-local-rl-package:v1",
        schema_attested_package=schema_attested,
        runtime_attestation=runtime,
        accepted_by="program-local-rl-evidence-acceptor",
        accepted_at=schema_attested.created_at + timedelta(seconds=1),
    )
    return manager, final, binding, spec, contract


def _verified_evoagent_package(tmp_path):
    lab = LocalAgenticRLTrainingLab(
        tmp_path / "actual-local-rl",
        source_commit="d" * 40,
    )
    result = lab.run()
    manager = LocalRLPackageManager()
    package = manager.load_file(result.package_path)
    return manager, package


def test_runtime_contract_binds_package_manager_and_schema(
    tmp_path,
    monkeypatch,
):
    manager, package, _, _, contract = _runtime_attested(
        tmp_path,
        monkeypatch,
    )

    assert manager.verify(package) is True
    assert contract.package_module == "evoagent.local_rl"
    assert contract.manager_module == "evoagent.local_rl"
    assert contract.package_schema_hash
    assert package.checkpoint_promotion_performed is False
    assert package.production_activation_performed is False


def test_runtime_attestor_rejects_package_or_manager_substitution(
    tmp_path,
    monkeypatch,
):
    _install_runtime_types(monkeypatch)
    _, _, _, binding = _binding(tmp_path)
    spec = _spec(binding)
    contract = NativeLocalRLRuntimeContractBuilder().build(
        package_type=_NativePackage,
        manager_type=_NativeManager,
        projection_spec=spec,
        reviewed_by="native-runtime-contract-reviewer",
        reviewed_at=binding.result.completed_at - timedelta(minutes=1),
        contract_id="native-local-rl-runtime-contract:substitution",
    )

    class OtherPackage(_NativePackage):
        pass

    class OtherManager(_NativeManager):
        pass

    with pytest.raises(TypeError, match="package type differs"):
        RuntimeBoundNativeLocalRLAttestor().attest(
            OtherPackage(**_native(binding).model_dump()),
            manager=_NativeManager(),
            contract=contract,
            projection_spec=spec,
            verified_by="native-local-rl-runtime-verifier",
            verified_at=binding.result.completed_at + timedelta(seconds=1),
            attestation_id="native-local-rl-runtime-attestation:package-forgery",
            runtime_receipt_id="native-local-rl-runtime-receipt:package-forgery",
            projection_receipt_id="native-local-rl-projection-receipt:package-forgery",
        )

    with pytest.raises(TypeError, match="manager type differs"):
        RuntimeBoundNativeLocalRLAttestor().attest(
            _native(binding),
            manager=OtherManager(),
            contract=contract,
            projection_spec=spec,
            verified_by="native-local-rl-runtime-verifier",
            verified_at=binding.result.completed_at + timedelta(seconds=1),
            attestation_id="native-local-rl-runtime-attestation:manager-forgery",
            runtime_receipt_id="native-local-rl-runtime-receipt:manager-forgery",
            projection_receipt_id="native-local-rl-projection-receipt:manager-forgery",
        )


def test_runtime_contract_reviewer_must_be_independent(
    tmp_path,
    monkeypatch,
):
    _, _, _, binding = _binding(tmp_path)
    overlapping_reviewer = binding.intent.governed_actor_ids[0]

    with pytest.raises(
        RuntimeAttestedProgramLocalRLPackageError,
        match="reviewer overlaps",
    ):
        _runtime_attested(
            tmp_path / "overlap",
            monkeypatch,
            runtime_reviewer=overlapping_reviewer,
        )


def test_concrete_projector_consumes_verified_evoagent_package(tmp_path):
    manager, package = _verified_evoagent_package(tmp_path)
    projection = EvoagentLocalRLPackageProjector(manager).project(package)

    selected_report = next(
        report
        for report in package.candidate_evaluations
        if report.report_hash == package.decision.selected_report_hash
    )
    assert projection.local_rl_package_id == package.package_id
    assert projection.local_rl_package_hash == package.package_hash
    assert projection.local_rl_run_id == package.manifest.run_id
    assert projection.optimizer_config_hash == (
        EvoagentLocalRLPackageProjector.optimizer_config_hash(package)
    )
    assert projection.training_task_set_hash == (
        EvoagentLocalRLPackageProjector.task_set_hash(
            package.manifest.training_tasks
        )
    )
    assert projection.heldout_task_set_hash == (
        package.baseline_evaluation.task_manifest_hash
    )
    assert projection.initial_checkpoint_hash == (
        package.training.initial_checkpoint.checkpoint_hash
    )
    assert projection.selected_checkpoint_hash == (
        package.decision.selected_checkpoint_hash
    )
    assert projection.optimizer_evidence_hash == package.training.result_hash
    assert projection.heldout_evaluation_hash == selected_report.report_hash
    assert projection.usage.iterations == package.training.usage.iterations
    assert projection.usage.rollouts == package.training.usage.rollouts
    assert projection.usage.tokens == 0
    assert projection.usage.cost_usd == 0.0
    assert projection.heldout_reward_delta > 0.0
    assert projection.heldout_success_delta == 1.0
    assert projection.unsafe_action_count == 0
    assert projection.regression_count == 0
    assert projection.training_task_set_hash != projection.heldout_task_set_hash
    assert projection.native_package_hash_recomputed is True
    assert projection.optimizer_recomputed is True
    assert projection.heldout_evaluation_recomputed is True
    assert projection.checkpoint_selection_recomputed is True


def test_concrete_projector_requires_exact_manager_and_true_verification(
    tmp_path,
    monkeypatch,
):
    manager, package = _verified_evoagent_package(tmp_path)

    class OtherManager(LocalRLPackageManager):
        pass

    with pytest.raises(TypeError, match="exact LocalRLPackageManager"):
        EvoagentLocalRLPackageProjector(OtherManager())

    monkeypatch.setattr(
        LocalRLPackageManager,
        "verify",
        lambda self, candidate: False,
    )
    with pytest.raises(ValueError, match="verification did not pass"):
        EvoagentLocalRLPackageProjector(manager).project(package)


def test_concrete_projector_rejects_tampered_native_evaluation(tmp_path):
    manager, package = _verified_evoagent_package(tmp_path)
    selected_index = next(
        index
        for index, report in enumerate(package.candidate_evaluations)
        if report.report_hash == package.decision.selected_report_hash
    )
    selected = package.candidate_evaluations[selected_index]
    forged_selected = selected.model_copy(update={"unsafe_action_count": 1})
    candidates = list(package.candidate_evaluations)
    candidates[selected_index] = forged_selected
    forged_package = package.model_copy(
        update={"candidate_evaluations": tuple(candidates)}
    )
    forged_payload = forged_package.model_dump(
        mode="json",
        exclude={"package_hash"},
    )
    forged_package = forged_package.model_copy(
        update={"package_hash": canonical_sha256(forged_payload)}
    )

    with pytest.raises(
        LocalRLPackageError,
        match="candidate evaluations are not reproducible",
    ):
        EvoagentLocalRLPackageProjector(manager).project(forged_package)
