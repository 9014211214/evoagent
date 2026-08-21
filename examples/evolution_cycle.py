from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.cycles import (
    EvolutionCyclePolicy,
    EvolutionCycleRequest,
    EvolutionCycleService,
    ModelEvolutionSettings,
    StructuredVerifierSkillBackend,
)
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import ExecutionTrace, FailureLayer, Task
from evoagent.skills import SkillRegistry, SkillSpec
from evoagent.traces import JsonlTraceStore, TraceTrustLevel
from evoagent.training import (
    DatasetSignals,
    DryRunMLInternBackend,
    MetricTarget,
    MLInternCLIAdapter,
    TrainingBudget,
    TrainingMethod,
)


def failed_trace(trace_id, task_id, *, skill_id=None, skill_version=None, feedback=""):
    return ExecutionTrace(
        trace_id=trace_id,
        task=Task(task_id=task_id, task_type="synthetic-decision", input={}),
        model_id="public/model-v0",
        skill_id=skill_id,
        skill_version=skill_version,
        observable_events=[{"event": "synthetic_execution"}],
        final_output={"status": "failure"},
        verifier_passed=False,
        verifier_feedback=feedback,
        cost={"llm_tokens": 10},
    )


with TemporaryDirectory() as directory:
    registry = SkillRegistry()
    registry.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
            rules=("accept_safe",),
        )
    )
    service = EvolutionCycleService(
        trace_store=JsonlTraceStore(Path(directory) / "traces.jsonl"),
        skill_registry=registry,
        skill_backend=StructuredVerifierSkillBackend(),
        policy=EvolutionCyclePolicy(model_min_traces=3, model_min_distinct_tasks=3),
    )

    skill_result = service.process(
        EvolutionCycleRequest(
            trace=failed_trace(
                "trace:skill:1",
                "task:skill:1",
                skill_id="decision_skill",
                skill_version="1.0.0",
                feedback="missing_skill_rule: reject_unsafe",
            ),
            source="synthetic-demo",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=SyntheticCounterfactualRunner(
            SyntheticFaultScenario(scenario_id="skill-fault", fault_layers={FailureLayer.SKILL})
        ),
    )
    print("skill cycle:", skill_result.status.value, skill_result.skill_candidate.version)
    print("active remains:", registry.active("decision_skill").spec.version)

    settings = ModelEvolutionSettings(
        problem_cluster="multi-step-planning",
        target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
        dataset_signals=DatasetSignals(gold_trajectories=3),
        allowed_methods=(TrainingMethod.SFT,),
        budget=TrainingBudget(max_gpu_hours=2, max_training_tokens=50000),
    )
    backend = DryRunMLInternBackend(
        MLInternCLIAdapter(execution_enabled=False),
        workspace=str(Path(directory) / "model-run"),
    )
    for index in range(1, 4):
        model_result = service.process(
            EvolutionCycleRequest(
                trace=failed_trace(f"trace:model:{index}", f"task:model:{index}"),
                source="synthetic-demo",
                trust_level=TraceTrustLevel.VERIFIED,
                model_settings=settings,
            ),
            counterfactual_runner=SyntheticCounterfactualRunner(
                SyntheticFaultScenario(scenario_id=f"model-fault:{index}", fault_layers={FailureLayer.MODEL})
            ),
            model_backend=backend,
        )
        print("model cycle", index, ":", model_result.status.value)
