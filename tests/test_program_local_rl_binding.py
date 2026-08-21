from __future__ import annotations

from datetime import timedelta

import pytest

from evoagent import __version__
from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.lab import (
    DEFAULT_THIRD_PARTY_LOCK_HASH,
    MultiGenerationEvolutionProgramLab,
)
from evoagent.local_rl import (
    IndependentLocalPolicyEvaluator,
    LocalGroupRelativePolicyOptimizer,
    LocalPolicyCheckpointSelector,
    LocalRLPackageManager,
    LocalRLTaskKind,
    ProgramLocalRLBindingError,
    ProgramLocalRLBindingManager,
    SQLiteLocalRLRepository,
    build_environment_contract,
    build_hyperparameters,
    build_local_rl_task,
    build_run_manifest,
    build_training_budget,
)
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    GenerationStatus,
    ProgramState,
    SQLiteEvolutionProgramRepository,
)


SOURCE_REPOSITORY = "https://github.com/9014211214/evoagent"
SOURCE_COMMIT = "c" * 40
TRAINER = "program-bound-local-rl-trainer"
EVALUATOR = "program-bound-local-rl-evaluator"
SELECTOR = "program-bound-local-rl-selector"
COMPLETION_ATTESTOR = "program-bound-local-rl-completion-attestor"


def _manifest(run_id, created_at, *, seed=17):
    environment = build_environment_contract()
    training_tasks = (
        build_local_rl_task(f"{run_id}:train:normal:1", LocalRLTaskKind.NORMAL),
        build_local_rl_task(f"{run_id}:train:normal:2", LocalRLTaskKind.NORMAL),
        build_local_rl_task(
            f"{run_id}:train:protected:1", LocalRLTaskKind.PROTECTED
        ),
        build_local_rl_task(
            f"{run_id}:train:protected:2", LocalRLTaskKind.PROTECTED
        ),
    )
    held_out_tasks = (
        build_local_rl_task(f"{run_id}:heldout:normal:1", LocalRLTaskKind.NORMAL),
        build_local_rl_task(f"{run_id}:heldout:normal:2", LocalRLTaskKind.NORMAL),
        build_local_rl_task(
            f"{run_id}:heldout:protected:1", LocalRLTaskKind.PROTECTED
        ),
        build_local_rl_task(
            f"{run_id}:heldout:protected:2", LocalRLTaskKind.PROTECTED
        ),
    )
    return build_run_manifest(
        run_id=run_id,
        created_at=created_at,
        environment=environment,
        training_tasks=training_tasks,
        held_out_tasks=held_out_tasks,
        hyperparameters=build_hyperparameters(
            learning_rate=0.4,
            clip_epsilon=0.2,
            entropy_coefficient=0.01,
            max_gradient_norm=1.0,
            update_epochs=4,
            group_size=24,
            seed=seed,
            retained_checkpoint_interval=2,
        ),
        budget=build_training_budget(
            maximum_iterations=24,
            maximum_rollouts=3_000,
            maximum_episode_steps=6_000,
            maximum_parameter_updates=200,
            maximum_wall_seconds=30.0,
        ),
    )


def _running_program(package, root):
    g0, g1 = package.generations
    d0 = package.decisions[0]
    plan = g1.plan
    assert g0.outcome is not None
    assert plan is not None

    repository = SQLiteEvolutionProgramRepository(root / "program.db")
    campaigns = SQLiteCampaignRepository(root / "campaign.db")
    controller = EvolutionProgramController(
        repository=repository,
        campaign_governance=CampaignGovernanceService(campaigns),
    )
    controller.register_from_release(
        package.drift_release_package,
        program_id=g0.program_id,
        policy=package.policy,
        generation_id=g0.generation_id,
        outcome_id=g0.outcome.outcome_id,
        created_by="program-bound-owner",
        created_at=g0.created_at,
    )
    signal, _ = controller.store_feedback(
        package.drift_release_package,
        program_id=g0.program_id,
        generation_index=0,
        signal_id=package.signal.signal_id,
        actor_id="program-bound-feedback-ingestor",
        created_at=package.signal.created_at,
    )
    attribution, _ = controller.store_attribution(
        g0.program_id,
        package.attribution,
        actor_id=package.attribution.attributor_id,
        created_at=package.attribution.created_at,
    )
    decision, _ = controller.decide(
        program_id=g0.program_id,
        generation_id=g0.generation_id,
        decision_id=d0.decision_id,
        decided_by=d0.decided_by,
        decided_at=d0.decided_at,
        signal=signal,
        attribution=attribution,
    )
    submission = controller.submit_generation(
        plan,
        evaluation_actor_id="program-bound-generation-evaluator",
        submitted_at=plan.created_at,
    )
    campaign = controller.approve_generation(
        submission.campaign.campaign_id,
        actor_id="program-bound-reviewer-a",
        reason="Independent Program evidence review passed.",
        expected_revision=submission.campaign.revision,
    )
    campaign = controller.approve_generation(
        campaign.campaign_id,
        actor_id="program-bound-reviewer-b",
        reason="Independent Program budget review passed.",
        expected_revision=campaign.revision,
    )
    controller.synchronize_authorization(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        actor_id="program-bound-authorization-sync",
    )
    authorized_head = repository.head(plan.program_id)
    controller.start_generation(
        program_id=plan.program_id,
        generation_id=plan.generation_id,
        campaign_id=campaign.campaign_id,
        expected_revision=authorized_head.revision,
        actor_id="program-bound-generation-operator",
    )
    head = repository.head(plan.program_id)
    generation = repository.get_generation(plan.program_id, plan.generation_id)
    assert head.state == ProgramState.GENERATION_RUNNING
    assert generation.status == GenerationStatus.RUNNING
    return {
        "repository": repository,
        "campaigns": campaigns,
        "program": repository.get_program(plan.program_id),
        "head": head,
        "generation": generation,
        "campaign": campaigns.get(campaign.campaign_id),
        "approvals": tuple(campaigns.approvals(campaign.campaign_id)),
        "policy": package.policy,
        "signal": signal,
        "attribution": attribution,
        "decision": decision,
        "plan": plan,
    }


def _local_package(
    root,
    manifest,
    *,
    registration_actor,
    trainer=TRAINER,
    evaluator_id=EVALUATOR,
    selector=SELECTOR,
):
    repository = SQLiteLocalRLRepository(root / f"{manifest.run_id}.db")
    registered_at = manifest.created_at + timedelta(seconds=2)
    repository.register_manifest(
        manifest,
        actor_id=registration_actor,
        now=registered_at,
    )
    training = LocalGroupRelativePolicyOptimizer().train(manifest)
    repository.store_training(
        training,
        actor_id=trainer,
        now=registered_at + timedelta(seconds=1),
    )
    evaluator = IndependentLocalPolicyEvaluator()
    baseline = evaluator.evaluate(
        manifest,
        training.initial_checkpoint,
        evaluator_id=evaluator_id,
        trainer_id=trainer,
    )
    candidates = tuple(
        evaluator.evaluate(
            manifest,
            checkpoint,
            evaluator_id=evaluator_id,
            trainer_id=trainer,
        )
        for checkpoint in training.retained_checkpoints
    )
    evaluated_at = registered_at + timedelta(seconds=2)
    repository.store_evaluations(
        manifest.run_id,
        baseline=baseline,
        candidates=candidates,
        actor_id=evaluator_id,
        now=evaluated_at,
    )
    decided_at = evaluated_at + timedelta(seconds=1)
    decision = LocalPolicyCheckpointSelector().decide(
        manifest,
        training,
        baseline,
        candidates,
        decision_id=f"{manifest.run_id}:selection",
        decision_actor_id=selector,
        decided_at=decided_at,
    )
    repository.store_decision(
        decision,
        actor_id=selector,
        now=decided_at,
    )
    repository.verify_state(manifest.run_id)
    return LocalRLPackageManager().build(
        package_id=f"{manifest.run_id}:package",
        created_at=decided_at + timedelta(seconds=1),
        framework_version=__version__,
        source_repository=SOURCE_REPOSITORY,
        source_commit=SOURCE_COMMIT,
        third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
        trainer_id=trainer,
        manifest=manifest,
        training=training,
        baseline_evaluation=baseline,
        candidate_evaluations=candidates,
        decision=decision,
        audit_events=repository.events(),
        audit_checkpoint=repository.checkpoint(),
    )


@pytest.fixture(scope="module")
def bound_context(tmp_path_factory):
    root = tmp_path_factory.mktemp("program-local-rl-binding")
    source = MultiGenerationEvolutionProgramLab(
        root / "source-program",
        source_commit=SOURCE_COMMIT,
    ).run()
    program_package = EvolutionProgramPackageManager().load_file(
        source.package_path
    )
    running = _running_program(program_package, root / "running-program")
    manifest = _manifest(
        "program-bound-local-rl-run",
        running["head"].updated_at + timedelta(seconds=1),
    )
    manager = ProgramLocalRLBindingManager()
    ticket = manager.build_ticket(
        ticket_id="program-local-rl-ticket:g1",
        authorized_at=manifest.created_at + timedelta(seconds=1),
        authorized_by="program-local-rl-authorizer",
        program=running["program"],
        head=running["head"],
        policy=running["policy"],
        signal=running["signal"],
        attribution=running["attribution"],
        continue_decision=running["decision"],
        generation_plan=running["plan"],
        generation=running["generation"],
        campaign=running["campaign"],
        approvals=running["approvals"],
        local_rl_manifest=manifest,
    )
    local_package = _local_package(
        root / "local-package",
        manifest,
        registration_actor=ticket.authorized_by,
    )
    bound_package = manager.build(
        package_id="program-bound-local-rl-package:g1",
        created_at=local_package.created_at + timedelta(seconds=2),
        framework_version=__version__,
        source_repository=SOURCE_REPOSITORY,
        source_commit=SOURCE_COMMIT,
        third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
        ticket=ticket,
        local_rl_package=local_package,
        receipt_id="program-local-rl-receipt:g1",
        completed_by=COMPLETION_ATTESTOR,
        completed_at=local_package.created_at + timedelta(seconds=1),
    )
    return {
        "root": root,
        "running": running,
        "manifest": manifest,
        "manager": manager,
        "ticket": ticket,
        "local_package": local_package,
        "bound_package": bound_package,
    }


def test_program_bound_local_rl_package_is_verifiable_and_non_activating(
    bound_context,
):
    package = bound_context["bound_package"]
    assert bound_context["manager"].verify(package) is True
    assert package.ticket.generation.status == GenerationStatus.RUNNING
    assert package.ticket.local_optimizer_execution_authorized is True
    assert package.ticket.selected_checkpoint_satisfies_generation_outcome is False
    assert package.ticket.release_evaluation_still_required is True
    assert package.receipt.local_optimizer_execution_completed is True
    assert package.receipt.strict_held_out_improvement_verified is True
    assert package.receipt.zero_unsafe_held_out_actions_verified is True
    assert package.receipt.checkpoint_promotion_authorized is False
    assert package.receipt.checkpoint_activation_authorized is False
    assert package.receipt.release_authorized is False
    assert package.receipt.production_deployment_authorized is False
    assert package.generation_outcome_not_satisfied is True
    assert package.release_evaluation_still_required is True


def test_ticket_rejects_non_running_generation(bound_context):
    ticket = bound_context["ticket"]
    forged_generation = ticket.generation.model_copy(
        update={"status": GenerationStatus.AUTHORIZED}
    )

    with pytest.raises(
        ValueError,
        match="exact running generation",
    ):
        bound_context["manager"].build_ticket(
            ticket_id="program-local-rl-ticket:non-running",
            authorized_at=ticket.authorized_at,
            authorized_by=ticket.authorized_by,
            program=ticket.program,
            head=ticket.head,
            policy=ticket.policy,
            signal=ticket.signal,
            attribution=ticket.attribution,
            continue_decision=ticket.continue_decision,
            generation_plan=ticket.generation_plan,
            generation=forged_generation,
            campaign=ticket.campaign,
            approvals=ticket.approvals,
            local_rl_manifest=ticket.local_rl_manifest,
        )


def test_binding_rejects_local_package_from_another_manifest(bound_context):
    ticket = bound_context["ticket"]
    other_manifest = _manifest(
        "program-bound-local-rl-other-run",
        ticket.local_rl_manifest.created_at,
        seed=18,
    )
    other_package = _local_package(
        bound_context["root"] / "other-local-package",
        other_manifest,
        registration_actor=ticket.authorized_by,
    )

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="differs from the authorized manifest",
    ):
        bound_context["manager"].build(
            package_id="program-bound-local-rl-package:wrong-manifest",
            created_at=other_package.created_at + timedelta(seconds=2),
            framework_version=__version__,
            source_repository=SOURCE_REPOSITORY,
            source_commit=SOURCE_COMMIT,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            ticket=ticket,
            local_rl_package=other_package,
            receipt_id="program-local-rl-receipt:wrong-manifest",
            completed_by=COMPLETION_ATTESTOR,
            completed_at=other_package.created_at + timedelta(seconds=1),
        )


def test_binding_rejects_program_approver_as_local_trainer(bound_context):
    ticket = bound_context["ticket"]
    trainer = ticket.approvals[0].actor_id
    overlapping_package = _local_package(
        bound_context["root"] / "overlapping-local-package",
        ticket.local_rl_manifest,
        registration_actor=ticket.authorized_by,
        trainer=trainer,
        evaluator_id="overlap-independent-evaluator",
        selector="overlap-independent-selector",
    )

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="not independent",
    ):
        bound_context["manager"].build(
            package_id="program-bound-local-rl-package:role-overlap",
            created_at=overlapping_package.created_at + timedelta(seconds=2),
            framework_version=__version__,
            source_repository=SOURCE_REPOSITORY,
            source_commit=SOURCE_COMMIT,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            ticket=ticket,
            local_rl_package=overlapping_package,
            receipt_id="program-local-rl-receipt:role-overlap",
            completed_by=COMPLETION_ATTESTOR,
            completed_at=overlapping_package.created_at + timedelta(seconds=1),
        )
