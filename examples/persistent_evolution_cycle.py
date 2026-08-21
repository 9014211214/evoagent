from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.campaigns import (
    CampaignGovernanceService,
    PersistentModelEvidenceAccumulator,
    SQLiteCampaignRepository,
)
from evoagent.campaigns.cycle import GovernedEvolutionCycleService
from evoagent.cycles import (
    EvolutionCyclePolicy,
    EvolutionCycleRequest,
    ModelEvolutionSettings,
    StructuredVerifierSkillBackend,
)
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import ExecutionTrace, FailureLayer, Task
from evoagent.skills import SkillRegistry
from evoagent.traces import JsonlTraceStore, TraceTrustLevel
from evoagent.training import (
    DatasetSignals,
    DryRunMLInternBackend,
    MetricTarget,
    MLInternCLIAdapter,
    TrainingBudget,
    TrainingMethod,
)


def failed_trace(index: int) -> ExecutionTrace:
    return ExecutionTrace(
        trace_id=f"trace:model:{index}",
        task=Task(
            task_id=f"task:model:{index}",
            task_type="synthetic-decision",
            input={},
        ),
        model_id="public/model-v0",
        observable_events=[{"event": "synthetic_execution"}],
        final_output={"status": "failure"},
        verifier_passed=False,
        verifier_feedback="multi-step planning failed",
        cost={"llm_tokens": 10},
    )


with TemporaryDirectory() as directory:
    root = Path(directory)
    database = root / "campaigns.db"
    trace_file = root / "traces.jsonl"
    settings = ModelEvolutionSettings(
        problem_cluster="multi-step-planning",
        target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
        dataset_signals=DatasetSignals(gold_trajectories=4),
        allowed_methods=(TrainingMethod.SFT,),
        budget=TrainingBudget(max_gpu_hours=2, max_training_tokens=50000),
    )
    backend = DryRunMLInternBackend(
        MLInternCLIAdapter(execution_enabled=False),
        workspace=str(root / "model-run"),
    )

    for index in range(1, 5):
        # Recreate the repository and service every round to demonstrate persistence.
        repository = SQLiteCampaignRepository(database)
        service = GovernedEvolutionCycleService(
            trace_store=JsonlTraceStore(trace_file),
            skill_registry=SkillRegistry(),
            skill_backend=StructuredVerifierSkillBackend(),
            policy=EvolutionCyclePolicy(model_min_traces=3, model_min_distinct_tasks=3),
            evidence_accumulator=PersistentModelEvidenceAccumulator(repository),
            campaign_governance=CampaignGovernanceService(repository),
        )
        result = service.process(
            EvolutionCycleRequest(
                trace=failed_trace(index),
                source="synthetic-demo",
                trust_level=TraceTrustLevel.VERIFIED,
                model_settings=settings,
            ),
            counterfactual_runner=SyntheticCounterfactualRunner(
                SyntheticFaultScenario(
                    scenario_id=f"model-fault:{index}",
                    fault_layers={FailureLayer.MODEL},
                )
            ),
            model_backend=backend,
        )
        print(
            f"round={index} status={result.status.value} "
            f"campaign={result.campaign_id} reused={result.reused}"
        )
