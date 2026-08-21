from evoagent.diagnosis.counterfactual_engine import CounterfactualAttributionEngine
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import FailureLayer
from evoagent.training import (
    DatasetSignals,
    DryRunMLInternBackend,
    MetricTarget,
    MLInternCLIAdapter,
    ModelEvolutionOrchestrator,
    ModelTicketFactory,
    TrainingBudget,
    TrainingMethod,
)

report = CounterfactualAttributionEngine().diagnose(
    SyntheticCounterfactualRunner(
        SyntheticFaultScenario(scenario_id="demo:model", fault_layers={FailureLayer.MODEL})
    )
)
ticket = ModelTicketFactory().create(
    report,
    ticket_id="demo-ticket",
    base_model_id="public/model-v0",
    problem_cluster="multi-step constraint planning",
    evidence_trace_ids=("trace:demo:1",),
    target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
    dataset_signals=DatasetSignals(gold_trajectories=20),
    allowed_methods=(TrainingMethod.SFT,),
    budget=TrainingBudget(max_gpu_hours=2, max_training_tokens=50000, max_cost_usd=20),
    safety_constraints=("public or synthetic data only", "no automatic deployment"),
)
candidate = ModelEvolutionOrchestrator().run(
    ticket,
    DryRunMLInternBackend(
        MLInternCLIAdapter(execution_enabled=False),
        workspace="./.evoagent/model-runs/demo-ticket",
    ),
)

print("method:", candidate.method.value)
print("status:", candidate.status)
print("command prefix:", candidate.task_spec.command[:5])
print("trace sharing:", candidate.task_spec.runtime_config["share_traces"])
