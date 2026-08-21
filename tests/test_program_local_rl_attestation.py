from datetime import timedelta

import pytest

from evoagent.program_rl.attestation import (
    NativeLocalRLAttestor,
    NativeLocalRLProjection,
)
from evoagent.program_rl.attested_package import (
    AttestedProgramLocalRLPackageError,
    AttestedProgramLocalRLPackageManager,
)
from tests.test_program_local_rl_adapter import _binding


class _Verifier:
    def __init__(self, passed=True):
        self.passed = passed
        self.called = False

    def verify(self, package):
        self.called = True
        return self.passed


class _Projector:
    def __init__(self, projection):
        self.projection = projection
        self.called = False

    def project(self, package):
        self.called = True
        return self.projection


def _projection(binding):
    intent = binding.intent
    result = binding.result
    return NativeLocalRLProjection(
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
        usage=result.usage,
        heldout_reward_delta=result.heldout_reward_delta,
        heldout_success_delta=result.heldout_success_delta,
        unsafe_action_count=result.unsafe_action_count,
        regression_count=result.regression_count,
    )


def _attested(tmp_path):
    _, _, _, binding = _binding(tmp_path)
    verifier = _Verifier()
    projector = _Projector(_projection(binding))
    attestation = NativeLocalRLAttestor().attest(
        object(),
        verifier=verifier,
        projector=projector,
        verified_by="native-local-rl-package-verifier",
        verified_at=binding.result.completed_at + timedelta(seconds=1),
        attestation_id="native-local-rl-attestation:program:g1",
    )
    manager = AttestedProgramLocalRLPackageManager()
    package = manager.build(
        package_id="attested-program-local-rl-package:program:g1",
        base_package=binding,
        native_attestation=attestation,
        bound_by="program-local-rl-result-binder",
        bound_at=attestation.verified_at + timedelta(seconds=1),
        created_at=attestation.verified_at + timedelta(seconds=2),
    )
    return verifier, projector, manager, package


def test_native_package_verifier_must_run_before_program_binding(tmp_path):
    verifier, projector, manager, package = _attested(tmp_path)

    assert verifier.called is True
    assert projector.called is True
    assert manager.verify(package) is True
    assert package.native_attestation.native_package_verified is True
    assert package.checkpoint_promotion_performed is False
    assert package.production_activation_performed is False


def test_failed_native_package_verification_produces_no_attestation(tmp_path):
    _, _, _, binding = _binding(tmp_path)
    verifier = _Verifier(passed=False)
    projector = _Projector(_projection(binding))

    with pytest.raises(ValueError, match="verification did not pass"):
        NativeLocalRLAttestor().attest(
            object(),
            verifier=verifier,
            projector=projector,
            verified_by="native-local-rl-package-verifier",
            verified_at=binding.result.completed_at + timedelta(seconds=1),
            attestation_id="native-local-rl-attestation:rejected",
        )
    assert verifier.called is True
    assert projector.called is False


def test_attested_package_rejects_projection_and_role_substitution(tmp_path):
    _, _, manager, package = _attested(tmp_path)
    projection = package.native_attestation.projection.model_copy(
        update={"optimizer_config_hash": "f" * 64}
    )
    attestation = package.native_attestation.model_copy(
        update={"projection": projection}
    )
    with pytest.raises(
        AttestedProgramLocalRLPackageError,
        match="differs from the Program optimization intent",
    ):
        manager.verify(package.model_copy(update={"native_attestation": attestation}))

    overlapping = package.attested_result.model_copy(
        update={"bound_by": package.native_attestation.verified_by}
    )
    with pytest.raises(
        AttestedProgramLocalRLPackageError,
        match="binder overlaps",
    ):
        manager.verify(package.model_copy(update={"attested_result": overlapping}))
