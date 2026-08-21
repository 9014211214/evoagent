from __future__ import annotations

from datetime import timedelta

import pytest

from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import (
    LocalRLPackageManager,
    ProgramLocalRLProjectionPackage,
    ProgramLocalRLProjectionPackageError,
    ProgramLocalRLProjectionPackageManager,
    build_program_local_rl_projection_spec,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.program_rl import (
    NativeLocalRLRuntimeContractBuilder,
    RuntimeBoundNativeLocalRLAttestor,
)


def _projection(tmp_path):
    result = LocalAgenticRLTrainingLab(
        tmp_path / "real-local-rl",
        source_commit="a" * 40,
    ).run()
    source = LocalRLPackageManager().load_file(result.package_path)
    manager = ProgramLocalRLProjectionPackageManager()
    projection = manager.build(
        source,
        projection_package_id="program-local-rl-projection:controlled-v2.3",
    )
    return source, manager, projection


def test_projection_is_derived_from_real_native_optimizer_package(tmp_path):
    source, manager, projection = _projection(tmp_path)

    assert manager.verify(projection) is True
    assert projection.source_package == source
    assert projection.source_package_hash == source.package_hash
    assert projection.local_rl_package_id == source.package_id
    assert projection.local_rl_package_hash == source.package_hash
    assert projection.local_rl_run_id == source.manifest.run_id
    assert projection.initial_checkpoint_hash == (
        source.training.initial_checkpoint.checkpoint_hash
    )
    assert projection.selected_checkpoint_hash == (
        source.decision.selected_checkpoint_hash
    )
    assert projection.optimizer_evidence_hash == source.training.result_hash
    assert projection.heldout_evaluation_hash == (
        source.decision.selected_report_hash
    )
    assert projection.unsafe_action_count == 0
    assert projection.regression_count == 0
    assert projection.checkpoint_promotion_authorized is False
    assert projection.production_activation_authorized is False
    assert projection.production_deployment_authorized is False


def test_runtime_contract_binds_exact_projection_package_and_manager(tmp_path):
    source, manager, projection = _projection(tmp_path)
    spec = build_program_local_rl_projection_spec(
        created_by="independent-program-projection-schema-reviewer",
        created_at=source.created_at + timedelta(seconds=1),
    )
    contract = NativeLocalRLRuntimeContractBuilder().build(
        package_type=ProgramLocalRLProjectionPackage,
        manager_type=ProgramLocalRLProjectionPackageManager,
        projection_spec=spec,
        reviewed_by="independent-native-runtime-contract-reviewer",
        reviewed_at=source.created_at + timedelta(seconds=2),
        contract_id="native-local-rl-runtime-contract:controlled-v2.3",
    )
    runtime = RuntimeBoundNativeLocalRLAttestor().attest(
        projection,
        manager=manager,
        contract=contract,
        projection_spec=spec,
        verified_by="independent-native-runtime-package-verifier",
        verified_at=source.created_at + timedelta(seconds=3),
        attestation_id="native-local-rl-runtime-attestation:controlled-v2.3",
        runtime_receipt_id="native-local-rl-runtime-receipt:controlled-v2.3",
        projection_receipt_id=(
            "native-local-rl-projection-receipt:controlled-v2.3"
        ),
    )

    projected = runtime.schema_attestation.base_attestation.projection
    assert projected.local_rl_package_hash == source.package_hash
    assert projected.selected_checkpoint_hash == (
        source.decision.selected_checkpoint_hash
    )
    assert projected.optimizer_evidence_hash == source.training.result_hash
    assert runtime.runtime_contract.contract_hash == contract.contract_hash
    assert runtime.runtime_receipt.native_package_verified is True
    assert runtime.checkpoint_promotion_authorized is False
    assert runtime.production_activation_authorized is False


def test_rehashed_flat_checkpoint_substitution_is_rejected(tmp_path):
    _, manager, projection = _projection(tmp_path)
    payload = projection.model_dump(
        mode="json",
        exclude={"projection_package_hash"},
    )
    payload["selected_checkpoint_hash"] = "f" * 64
    forged = projection.model_copy(
        update={
            "selected_checkpoint_hash": "f" * 64,
            "projection_package_hash": canonical_sha256(payload),
        }
    )

    with pytest.raises(
        ProgramLocalRLProjectionPackageError,
        match="recomputed native evidence",
    ):
        manager.verify(forged)


def test_rehashed_nested_native_package_substitution_is_rejected(tmp_path):
    source, manager, projection = _projection(tmp_path)
    forged_source_payload = source.model_dump(
        mode="json",
        exclude={"package_hash"},
    )
    forged_source_payload["trainer_id"] = "forged-native-trainer"
    forged_source = source.model_copy(
        update={
            "trainer_id": "forged-native-trainer",
            "package_hash": canonical_sha256(forged_source_payload),
        }
    )
    forged_payload = projection.model_dump(
        mode="json",
        exclude={"projection_package_hash"},
    )
    forged_payload["source_package"] = forged_source.model_dump(mode="json")
    forged_payload["source_package_hash"] = forged_source.package_hash
    forged = projection.model_copy(
        update={
            "source_package": forged_source,
            "source_package_hash": forged_source.package_hash,
            "projection_package_hash": canonical_sha256(forged_payload),
        }
    )

    with pytest.raises(ValueError, match="reproducible|trainer|audit"):
        manager.verify(forged)
