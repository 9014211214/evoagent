import pytest

from evoagent.diagnosis.counterfactual_engine import CounterfactualAttributionEngine
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import FailureLayer
from evoagent.training import (
    AgenticRLEnvironmentSpec,
    AgenticRLPlanner,
    DatasetSignals,
    DryRunAgenticRLBackend,
    MetricTarget,
    ModelEvolutionOrchestrator,
    ModelTicketFactory,
    RewardComponent,
    RewardSpec,
    RLAlgorithm,
    TrainingBudget,
    TrainingMethod,
)


def ticket():
    report = CounterfactualAttributionEngine().diagnose(
        SyntheticCounterfactualRunner(
            SyntheticFaultScenario(scenario_id="rl:model", fault_layers={FailureLayer.MODEL})
        )
    )
    return ModelTicketFactory().create(
        report,
        ticket_id="rl-ticket",
        base_model_id="public/model-v0",
        problem_cluster="long-horizon tool planning",
        evidence_trace_ids=("trace:rl:1",),
        target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
        dataset_signals=DatasetSignals(
            replayable_environment=True,
            resettable_environment=True,
            machine_verifier=True,
        ),
        allowed_methods=(TrainingMethod.AGENTIC_RL,),
        budget=TrainingBudget(max_gpu_hours=4, max_rollouts=64, max_cost_usd=50),
        replay_environment="synthetic-rl-env:v1",
    )


def environment(**overrides):
    values = dict(
        environment_id="synthetic-rl-env:v1",
        replayable=True,
        resettable=True,
        machine_verifier=True,
        isolated=True,
        side_effect_free=True,
        max_episode_steps=30,
    )
    values.update(overrides)
    return AgenticRLEnvironmentSpec(**values)


def reward():
    return RewardSpec(
        components=(
            RewardComponent(name="task_success", weight=1.0, kind="reward"),
            RewardComponent(name="invalid_tool_call", weight=-0.2, kind="penalty"),
        )
    )


def test_invalid_environment_cannot_generate_rl_task():
    backend = DryRunAgenticRLBackend(
        AgenticRLPlanner(),
        environment=environment(resettable=False),
        reward=reward(),
        algorithm=RLAlgorithm.GRPO,
        workspace="/tmp/rl",
    )
    with pytest.raises(ValueError):
        ModelEvolutionOrchestrator().run(ticket(), backend)


def test_agentic_rl_returns_non_deployed_candidate():
    backend = DryRunAgenticRLBackend(
        AgenticRLPlanner(),
        environment=environment(),
        reward=reward(),
        algorithm=RLAlgorithm.GRPO,
        workspace="/tmp/rl",
    )
    candidate = ModelEvolutionOrchestrator().run(ticket(), backend)

    assert candidate.status == "candidate"
    assert candidate.task_spec.execution_enabled is False
    assert candidate.task_spec.runtime_config["deploy_candidate"] is False
    assert candidate.task_spec.rollout_budget == 64
