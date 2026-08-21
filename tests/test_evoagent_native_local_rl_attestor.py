from datetime import timedelta

import pytest

from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import LocalRLPackageManager
from evoagent.program_rl.evoagent_native import (
    EvoagentLocalRLPackageAttestationError,
    EvoagentLocalRLPackageAttestor,
    EvoagentLocalRLPackageProjector,
)


def _package(tmp_path):
    result = LocalAgenticRLTrainingLab(
        tmp_path / "native-local-rl",
        source_commit="e" * 40,
    ).run()
    manager = LocalRLPackageManager()
    return manager, manager.load_file(result.package_path)


def test_attestor_verifies_real_native_package(tmp_path):
    manager, package = _package(tmp_path)
    attestor = EvoagentLocalRLPackageAttestor()
    attestation = attestor.attest(
        package,
        manager=manager,
        verified_by="independent-native-package-verifier",
        verified_at=package.created_at + timedelta(seconds=1),
        attestation_id="native-local-rl-attestation:real-package",
    )

    assert attestor.verify(
        package,
        manager=manager,
        attestation=attestation,
    ) is True
    assert attestation.projection.local_rl_package_hash == package.package_hash
    assert attestation.projection.optimizer_evidence_hash == (
        package.training.result_hash
    )
    assert attestation.projection.heldout_evaluation_hash == (
        package.decision.selected_report_hash
    )
    assert attestation.checkpoint_promotion_authorized is False
    assert attestation.production_activation_authorized is False


def test_attestor_rejects_native_evidence_producer(tmp_path):
    manager, package = _package(tmp_path)

    with pytest.raises(
        EvoagentLocalRLPackageAttestationError,
        match="overlaps",
    ):
        EvoagentLocalRLPackageAttestor().attest(
            package,
            manager=manager,
            verified_by=package.trainer_id,
            verified_at=package.created_at + timedelta(seconds=1),
            attestation_id="native-local-rl-attestation:invalid-role",
        )


def test_projector_rejects_selector_trainer_overlap(tmp_path):
    lab = LocalAgenticRLTrainingLab(
        tmp_path / "overlapping-native-roles",
        source_commit="f" * 40,
    )
    lab.DECISION_ACTOR_ID = lab.TRAINER_ID
    result = lab.run()
    manager = LocalRLPackageManager()
    package = manager.load_file(result.package_path)

    assert manager.verify(package) is True
    with pytest.raises(ValueError, match="must be independent"):
        EvoagentLocalRLPackageProjector(manager).project(package)


def test_projector_rejects_non_monotonic_native_audit(tmp_path):
    lab = LocalAgenticRLTrainingLab(
        tmp_path / "non-monotonic-native-audit",
        source_commit="1" * 40,
    )
    lab.DECIDED_AT = lab.CREATED_AT - timedelta(seconds=1)
    result = lab.run()
    manager = LocalRLPackageManager()
    package = manager.load_file(result.package_path)

    assert manager.verify(package) is True
    with pytest.raises(ValueError, match="timestamps are not monotonic"):
        EvoagentLocalRLPackageProjector(manager).project(package)
