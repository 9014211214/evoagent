from datetime import timedelta

import pytest

from evoagent import __version__
from evoagent.lab import (
    DEFAULT_THIRD_PARTY_LOCK_HASH,
    MultiGenerationEvolutionProgramLab,
)
from evoagent.program.hashing import program_payload_hash
from evoagent.program import (
    EvolutionProgramPackageManager,
    GenerationRecord,
    GenerationStatus,
    ProgramCheckpoint,
    ProgramState,
)
from evoagent.program_rl import (
    LocalRLExecutionBudget,
    LocalRLExecutionUsage,
    ProgramLocalRLAdapter,
    ProgramLocalRLPackageError,
    ProgramLocalRLPackageManager,
)


def _running_program(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="a" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    g1 = package.generations[1]
    assert g1.plan is not None
    started_event = next(
        item
        for item in package.program_events
        if item.event_type.value == "generation_started"
    )
    running = GenerationRecord(
        program_id=g1.program_id,
        generation_id=g1.generation_id,
        generation_index=g1.generation_index,
        parent_generation_id=g1.parent_generation_id,
        status=GenerationStatus.RUNNING,
        plan=g1.plan,
        outcome=None,
        campaign_id=g1.campaign_id,
        created_at=g1.created_at,
        updated_at=started_event.created_at,
    )
    head = package.final_head.model_copy(
        update={
            "state": ProgramState.GENERATION_RUNNING,
            "current_generation_index": g1.generation_index,
            "active_generation_id": g1.generation_id,
            "revision": package.final_head.revision - 2,
            "last_decision_id": package.decisions[0].decision_id,
            "updated_at": started_event.created_at,
        }
    )
    checkpoint = ProgramCheckpoint(
        event_count=started_event.sequence,
        head_hash=started_event.event_hash,
    )
    governed = (
        package.signal.evidence_producer_id,
        package.attribution.attributor_id,
        g1.plan.created_by,
        package.campaign_events[1].actor_id,
        *(item.actor_id for item in package.generation_approvals),
    )
    return package, running, head, checkpoint, tuple(dict.fromkeys(governed))


def _binding(tmp_path):
    package, generation, head, checkpoint, governed = _running_program(tmp_path)
    adapter = ProgramLocalRLAdapter()
    intent_time = head.updated_at + timedelta(seconds=1)
    intent = adapter.build_intent(
        generation=generation,
        head=head,
        checkpoint=checkpoint,
        signal=package.signal,
        attribution=package.attribution,
        governed_actor_ids=governed,
        local_rl_run_id="local-rl-run:program:g1",
        optimizer_config_hash="1" * 64,
        training_task_set_hash="2" * 64,
        heldout_task_set_hash="3" * 64,
        created_by="local-rl-intent-builder",
        created_at=intent_time,
    )
    authorization = adapter.authorize(
        intent,
        generation_plan=generation.plan,
        budget=LocalRLExecutionBudget(
            max_iterations=4,
            max_rollouts=32,
            max_tokens=0,
            max_cost_usd=0.0,
        ),
        authorized_by="local-rl-execution-authorizer",
        authorized_at=intent_time + timedelta(seconds=1),
        expires_at=intent_time + timedelta(hours=1),
    )
    result = adapter.bind_result(
        intent,
        authorization,
        local_rl_package_id="local-rl-package:program:g1",
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
        executed_by="local-rl-offline-executor",
        started_at=authorization.authorized_at + timedelta(seconds=1),
        completed_at=authorization.authorized_at + timedelta(minutes=1),
    )
    manager = ProgramLocalRLPackageManager()
    binding = manager.build(
        package_id="program-local-rl-binding:program:g1",
        framework_version=__version__,
        source_repository="https://github.com/9014211214/evoagent",
        source_commit="a" * 40,
        third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
        intent=intent,
        authorization=authorization,
        result=result,
        created_at=result.completed_at + timedelta(seconds=1),
    )
    return adapter, manager, generation, binding


def test_program_local_rl_binding_has_no_promotion_or_activation_authority(tmp_path):
    _, manager, _, binding = _binding(tmp_path)

    assert manager.verify(binding) is True
    assert binding.intent.optimizer_execution_authorized is False
    assert binding.authorization.optimizer_execution_authorized is True
    assert binding.authorization.checkpoint_promotion_authorized is False
    assert binding.result.checkpoint_promotion_authorized is False
    assert binding.checkpoint_promotion_performed is False
    assert binding.production_activation_performed is False
    assert binding.foundation_model_weights_updated is False
    assert binding.result.selected_checkpoint_hash != (
        binding.result.initial_checkpoint_hash
    )


def test_program_local_rl_requires_separate_execution_authorizer(tmp_path):
    package, generation, head, checkpoint, governed = _running_program(tmp_path)
    adapter = ProgramLocalRLAdapter()
    intent = adapter.build_intent(
        generation=generation,
        head=head,
        checkpoint=checkpoint,
        signal=package.signal,
        attribution=package.attribution,
        governed_actor_ids=governed,
        local_rl_run_id="local-rl-run:authorization-control",
        optimizer_config_hash="1" * 64,
        training_task_set_hash="2" * 64,
        heldout_task_set_hash="3" * 64,
        created_by="local-rl-intent-builder",
        created_at=head.updated_at + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="authorizer must be independent"):
        adapter.authorize(
            intent,
            generation_plan=generation.plan,
            budget=LocalRLExecutionBudget(
                max_iterations=1,
                max_rollouts=1,
                max_tokens=0,
                max_cost_usd=0.0,
            ),
            authorized_by=package.attribution.attributor_id,
            authorized_at=intent.created_at + timedelta(seconds=1),
        )


def _rehash_result(result):
    payload = result.model_dump(mode="json", exclude={"result_hash"})
    return result.model_copy(update={"result_hash": program_payload_hash(payload)})


def test_program_local_rl_rejects_budget_safety_and_actor_tampering(tmp_path):
    _, manager, _, binding = _binding(tmp_path)

    forged_usage = binding.result.usage.model_copy(
        update={"rollouts": binding.authorization.budget.max_rollouts + 1}
    )
    forged_result = _rehash_result(
        binding.result.model_copy(update={"usage": forged_usage})
    )
    with pytest.raises(ProgramLocalRLPackageError, match="execution budget"):
        manager.verify(binding.model_copy(update={"result": forged_result}))

    unsafe_result = _rehash_result(
        binding.result.model_copy(update={"unsafe_action_count": 1})
    )
    with pytest.raises(ProgramLocalRLPackageError, match="safe held-out"):
        manager.verify(binding.model_copy(update={"result": unsafe_result}))

    overlapping_result = _rehash_result(
        binding.result.model_copy(
            update={"executed_by": binding.authorization.authorized_by}
        )
    )
    with pytest.raises(ProgramLocalRLPackageError, match="executor overlaps"):
        manager.verify(binding.model_copy(update={"result": overlapping_result}))
