from __future__ import annotations

from pathlib import Path

import pytest

from evoagent.lab import (
    AcceptedLocalPolicyPromotionLab,
    ProgramLocalRLAcceptanceLab,
    ProgramLocalRLAcceptedEvidenceError,
    ProgramLocalRLAcceptedEvidenceManager,
)
from evoagent.local_policy import LocalPolicyPromotionPackageManager
from evoagent.local_rl import LocalRLPackageManager
from evoagent.model_registry.models import canonical_sha256
from evoagent.program_rl import ProgramLocalRLAcceptanceManager


def _accepted(tmp_path):
    lab = ProgramLocalRLAcceptanceLab(
        tmp_path / "accepted-program-local-rl",
        source_commit="a" * 40,
    )
    result = lab.run()
    bundle = ProgramLocalRLAcceptedEvidenceManager().load_file(
        result.bundle_path
    )
    return lab, result, bundle


def test_real_local_optimizer_reaches_complete_program_acceptance(tmp_path):
    lab, result, bundle = _accepted(tmp_path)

    assert result.resumed is False
    assert result.optimizer_invoked is True
    assert result.local_policy_optimization_performed is True
    assert result.foundation_model_training_performed is False
    assert result.production_activation_performed is False
    assert result.production_deployment_performed is False
    assert result.external_rollout_performed is False
    assert Path(result.bundle_path).is_file()
    assert result.bundle_hash == bundle.bundle_hash
    assert result.running_attestation_hash == (
        bundle.running_attestation.attestation_hash
    )
    assert result.native_local_rl_package_hash == (
        bundle.native_local_rl_package.package_hash
    )
    assert result.selected_checkpoint_hash == (
        bundle.native_local_rl_package.decision.selected_checkpoint_hash
    )
    assert bundle.native_local_rl_package.training.initial_checkpoint.checkpoint_hash != (
        bundle.native_local_rl_package.decision.selected_checkpoint_hash
    )
    assert LocalRLPackageManager().verify(
        bundle.native_local_rl_package
    ) is True
    assert ProgramLocalRLAcceptedEvidenceManager.verify(bundle) is True
    assert ProgramLocalRLAcceptanceManager.verify(
        bundle.fully_attested_package,
        bundle.trusted_anchors,
        bundle.acceptance_receipt,
    ) is True
    assert bundle.acceptance_receipt.evidence_accepted is True
    assert bundle.acceptance_receipt.checkpoint_promotion_authorized is False
    assert bundle.acceptance_receipt.production_activation_authorized is False


def test_accepted_chain_can_enter_v2_2_promotion_only_lifecycle(tmp_path):
    _, _, bundle = _accepted(tmp_path)
    promotion = AcceptedLocalPolicyPromotionLab(
        tmp_path / "accepted-policy-promotion",
        accepted_program_package=bundle.fully_attested_package,
        trusted_anchors=bundle.trusted_anchors,
        acceptance_receipt=bundle.acceptance_receipt,
        source_commit="b" * 40,
        perform_rollback=False,
    )

    first = promotion.run()
    packaged = LocalPolicyPromotionPackageManager().load_file(
        first.package_path
    )
    before = Path(first.package_path).read_bytes()
    second = promotion.run()

    assert first.resumed is False
    assert second.resumed is True
    assert first.active_policy_id == promotion.candidate_policy_id
    assert first.active_revision == 1
    assert first.promotion_completed is True
    assert first.rollback_completed is False
    assert first.local_policy_pointer_mutation_only is True
    assert first.foundation_model_weights_updated is False
    assert first.production_activation_performed is False
    assert first.production_deployment_performed is False
    assert first.package_hash == second.package_hash
    assert Path(second.package_path).read_bytes() == before
    assert LocalPolicyPromotionPackageManager.verify(packaged) is True


def test_second_acceptance_invocation_is_fully_read_only(tmp_path):
    lab, first, first_bundle = _accepted(tmp_path)
    bundle_before = Path(first.bundle_path).read_bytes()
    native_before = Path(
        lab.native_local_rl_root / "local-agentic-rl-package.json"
    ).read_bytes()

    second = lab.run()
    second_bundle = ProgramLocalRLAcceptedEvidenceManager().load_file(
        second.bundle_path
    )

    assert second.resumed is True
    assert second.optimizer_invoked is False
    assert second.bundle_hash == first.bundle_hash
    assert second.fully_attested_package_hash == (
        first.fully_attested_package_hash
    )
    assert second.acceptance_receipt_hash == first.acceptance_receipt_hash
    assert second_bundle == first_bundle
    assert Path(second.bundle_path).read_bytes() == bundle_before
    assert Path(
        lab.native_local_rl_root / "local-agentic-rl-package.json"
    ).read_bytes() == native_before


def test_rehashed_native_checkpoint_substitution_is_rejected(tmp_path):
    _, _, bundle = _accepted(tmp_path)
    native = bundle.native_local_rl_package
    forged_decision = native.decision.model_copy(
        update={"selected_checkpoint_hash": "f" * 64}
    )
    native_payload = native.model_dump(mode="json", exclude={"package_hash"})
    native_payload["decision"] = forged_decision.model_dump(mode="json")
    forged_native = native.model_copy(
        update={
            "decision": forged_decision,
            "package_hash": canonical_sha256(native_payload),
        }
    )
    bundle_payload = bundle.model_dump(mode="json", exclude={"bundle_hash"})
    bundle_payload["native_local_rl_package"] = forged_native.model_dump(
        mode="json"
    )
    forged_bundle = bundle.model_copy(
        update={
            "native_local_rl_package": forged_native,
            "bundle_hash": canonical_sha256(bundle_payload),
        }
    )

    with pytest.raises(ValueError, match="selection|reproducible|checkpoint"):
        ProgramLocalRLAcceptedEvidenceManager.verify(forged_bundle)


def test_conflicting_existing_bundle_is_not_overwritten(tmp_path):
    _, _, bundle = _accepted(tmp_path)
    manager = ProgramLocalRLAcceptedEvidenceManager()
    target = tmp_path / "conflicting-accepted-evidence.json"
    original = b'{"foreign":"evidence"}\n'
    target.write_bytes(original)

    with pytest.raises(
        ProgramLocalRLAcceptedEvidenceError,
        match="differs from immutable bundle",
    ):
        manager.export_file(bundle, target)

    assert target.read_bytes() == original
