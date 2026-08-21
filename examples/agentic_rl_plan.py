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

report = CounterfactualAttributionEngine().diagnose(
    SyntheticCounterfactualRunner(
        SyntheticFaultScenario(scenario_id="demo:rl", fault_layers={FailureLayer.MODEL})
    )
)
ticket = ModelTicketFactory().create(
    report,
    ticket_id="demo-rl-ticket",
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
backend = DryRunAgenticRLBackend(
    AgenticRLPlanner(),
    environment=AgenticRLEnvironmentSpec(
        environment_id="synthetic-rl-env:v1",
        replayable=True,
        resettable=True,
        machine_verifier=True,
        isolated=True,
        side_effect_free=True,
        max_episode_steps=30,
    ),
    reward=RewardSpec(
        components=(
            RewardComponent(name="task_success", weight=1.0, kind="reward"),
            RewardComponent(name="invalid_tool_call", weight=-0.2, kind="penalty"),
        )
    ),
    algorithm=RLAlgorithm.GRPO,
    workspace="./.evoagent/rl-runs/demo",
)
candidate = ModelEvolutionOrchestrator().run(ticket, backend)
print("method:", candidate.method.value)
print("algorithm:", candidate.task_spec.algorithm.value)
print("rollout budget:", candidate.task_spec.rollout_budget)
print("deploy candidate:", candidate.task_spec.runtime_config["deploy_candidate"])
