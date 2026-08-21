from datetime import timedelta

import pytest
from pydantic import BaseModel, ConfigDict

from evoagent import __version__
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import EvolutionProgramPackageManager
from evoagent.program.hashing import program_payload_hash
from evoagent.local_rl.program_adapter import (
    ProgramLocalRLBindingError,
    ProgramLocalRLBindingPackageManager,
    build_program_local_rl_binding_package,
    build_program_local_rl_evidence,
    build_program_local_rl_execution_authorization,
    build_program_local_rl_intent,
)


class _FakeLocalRLPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str
    run_id: str
    initial_checkpoint_hash: str
    selected_checkpoint_hash: str
    held_out_evaluation_hash: str
    optimizer_audit_checkpoint_hash: str
    checkpoint_promotion_authorized: bool = False
    activation_authorized: bool = False
    production_deployment_performed: bool = False
    upload_performed: bool = False
    package_hash: str


def _program_package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="3" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def _local_package(*, activation_authorized=False):
    payload = {
        "package_id": "local-rl-package:program-binding",
        "run_id": "local-rl-run:program-binding",
        "initial_checkpoint_hash": "1" * 64,
        "selected_checkpoint_hash": "2" * 64,
        "held_out_evaluation_hash": "3" * 64,
        "optimizer_audit_checkpoint_hash": "4" * 64,
        "checkpoint_promotion_authorized": False,
        "activation_authorized": activation_authorized,
        "production_deployment_performed": False,
        "upload_performed": False,
    }
    return _FakeLocalRLPackage(
        **payload,
        package_hash=program_payload_hash(payload),
    )


def _binding_inputs(tmp_path):
    package = _program_package(tmp_path)
    parent = package.generations[0].outcome
    plan = package.generations[1].plan
    assert parent is not None
    assert plan is not None
    intent = build_program_local_rl_intent(
        intent_id="program-local-rl-intent:g1",
        policy=package.policy,
        parent_outcome=parent,
        signal=package.signal,
        attribution=package.attribution,
        decision=package.decisions[0],
        plan=plan,
        local_rl_run_id="local-rl-run:program-binding",
        local_rl_spec={
            "optimizer": "bounded-group-relative-policy-gradient",
            "maximum_iterations": 4,
            "maximum_rollouts": 64,
            "held_out_selection_required": True,
        },
        created_by="local-rl-binding-controller",
        created_at=plan.created_at + timedelta(seconds=1),
    )
    authorization = build_program_local_rl_execution_authorization(
        intent,
        authorization_id="program-local-rl-authorization:g1",
        approval_actor_ids=(
            "local-rl-execution-reviewer-a",
            "local-rl-execution-reviewer-b",
        ),
        approved_at=intent.created_at + timedelta(seconds=1),
        max_optimizer_iterations=4,
        max_rollouts=64,
    )
    return package, intent, authorization


def test_program_local_rl_binding_is_evidence_only_and_restart_safe(tmp_path):
    _, intent, authorization = _binding_inputs(tmp_path)
    local_package = _local_package()
    evidence = build_program_local_rl_evidence(
        intent,
        authorization,
        local_package,
        evidence_id="program-local-rl-evidence:g1",
        initial_checkpoint_hash=local_package.initial_checkpoint_hash,
        selected_checkpoint_hash=local_package.selected_checkpoint_hash,
        held_out_evaluation_hash=local_package.held_out_evaluation_hash,
        optimizer_audit_checkpoint_hash=(
            local_package.optimizer_audit_checkpoint_hash
        ),
        optimizer_iterations=4,
        rollout_count=64,
        unsafe_action_count=0,
        regression_count=0,
        selected_strictly_improved=True,
        evidence_producer_id="independent-local-rl-evaluator",
        completed_at=authorization.approved_at + timedelta(seconds=1),
    )
    binding = build_program_local_rl_binding_package(
        package_id="program-local-rl-binding-package:g1",
        framework_version=__version__,
        source_repository=(
            "https://github.com/9014211214/evoagent"
        ),
        source_commit="3" * 40,
        intent=intent,
        authorization=authorization,
        evidence=evidence,
        created_at=evidence.completed_at + timedelta(seconds=1),
    )

    assert authorization.local_optimizer_execution_authorized is True
    assert authorization.foundation_model_training_authorized is False
    assert evidence.held_out_gate_passed is True
    assert evidence.foundation_model_weights_modified is False
    assert binding.optimizer_execution_authorized_by_package is False
    assert binding.checkpoint_promotion_authorized is False
    assert binding.activation_authorized is False
    assert binding.production_deployment_authorized is False

    manager = ProgramLocalRLBindingPackageManager()
    path = manager.export_file(binding, tmp_path / "binding.json")
    before = path.read_bytes()
    assert manager.load_file(path) == binding
    assert manager.export_file(binding, path) == path
    assert path.read_bytes() == before


def test_local_rl_execution_cannot_exceed_separate_authorization(tmp_path):
    _, intent, authorization = _binding_inputs(tmp_path)
    local_package = _local_package()

    with pytest.raises(ProgramLocalRLBindingError, match="exceeded"):
        build_program_local_rl_evidence(
            intent,
            authorization,
            local_package,
            evidence_id="program-local-rl-evidence:over-budget",
            initial_checkpoint_hash=local_package.initial_checkpoint_hash,
            selected_checkpoint_hash=local_package.selected_checkpoint_hash,
            held_out_evaluation_hash=local_package.held_out_evaluation_hash,
            optimizer_audit_checkpoint_hash=(
                local_package.optimizer_audit_checkpoint_hash
            ),
            optimizer_iterations=5,
            rollout_count=64,
            unsafe_action_count=0,
            regression_count=0,
            selected_strictly_improved=True,
            evidence_producer_id="independent-local-rl-evaluator",
            completed_at=authorization.approved_at + timedelta(seconds=1),
        )

    with pytest.raises(ProgramLocalRLBindingError, match="exceeded"):
        build_program_local_rl_evidence(
            intent,
            authorization,
            local_package,
            evidence_id="program-local-rl-evidence:unsafe",
            initial_checkpoint_hash=local_package.initial_checkpoint_hash,
            selected_checkpoint_hash=local_package.selected_checkpoint_hash,
            held_out_evaluation_hash=local_package.held_out_evaluation_hash,
            optimizer_audit_checkpoint_hash=(
                local_package.optimizer_audit_checkpoint_hash
            ),
            optimizer_iterations=4,
            rollout_count=64,
            unsafe_action_count=1,
            regression_count=0,
            selected_strictly_improved=True,
            evidence_producer_id="independent-local-rl-evaluator",
            completed_at=authorization.approved_at + timedelta(seconds=1),
        )


def test_adapter_rejects_local_package_with_implicit_activation(tmp_path):
    _, intent, authorization = _binding_inputs(tmp_path)
    local_package = _local_package(activation_authorized=True)

    with pytest.raises(ProgramLocalRLBindingError, match="forbidden activation"):
        build_program_local_rl_evidence(
            intent,
            authorization,
            local_package,
            evidence_id="program-local-rl-evidence:implicit-activation",
            initial_checkpoint_hash=local_package.initial_checkpoint_hash,
            selected_checkpoint_hash=local_package.selected_checkpoint_hash,
            held_out_evaluation_hash=local_package.held_out_evaluation_hash,
            optimizer_audit_checkpoint_hash=(
                local_package.optimizer_audit_checkpoint_hash
            ),
            optimizer_iterations=4,
            rollout_count=64,
            unsafe_action_count=0,
            regression_count=0,
            selected_strictly_improved=True,
            evidence_producer_id="independent-local-rl-evaluator",
            completed_at=authorization.approved_at + timedelta(seconds=1),
        )


def test_adapter_rejects_unbound_checkpoint_hash(tmp_path):
    _, intent, authorization = _binding_inputs(tmp_path)
    local_package = _local_package()

    with pytest.raises(ProgramLocalRLBindingError, match="not all present"):
        build_program_local_rl_evidence(
            intent,
            authorization,
            local_package,
            evidence_id="program-local-rl-evidence:foreign-checkpoint",
            initial_checkpoint_hash=local_package.initial_checkpoint_hash,
            selected_checkpoint_hash="f" * 64,
            held_out_evaluation_hash=local_package.held_out_evaluation_hash,
            optimizer_audit_checkpoint_hash=(
                local_package.optimizer_audit_checkpoint_hash
            ),
            optimizer_iterations=4,
            rollout_count=64,
            unsafe_action_count=0,
            regression_count=0,
            selected_strictly_improved=True,
            evidence_producer_id="independent-local-rl-evaluator",
            completed_at=authorization.approved_at + timedelta(seconds=1),
        )
