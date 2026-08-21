from datetime import timedelta

from evoagent import __version__
from evoagent.lab import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import LocalRLPackageManager
from evoagent.program_rl import (
    AttestedProgramLocalRLPackageManager,
    EvoagentLocalRLPackageAttestor,
    EvoagentLocalRLPackageProjector,
    LocalRLExecutionBudget,
    ProgramLocalRLAdapter,
    ProgramLocalRLPackageManager,
)
from tests.test_program_local_rl_adapter import _running_program


def test_verified_native_package_binds_to_program(tmp_path):
    program, generation, head, checkpoint, governed = _running_program(tmp_path / "p")
    plan = generation.plan
    assert plan is not None
    intent_at = head.updated_at + timedelta(seconds=1)

    lab = LocalAgenticRLTrainingLab(tmp_path / "rl", source_commit="a" * 40)
    lab.CREATED_AT = intent_at + timedelta(seconds=2)
    lab.DECIDED_AT = lab.CREATED_AT + timedelta(minutes=5)
    native = LocalRLPackageManager().load_file(lab.run().package_path)
    native_manager = LocalRLPackageManager()
    projected = EvoagentLocalRLPackageProjector(native_manager).project(native)

    adapter = ProgramLocalRLAdapter()
    intent = adapter.build_intent(
        generation=generation,
        head=head,
        checkpoint=checkpoint,
        signal=program.signal,
        attribution=program.attribution,
        governed_actor_ids=governed,
        local_rl_run_id=projected.local_rl_run_id,
        optimizer_config_hash=projected.optimizer_config_hash,
        training_task_set_hash=projected.training_task_set_hash,
        heldout_task_set_hash=projected.heldout_task_set_hash,
        created_by="native-intent-builder",
        created_at=intent_at,
    )
    authorization = adapter.authorize(
        intent,
        generation_plan=plan,
        budget=LocalRLExecutionBudget(
            max_iterations=projected.usage.iterations,
            max_rollouts=projected.usage.rollouts,
            max_tokens=projected.usage.tokens,
            max_cost_usd=projected.usage.cost_usd,
        ),
        authorized_by="native-optimizer-authorizer",
        authorized_at=intent_at + timedelta(seconds=1),
        expires_at=native.created_at + timedelta(hours=1),
    )
    result = adapter.bind_result(
        intent,
        authorization,
        local_rl_package_id=projected.local_rl_package_id,
        local_rl_package_hash=projected.local_rl_package_hash,
        initial_checkpoint_hash=projected.initial_checkpoint_hash,
        selected_checkpoint_hash=projected.selected_checkpoint_hash,
        optimizer_evidence_hash=projected.optimizer_evidence_hash,
        heldout_evaluation_hash=projected.heldout_evaluation_hash,
        usage=projected.usage,
        heldout_reward_delta=projected.heldout_reward_delta,
        heldout_success_delta=projected.heldout_success_delta,
        unsafe_action_count=projected.unsafe_action_count,
        regression_count=projected.regression_count,
        executed_by=native.trainer_id,
        started_at=native.manifest.created_at,
        completed_at=native.created_at,
    )
    base_manager = ProgramLocalRLPackageManager()
    base = base_manager.build(
        package_id="program-local-rl:verified-native",
        framework_version=__version__,
        source_repository="https://github.com/9014211214/evoagent",
        source_commit="a" * 40,
        third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
        intent=intent,
        authorization=authorization,
        result=result,
        created_at=native.created_at + timedelta(seconds=1),
    )
    attestation = EvoagentLocalRLPackageAttestor().attest(
        native,
        manager=native_manager,
        verified_by="native-package-verifier",
        verified_at=base.created_at + timedelta(seconds=1),
        attestation_id="native-attestation:verified-package",
    )
    attested = AttestedProgramLocalRLPackageManager().build(
        package_id="attested-program-local-rl:verified-native",
        base_package=base,
        native_attestation=attestation,
        bound_by="native-result-binder",
        bound_at=attestation.verified_at + timedelta(seconds=1),
        created_at=attestation.verified_at + timedelta(seconds=2),
    )

    assert native_manager.verify(native) is True
    assert base_manager.verify(base) is True
    assert AttestedProgramLocalRLPackageManager().verify(attested) is True
    assert result.local_rl_package_hash == native.package_hash
    assert projected.usage.iterations == 24
    assert projected.usage.rollouts == 2_304
    assert projected.unsafe_action_count == 0
    assert projected.regression_count == 0
    assert attested.checkpoint_promotion_performed is False
    assert attested.production_activation_performed is False
