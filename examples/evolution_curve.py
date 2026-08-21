from evoagent.benchmarks import (
    BenchmarkManifest,
    EvolutionEvaluationProtocol,
    EvolutionProtocolSpec,
    ResourceBudget,
    SyntheticFrozenEvaluator,
)
from evoagent.domain.models import AgentSnapshot

manifest = BenchmarkManifest(
    dataset_ref="synthetic/public-benchmark",
    revision="v1",
    split="held-out",
    task_ids=("t1", "t2", "t3"),
)
protocol = EvolutionProtocolSpec(
    protocol_id="demo-same-start",
    initial_model_id="public/model-v0",
    manifest=manifest,
    evolution_budget=ResourceBudget(max_task_trials=100, max_tokens=10000),
    evaluation_budget=ResourceBudget(max_task_trials=3, max_tokens=1000),
)
snapshots = [
    AgentSnapshot(
        snapshot_id="A0",
        round_index=0,
        model_id="public/model-v0",
        metadata={"synthetic_task_scores": {"t1": 0, "t2": 0, "t3": 1}},
    ),
    AgentSnapshot(
        snapshot_id="A1",
        round_index=1,
        model_id="public/model-v1",
        parent_snapshot_id="A0",
        metadata={"synthetic_task_scores": {"t1": 1, "t2": 1, "t3": 1}},
    ),
    AgentSnapshot(
        snapshot_id="A2",
        round_index=2,
        model_id="public/model-v2",
        parent_snapshot_id="A1",
        metadata={"synthetic_task_scores": {"t1": 1, "t2": 0, "t3": 1}},
    ),
]
engine = EvolutionEvaluationProtocol()
run = engine.evaluate_run(
    system_name="demo-evolving-agent",
    snapshots=snapshots,
    protocol=protocol,
    evaluator=SyntheticFrozenEvaluator(),
)
summary = engine.summarize(run)
print("scores:", [round(item.score, 3) for item in run.evaluations])
print("evolution gain:", round(summary.evolution_gain, 3))
print("best round:", summary.best_round)
