from pathlib import Path

import pytest

from evoagent.continual import (
    ContinualComponent,
    ContinualLoopAction,
    ContinualTaskRole,
    SQLiteContinualSnapshotRegistry,
    ContinualRegistryConflict,
    BoundedObservablePolicyOptimizer,
    UnifiedContinualEvaluator,
    UnifiedCounterfactualRunner,
    build_action_policy,
    build_memory_record,
    build_loop_policy,
    build_policy_optimization_config,
    build_router_policy,
    build_router_rule,
    build_unified_snapshot,
    decide_loop_action,
    trace_policy_observation,
)
from evoagent.lab import UnifiedContinualEvolutionLab


def test_unified_continual_lab_evolves_all_runtime_components(tmp_path: Path):
    result = UnifiedContinualEvolutionLab(tmp_path).run()

    assert result.overall_scores == (0.0, 0.4, 0.6, 0.8, 1.0)
    assert result.changed_components == (
        ContinualComponent.SKILL,
        ContinualComponent.MEMORY,
        ContinualComponent.ROUTER,
        ContinualComponent.POLICY,
    )
    assert result.attribution_components == (
        ContinualComponent.SKILL,
        ContinualComponent.ROUTER,
        ContinualComponent.POLICY,
    )
    assert result.final_role_scores == {
        ContinualTaskRole.RETENTION: 1.0,
        ContinualTaskRole.TRANSFER: 1.0,
        ContinualTaskRole.ADVERSARIAL: 1.0,
        ContinualTaskRole.COMPOSITION: 1.0,
    }
    assert result.final_regression_count == 0
    assert result.final_forgetting_rate == 0.0
    assert result.final_safety_violation_count == 0
    assert result.policy_parameter_delta_l2 > 0.0
    assert result.registry_revision == 4
    assert result.loop_actions == (
        ContinualLoopAction.CONTINUE,
        ContinualLoopAction.CONTINUE,
        ContinualLoopAction.CONTINUE,
        ContinualLoopAction.CONTINUE,
        ContinualLoopAction.STOP_SUCCESS,
    )
    assert result.resumed is False
    assert result.optimizer_invoked is True


def test_unified_continual_lab_resumes_without_retraining_or_duplicate_events(
    tmp_path: Path,
):
    first = UnifiedContinualEvolutionLab(tmp_path).run()
    resumed = UnifiedContinualEvolutionLab(tmp_path).run()

    assert resumed.resumed is True
    assert resumed.optimizer_invoked is False
    assert resumed.result_hash == first.result_hash
    assert resumed.snapshot_hashes == first.snapshot_hashes
    assert resumed.decision_hashes == first.decision_hashes
    assert resumed.loop_decision_hashes == first.loop_decision_hashes
    assert resumed.registry_revision == first.registry_revision
    assert resumed.registry_event_count == first.registry_event_count


def test_verified_memory_changes_routing_without_raw_task_storage(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path)
    a0 = lab._initial_snapshot()
    a1 = lab._skill_candidate(a0)
    a2 = lab._memory_candidate(a1)
    task = next(
        item.task
        for item in lab._evaluation_manifest().tasks
        if item.task.task_id == "heldout:memory-transfer"
    )

    from evoagent.continual import UnifiedDocumentAgentRuntime

    runtime = UnifiedDocumentAgentRuntime(tmp_path / "runtime", seed=43)
    before = trace_policy_observation(runtime.run(task, a1))
    after = trace_policy_observation(runtime.run(task, a2))

    assert before["router_source"] == "default"
    assert before["selected_skill_ids"] == ("document_inspector",)
    assert after["router_source"] == "verified_memory"
    assert after["selected_skill_ids"] == ("document_writer",)
    serialized = a2.memory.model_dump_json()
    assert task.input["content"] not in serialized
    assert task.input["target_path"] not in serialized


def test_multiple_successful_component_repairs_escalate_as_conflict(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path)
    a0 = lab._initial_snapshot()
    a1 = lab._skill_candidate(a0)
    memory_candidate = lab._memory_candidate(a1)
    task = next(
        item.task
        for item in lab._evaluation_manifest().tasks
        if item.task.task_id == "heldout:memory-transfer"
    )
    router = build_router_policy(
        a1.router.policy_id,
        version=a1.router.version + 1,
        rules=(
            *a1.router.rules,
            build_router_rule(
                "route-memory-transfer-alternative",
                task_type=task.task_type,
                required_tags=("capability:write-verify",),
                skill_ids=("document_writer",),
                priority=100,
            ),
        ),
        default_skill_ids=a1.router.default_skill_ids,
        parent=a1.router,
    )
    router_candidate = build_unified_snapshot(
        lineage_id=a1.lineage_id,
        snapshot_id="A2-router-alternative",
        round_index=2,
        model_id=a1.model_id,
        skills=a1.skills,
        router=router,
        memory=a1.memory,
        action_policy=a1.action_policy,
        runtime_hash=a1.runtime_hash,
        tool_contract_hash=a1.tool_contract_hash,
        verifier_hash=a1.verifier_hash,
        creator_id="router-planner",
        parent=a1,
        changed_component=ContinualComponent.ROUTER,
        evidence_hashes=("1" * 64,),
    )

    report = UnifiedCounterfactualRunner(tmp_path / "conflict", seed=43).run(
        task,
        a1,
        {
            ContinualComponent.MEMORY: memory_candidate,
            ContinualComponent.ROUTER: router_candidate,
        },
        report_id="memory-router-conflict",
    )

    assert report.conflict is True
    assert report.actionable is False
    assert report.supported_component is None
    assert report.reason == "causal_conflict"


def test_one_component_snapshot_transition_rejects_two_changes(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path)
    a0 = lab._initial_snapshot()
    a1 = lab._skill_candidate(a0)
    changed_policy = build_action_policy(
        a0.action_policy.policy_id,
        version=1,
        iteration=1,
        state_keys=a0.action_policy.state_keys,
        logits=((1.0, 0.0), *a0.action_policy.logits[1:]),
        parent=a0.action_policy,
    )
    with pytest.raises(ValueError, match="exactly one"):
        build_unified_snapshot(
            lineage_id=a0.lineage_id,
            snapshot_id="invalid-two-component-candidate",
            round_index=1,
            model_id=a0.model_id,
            skills=a1.skills,
            router=a0.router,
            memory=a0.memory,
            action_policy=changed_policy,
            runtime_hash=a0.runtime_hash,
            tool_contract_hash=a0.tool_contract_hash,
            verifier_hash=a0.verifier_hash,
            creator_id="invalid-planner",
            parent=a0,
            changed_component=ContinualComponent.SKILL,
        )


def test_registry_rejects_candidate_with_unbound_parent_hash(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path / "lab")
    parent = lab._initial_snapshot()
    candidate = lab._skill_candidate(parent)
    forged = candidate.model_copy(update={"parent_snapshot_hash": "0" * 64})
    registry = SQLiteContinualSnapshotRegistry(tmp_path / "registry.db")
    registry.register_initial(parent, actor_id="bootstrap-operator")

    with pytest.raises(ContinualRegistryConflict, match="exact active-parent"):
        registry.register_candidate(forged, actor_id="candidate-operator")


def test_failed_trace_cannot_create_verified_memory(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path)
    snapshot = lab._initial_snapshot()
    task = lab._training_memory_source_task()

    from evoagent.continual import UnifiedDocumentAgentRuntime

    trace = UnifiedDocumentAgentRuntime(tmp_path / "failed-source", seed=41).run(
        task,
        snapshot,
    )
    assert trace.verifier_passed is False
    with pytest.raises(ValueError, match="verifier-passed"):
        build_memory_record(
            "must-not-exist",
            capability_key="write-verify",
            source_task=task,
            source_trace=trace,
        )


def test_policy_optimizer_checks_episode_budget_before_rollout(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path / "lab")
    snapshot = lab._initial_snapshot()
    optimizer_root = tmp_path / "optimizer"
    config = build_policy_optimization_config(
        iterations=1,
        group_size=4,
        maximum_rollouts=4,
        maximum_episode_steps=1,
    )

    with pytest.raises(RuntimeError, match="lacks budget"):
        BoundedObservablePolicyOptimizer(optimizer_root).train(
            snapshot,
            (lab._training_adversarial_task(),),
            config=config,
            result_id="budget-precheck",
        )
    assert not (optimizer_root / "rollouts" / "episodes").exists()


def test_evaluator_hashes_snapshot_id_before_using_it_as_a_directory(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path / "lab")
    original = lab._initial_snapshot()
    snapshot = build_unified_snapshot(
        lineage_id=original.lineage_id,
        snapshot_id="a/../../escaped-evaluator",
        round_index=0,
        model_id=original.model_id,
        skills=original.skills,
        router=original.router,
        memory=original.memory,
        action_policy=original.action_policy,
        runtime_hash=original.runtime_hash,
        tool_contract_hash=original.tool_contract_hash,
        verifier_hash=original.verifier_hash,
        creator_id="path-boundary-test",
    )
    evaluation_root = tmp_path / "evaluation"

    UnifiedContinualEvaluator(evaluation_root).evaluate(
        snapshot,
        lab._evaluation_manifest(),
        report_id="path-boundary-report",
    )

    assert not (tmp_path / "escaped-evaluator").exists()
    assert all(item.name.startswith("snapshot-") for item in evaluation_root.iterdir())


def test_loop_policy_stops_on_budget_and_escalates_on_non_improvement(tmp_path: Path):
    lab = UnifiedContinualEvolutionLab(tmp_path / "lab")
    snapshot = lab._initial_snapshot()
    report = UnifiedContinualEvaluator(tmp_path / "evaluation").evaluate(
        snapshot,
        lab._evaluation_manifest(),
        report_id="loop-negative-control-report",
    )
    policy = build_loop_policy(
        target_score=1.0,
        maximum_rounds=1,
        maximum_non_improving_rounds=1,
    )

    budget = decide_loop_action(
        report,
        policy=policy,
        completed_rounds=1,
        consecutive_non_improving_rounds=0,
        decision_id="loop-budget-stop",
    )
    stalled = decide_loop_action(
        report,
        policy=policy,
        completed_rounds=0,
        consecutive_non_improving_rounds=1,
        decision_id="loop-stalled-escalation",
    )

    assert budget.action == ContinualLoopAction.STOP_BUDGET
    assert stalled.action == ContinualLoopAction.ESCALATE
