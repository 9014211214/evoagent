from datetime import timedelta

import pytest

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.attested_package import (
    AttestedProgramLocalRLPackageManager,
)
from evoagent.program_rl.schema_attestation import (
    NativeLocalRLProjectionSpec,
    PydanticNativeLocalRLProjector,
    SchemaBoundNativeLocalRLAttestor,
)
from evoagent.program_rl.schema_attested_package import (
    SchemaAttestedProgramLocalRLPackageError,
    SchemaAttestedProgramLocalRLPackageManager,
)
from tests.test_program_local_rl_adapter import _binding
from tests.test_program_local_rl_attestation import _Verifier


def _native_payload(binding):
    intent = binding.intent
    result = binding.result
    return {
        "package": {
            "id": result.local_rl_package_id,
            "hash": result.local_rl_package_hash,
        },
        "run": {"id": intent.local_rl_run_id},
        "optimizer": {
            "config_hash": intent.optimizer_config_hash,
            "evidence_hash": result.optimizer_evidence_hash,
            "usage": {
                "iterations": result.usage.iterations,
                "rollouts": result.usage.rollouts,
                "tokens": result.usage.tokens,
                "cost_usd": result.usage.cost_usd,
            },
        },
        "tasks": {
            "training_hash": intent.training_task_set_hash,
            "heldout_hash": intent.heldout_task_set_hash,
        },
        "checkpoints": {
            "initial_hash": result.initial_checkpoint_hash,
            "selected_hash": result.selected_checkpoint_hash,
        },
        "heldout": {
            "evaluation_hash": result.heldout_evaluation_hash,
            "reward_delta": result.heldout_reward_delta,
            "success_delta": result.heldout_success_delta,
            "unsafe_action_count": result.unsafe_action_count,
            "regression_count": result.regression_count,
        },
    }


def _spec(binding, *, created_by="native-schema-reviewer", paths=None):
    created_at = binding.result.completed_at - timedelta(minutes=1)
    path_map = paths or {
        "local_rl_package_id": ("package", "id"),
        "local_rl_package_hash": ("package", "hash"),
        "local_rl_run_id": ("run", "id"),
        "optimizer_config_hash": ("optimizer", "config_hash"),
        "training_task_set_hash": ("tasks", "training_hash"),
        "heldout_task_set_hash": ("tasks", "heldout_hash"),
        "initial_checkpoint_hash": ("checkpoints", "initial_hash"),
        "selected_checkpoint_hash": ("checkpoints", "selected_hash"),
        "optimizer_evidence_hash": ("optimizer", "evidence_hash"),
        "heldout_evaluation_hash": ("heldout", "evaluation_hash"),
        "iterations": ("optimizer", "usage", "iterations"),
        "rollouts": ("optimizer", "usage", "rollouts"),
        "tokens": ("optimizer", "usage", "tokens"),
        "cost_usd": ("optimizer", "usage", "cost_usd"),
        "heldout_reward_delta": ("heldout", "reward_delta"),
        "heldout_success_delta": ("heldout", "success_delta"),
        "unsafe_action_count": ("heldout", "unsafe_action_count"),
        "regression_count": ("heldout", "regression_count"),
    }
    payload = {
        "spec_id": "native-local-rl-projection-spec:v1",
        "schema_name": "evoagent-local-rl-binding-fixture",
        "schema_version": "1.0",
        "paths": path_map,
        "created_by": created_by,
        "created_at": created_at,
    }
    return NativeLocalRLProjectionSpec(
        **payload,
        spec_hash=program_payload_hash(payload),
    )


def _schema_attested(tmp_path, *, spec_author="native-schema-reviewer"):
    _, _, _, binding = _binding(tmp_path)
    native_payload = _native_payload(binding)
    spec = _spec(binding, created_by=spec_author)
    schema_attestation = SchemaBoundNativeLocalRLAttestor().attest(
        native_payload,
        verifier=_Verifier(),
        projector=PydanticNativeLocalRLProjector(spec),
        verified_by="native-local-rl-package-verifier",
        verified_at=binding.result.completed_at + timedelta(seconds=1),
        attestation_id="native-local-rl-attestation:schema-bound",
        projection_receipt_id="native-local-rl-projection-receipt:v1",
    )
    attested = AttestedProgramLocalRLPackageManager().build(
        package_id="attested-program-local-rl-package:schema-bound",
        base_package=binding,
        native_attestation=schema_attestation.base_attestation,
        bound_by="program-local-rl-result-binder",
        bound_at=schema_attestation.base_attestation.verified_at
        + timedelta(seconds=1),
        created_at=schema_attestation.base_attestation.verified_at
        + timedelta(seconds=2),
    )
    manager = SchemaAttestedProgramLocalRLPackageManager()
    final = manager.build(
        package_id="schema-attested-program-local-rl-package:v1",
        attested_package=attested,
        schema_attestation=schema_attestation,
        created_at=attested.created_at + timedelta(seconds=1),
    )
    return manager, final, binding, native_payload


def test_reviewed_projection_schema_is_part_of_final_evidence(tmp_path):
    manager, package, _, _ = _schema_attested(tmp_path)

    assert manager.verify(package) is True
    assert package.schema_attestation.projection_spec.spec_hash
    assert package.schema_attestation.projection_receipt.native_package_source_hash
    assert package.checkpoint_promotion_performed is False
    assert package.production_activation_performed is False


def test_projection_path_substitution_fails_before_attestation(tmp_path):
    _, _, _, binding = _binding(tmp_path)
    native_payload = _native_payload(binding)
    paths = dict(_spec(binding).paths)
    paths["training_task_set_hash"] = paths["heldout_task_set_hash"]
    spec = _spec(binding, paths=paths)

    with pytest.raises(ValueError, match="training and held-out sets overlap"):
        SchemaBoundNativeLocalRLAttestor().attest(
            native_payload,
            verifier=_Verifier(),
            projector=PydanticNativeLocalRLProjector(spec),
            verified_by="native-local-rl-package-verifier",
            verified_at=binding.result.completed_at + timedelta(seconds=1),
            attestation_id="native-local-rl-attestation:path-substitution",
            projection_receipt_id="native-local-rl-projection-receipt:forged",
        )


def test_projection_schema_author_must_be_independent(tmp_path):
    _, _, _, binding = _binding(tmp_path)
    governed_author = binding.intent.governed_actor_ids[0]
    with pytest.raises(
        SchemaAttestedProgramLocalRLPackageError,
        match="schema author overlaps",
    ):
        _schema_attested(
            tmp_path / "overlap",
            spec_author=governed_author,
        )
