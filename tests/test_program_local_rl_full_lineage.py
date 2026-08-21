from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

import evoagent.local_rl as local_rl_module
from evoagent import __version__
from evoagent.lab import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import ProgramCheckpoint
from evoagent.program_rl import (
    AttestedProgramLocalRLPackageManager,
    FullyAttestedProgramLocalRLPackageManager,
    LocalRLExecutionBudget,
    LocalRLExecutionUsage,
    NativeLocalRLProjectionSpec,
    NativeLocalRLRuntimeContractBuilder,
    ProgramLocalRLAcceptanceError,
    ProgramLocalRLAcceptanceManager,
    ProgramLocalRLAdapter,
    ProgramLocalRLPackageManager,
    RunningAttestedProgramLocalRLPackageManager,
    RunningGenerationIntentBindingManager,
    RuntimeAttestedProgramLocalRLPackageManager,
    RuntimeBoundNativeLocalRLAttestor,
    SchemaAttestedProgramLocalRLPackageManager,
    build_trusted_anchors,
)
from tests.test_program_local_rl_adapter import _running_program


class _Roles(BaseModel):
    model_config = ConfigDict(frozen=True)

    actors: tuple[str, ...]

    def all_actor_ids(self) -> tuple[str, ...]:
        return self.actors


class _RunningAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    attestation_id: str
    program_id: str
    generation_id: str
    generation_index: int
    program_state: str
    program_head_revision: int
    program_checkpoint: ProgramCheckpoint
    campaign_id: str
    campaign_state: str
    campaign_revision: int
    campaign_checkpoint: ProgramCheckpoint
    plan_id: str
    plan_hash: str
    source_signal_id: str
    source_signal_hash: str
    attribution_receipt_id: str
    attribution_receipt_hash: str
    intervention_layer: object
    intervention_action: object
    parent_agent_identity_hash: str
    target_agent_identity_hash: str
    expected_release_package_hash: str
    expected_release_plan_hash: str
    roles: _Roles
    attested_by: str
    attested_at: datetime
    optimizer_execution_authorized: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    attestation_hash: str


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
        return type(package) is _NativePackage


_NativePackage.__module__ = "evoagent.local_rl"
_NativeManager.__module__ = "evoagent.local_rl"


def _install_native_types(monkeypatch):
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


def _running_attestation(tmp_path):
    package, generation, head, program_checkpoint, governed = _running_program(
        tmp_path
    )
    plan = generation.plan
    assert plan is not None
    role_actors = tuple(
        dict.fromkeys(
            (
                *governed,
                "independent-feedback-ingestor",
                "independent-authorization-actor",
                "independent-start-actor",
            )
        )
    )
    attested_at = head.updated_at + timedelta(seconds=1)
    payload = {
        "attestation_id": "running-generation-attestation:full-lineage:g1",
        "program_id": generation.program_id,
        "generation_id": generation.generation_id,
        "generation_index": generation.generation_index,
        "program_state": "generation_running",
        "program_head_revision": head.revision,
        "program_checkpoint": program_checkpoint,
        "campaign_id": generation.campaign_id,
        "campaign_state": "authorized",
        "campaign_revision": 5,
        "campaign_checkpoint": ProgramCheckpoint(
            event_count=6,
            head_hash="9" * 64,
        ),
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "source_signal_id": plan.source_signal_id,
        "source_signal_hash": plan.source_signal_hash,
        "attribution_receipt_id": plan.attribution_receipt_id,
        "attribution_receipt_hash": plan.attribution_receipt_hash,
        "intervention_layer": plan.intervention_layer,
        "intervention_action": plan.intervention_action,
        "parent_agent_identity_hash": plan.parent_agent_identity_hash,
        "target_agent_identity_hash": plan.target_agent_identity_hash,
        "expected_release_package_hash": plan.expected_release_package_hash,
        "expected_release_plan_hash": plan.expected_release_plan_hash,
        "roles": _Roles(actors=role_actors),
        "attested_by": "independent-running-generation-attestor",
        "attested_at": attested_at,
        "optimizer_execution_authorized": False,
        "checkpoint_promotion_authorized": False,
        "production_activation_authorized": False,
    }
    return (
        package,
        generation,
        _RunningAttestation(
            **payload,
            attestation_hash=program_payload_hash(payload),
        ),
    )


def _projection_spec(base_package, *, created_at):
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
        "spec_id": "native-local-rl-projection-spec:full-lineage",
        "schema_name": "native-local-rl-full-lineage",
        "schema_version": "1.0",
        "paths": paths,
        "created_by": "independent-native-schema-reviewer",
        "created_at": created_at,
    }
    return NativeLocalRLProjectionSpec(
        **payload,
        spec_hash=program_payload_hash(payload),
    )


def _native_package(base_package):
    intent = base_package.intent
    result = base_package.result
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


def _full_lineage(tmp_path, monkeypatch):
    _install_native_types(monkeypatch)
    _, generation, running_attestation = _running_attestation(tmp_path)
    plan = generation.plan
    assert plan is not None
    adapter = ProgramLocalRLAdapter()
    intent = adapter.build_intent_from_attestation(
        running_attestation,
        local_rl_run_id="local-rl-run:full-lineage",
        optimizer_config_hash="1" * 64,
        training_task_set_hash="2" * 64,
        heldout_task_set_hash="3" * 64,
        created_by="independent-local-rl-intent-builder",
        created_at=running_attestation.attested_at + timedelta(seconds=1),
    )
    intent_binding = RunningGenerationIntentBindingManager().build(
        intent,
        running_attestation,
    )
    authorization = adapter.authorize(
        intent,
        generation_plan=plan,
        budget=LocalRLExecutionBudget(
            max_iterations=4,
            max_rollouts=32,
            max_tokens=0,
            max_cost_usd=0.0,
        ),
        authorized_by="independent-optimizer-authorizer",
        authorized_at=intent.created_at + timedelta(seconds=1),
        expires_at=intent.created_at + timedelta(hours=1),
    )
    result = adapter.bind_result(
        intent,
        authorization,
        local_rl_package_id="native-local-rl-package:full-lineage",
        local_rl_package_hash="4" * 64,
        initial_checkpoint_hash="5" * 64,
        selected_checkpoint_hash="6" * 64,
        optimizer_evidence_hash="7" * 64,
        heldout_evaluation_hash="8" * 64,
        usage=LocalRLExecutionUsage(
            iterations=4,
            rollouts=32,
            tokens=0,
            cost_usd=0.0,
        ),
        heldout_reward_delta=0.25,
        heldout_success_delta=0.50,
        unsafe_action_count=0,
        regression_count=0,
        executed_by="independent-offline-optimizer-executor",
        started_at=authorization.authorized_at + timedelta(seconds=1),
        completed_at=authorization.authorized_at + timedelta(minutes=1),
    )
    base_manager = ProgramLocalRLPackageManager()
    base_package = base_manager.build(
        package_id="program-local-rl-package:full-lineage",
        framework_version=__version__,
        source_repository="https://github.com/9014211214/evoagent",
        source_commit="a" * 40,
        third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
        intent=intent,
        authorization=authorization,
        result=result,
        created_at=result.completed_at + timedelta(seconds=1),
    )
    running_package = RunningAttestedProgramLocalRLPackageManager().build(
        package_id="running-attested-local-rl-package:full-lineage",
        base_package=base_package,
        intent_binding=intent_binding,
        created_at=base_package.created_at + timedelta(seconds=1),
    )

    spec = _projection_spec(
        base_package,
        created_at=result.completed_at - timedelta(seconds=1),
    )
    contract = NativeLocalRLRuntimeContractBuilder().build(
        package_type=_NativePackage,
        manager_type=_NativeManager,
        projection_spec=spec,
        reviewed_by="independent-native-runtime-contract-reviewer",
        reviewed_at=result.completed_at + timedelta(seconds=1),
        contract_id="native-local-rl-runtime-contract:full-lineage",
    )
    runtime_attestation = RuntimeBoundNativeLocalRLAttestor().attest(
        _native_package(base_package),
        manager=_NativeManager(),
        contract=contract,
        projection_spec=spec,
        verified_by="independent-native-package-verifier",
        verified_at=result.completed_at + timedelta(seconds=2),
        attestation_id="native-local-rl-runtime-attestation:full-lineage",
        runtime_receipt_id="native-local-rl-runtime-receipt:full-lineage",
        projection_receipt_id="native-local-rl-projection-receipt:full-lineage",
    )
    attested_package = AttestedProgramLocalRLPackageManager().build(
        package_id="attested-program-local-rl-package:full-lineage",
        base_package=base_package,
        native_attestation=runtime_attestation.schema_attestation.base_attestation,
        bound_by="independent-program-result-binder",
        bound_at=runtime_attestation.runtime_receipt.verified_at
        + timedelta(seconds=1),
        created_at=runtime_attestation.runtime_receipt.verified_at
        + timedelta(seconds=2),
    )
    schema_package = SchemaAttestedProgramLocalRLPackageManager().build(
        package_id="schema-attested-local-rl-package:full-lineage",
        attested_package=attested_package,
        schema_attestation=runtime_attestation.schema_attestation,
        created_at=attested_package.created_at + timedelta(seconds=1),
    )
    runtime_package = RuntimeAttestedProgramLocalRLPackageManager().build(
        package_id="runtime-attested-local-rl-package:full-lineage",
        schema_attested_package=schema_package,
        runtime_attestation=runtime_attestation,
        accepted_by="independent-runtime-evidence-acceptor",
        accepted_at=schema_package.created_at + timedelta(seconds=1),
    )
    fully_manager = FullyAttestedProgramLocalRLPackageManager()
    fully_package = fully_manager.build(
        package_id="fully-attested-local-rl-package:full-lineage",
        running_attested_package=running_package,
        runtime_attested_package=runtime_package,
        accepted_by="independent-full-evidence-assembler",
        accepted_at=runtime_package.accepted_at + timedelta(seconds=1),
    )
    anchors = build_trusted_anchors(
        anchors_id="program-local-rl-trusted-anchors:full-lineage",
        running_attestation_hash=running_attestation.attestation_hash,
        program_checkpoint=running_attestation.program_checkpoint,
        campaign_checkpoint=running_attestation.campaign_checkpoint,
        native_runtime_contract_hash=contract.contract_hash,
        native_projection_spec_hash=spec.spec_hash,
        native_local_rl_package_hash=result.local_rl_package_hash,
        optimizer_evidence_hash=result.optimizer_evidence_hash,
        heldout_evaluation_hash=result.heldout_evaluation_hash,
        anchored_by="independent-external-anchor-store",
        anchored_at=fully_package.accepted_at + timedelta(seconds=1),
    )
    acceptance = ProgramLocalRLAcceptanceManager()
    receipt = acceptance.accept(
        fully_package,
        anchors,
        accepted_by="independent-final-evidence-acceptor",
        accepted_at=anchors.anchored_at + timedelta(seconds=1),
        receipt_id="program-local-rl-acceptance:full-lineage",
    )
    return fully_manager, acceptance, fully_package, anchors, receipt


def test_complete_program_to_native_rl_lineage_is_accepted(
    tmp_path,
    monkeypatch,
):
    fully_manager, acceptance, package, anchors, receipt = _full_lineage(
        tmp_path,
        monkeypatch,
    )

    assert fully_manager.verify(package) is True
    assert acceptance.verify(package, anchors, receipt) is True
    assert receipt.evidence_accepted is True
    assert receipt.checkpoint_promotion_authorized is False
    assert receipt.production_activation_authorized is False
    assert package.checkpoint_promotion_performed is False
    assert package.production_activation_performed is False
    assert package.official_benchmark_claimed is False


def test_nested_result_tamper_is_rejected_before_outer_hash(
    tmp_path,
    monkeypatch,
):
    fully_manager, _, package, _, _ = _full_lineage(tmp_path, monkeypatch)
    runtime = package.runtime_attested_package
    schema = runtime.schema_attested_package
    attested = schema.attested_package
    base = attested.base_package
    forged_result = base.result.model_copy(
        update={"heldout_reward_delta": -1.0}
    )
    forged_base = base.model_copy(update={"result": forged_result})
    forged_attested = attested.model_copy(update={"base_package": forged_base})
    forged_schema = schema.model_copy(update={"attested_package": forged_attested})
    forged_runtime = runtime.model_copy(
        update={"schema_attested_package": forged_schema}
    )
    forged = package.model_copy(
        update={"runtime_attested_package": forged_runtime}
    )

    with pytest.raises(ValueError, match="result hash mismatch|held-out"):
        fully_manager.verify(forged)


def test_coherent_running_anchor_rewrite_is_rejected_by_external_anchors(
    tmp_path,
    monkeypatch,
):
    _, acceptance, package, anchors, receipt = _full_lineage(
        tmp_path,
        monkeypatch,
    )
    running_package = package.running_attested_package
    binding = running_package.intent_binding
    embedded = dict(binding.running_attestation_payload)
    campaign_checkpoint = dict(embedded["campaign_checkpoint"])
    campaign_checkpoint["head_hash"] = "f" * 64
    embedded["campaign_checkpoint"] = campaign_checkpoint
    embedded["attestation_hash"] = program_payload_hash(
        {
            key: value
            for key, value in embedded.items()
            if key != "attestation_hash"
        }
    )
    forged_checkpoint = binding.campaign_checkpoint.model_copy(
        update={"head_hash": "f" * 64}
    )
    forged_binding_payload = binding.model_dump(
        mode="json",
        exclude={"binding_hash"},
    )
    forged_binding_payload.update(
        {
            "running_attestation_hash": embedded["attestation_hash"],
            "running_attestation_payload": embedded,
            "campaign_checkpoint": forged_checkpoint.model_dump(mode="json"),
        }
    )
    forged_binding = binding.model_copy(
        update={
            "running_attestation_hash": embedded["attestation_hash"],
            "running_attestation_payload": embedded,
            "campaign_checkpoint": forged_checkpoint,
            "binding_hash": program_payload_hash(forged_binding_payload),
        }
    )
    forged_running_payload = running_package.model_dump(
        mode="json",
        exclude={"package_hash"},
    )
    forged_running_payload["intent_binding"] = forged_binding.model_dump(
        mode="json"
    )
    forged_running = running_package.model_copy(
        update={
            "intent_binding": forged_binding,
            "package_hash": program_payload_hash(forged_running_payload),
        }
    )
    forged_full_payload = package.model_dump(
        mode="json",
        exclude={"package_hash"},
    )
    forged_full_payload["running_attested_package"] = (
        forged_running.model_dump(mode="json")
    )
    forged_full = package.model_copy(
        update={
            "running_attested_package": forged_running,
            "package_hash": program_payload_hash(forged_full_payload),
        }
    )

    with pytest.raises(
        ProgramLocalRLAcceptanceError,
        match="differs from independent external anchors",
    ):
        acceptance.verify(forged_full, anchors, receipt)
