import pytest

from evoagent.benchmarks import (
    BenchmarkManifest,
    EvaluationBatch,
    EvolutionEvaluationProtocol,
    EvolutionProtocolSpec,
    FrozenSnapshotEvaluator,
    ResourceBudget,
    ResourceUsage,
    SameStartComparator,
    SyntheticFrozenEvaluator,
)
from evoagent.domain.models import AgentSnapshot


def manifest():
    return BenchmarkManifest(
        dataset_ref="synthetic/public-benchmark",
        revision="v1",
        split="held-out",
        task_ids=("t1", "t2", "t3"),
    )


def protocol(initial_model="public/model-v0", evaluation_budget=None):
    return EvolutionProtocolSpec(
        protocol_id="same-start-v1",
        initial_model_id=initial_model,
        manifest=manifest(),
        evolution_budget=ResourceBudget(max_task_trials=100, max_tokens=10000),
        evaluation_budget=evaluation_budget
        or ResourceBudget(max_task_trials=3, max_tokens=1000),
    )


def snapshot(round_index, scores, *, model_id="public/model-v0", usage=None):
    return AgentSnapshot(
        snapshot_id=f"A{round_index}",
        round_index=round_index,
        model_id=model_id,
        metadata={
            "synthetic_task_scores": scores,
            "synthetic_usage": usage or {"task_trials": 3, "tokens": 100},
        },
    )


def test_longitudinal_curve_preserves_best_intermediate_checkpoint():
    snapshots = [
        snapshot(0, {"t1": 0, "t2": 0, "t3": 1}),
        snapshot(1, {"t1": 1, "t2": 1, "t3": 1}),
        snapshot(2, {"t1": 1, "t2": 0, "t3": 1}),
    ]
    engine = EvolutionEvaluationProtocol()
    run = engine.evaluate_run(
        system_name="ours",
        snapshots=snapshots,
        protocol=protocol(),
        evaluator=SyntheticFrozenEvaluator(),
    )
    summary = engine.summarize(run)

    assert summary.evolution_gain == pytest.approx(1 / 3)
    assert summary.best_score == 1.0
    assert summary.best_round == 1
    assert summary.final_round == 2


def test_budget_overflow_is_rejected():
    with pytest.raises(ValueError):
        EvolutionEvaluationProtocol().evaluate_run(
            system_name="ours",
            snapshots=[snapshot(0, {"t1": 1, "t2": 1, "t3": 1}, usage={"task_trials": 4})],
            protocol=protocol(),
            evaluator=SyntheticFrozenEvaluator(),
        )


def test_wrong_task_set_is_rejected():
    class MissingTaskEvaluator(FrozenSnapshotEvaluator):
        def evaluate(self, snapshot, manifest, budget):
            return EvaluationBatch(
                per_task={"t1": 1.0},
                usage=ResourceUsage(task_trials=1),
            )

    with pytest.raises(ValueError):
        EvolutionEvaluationProtocol().evaluate_run(
            system_name="ours",
            snapshots=[snapshot(0, {"t1": 1, "t2": 1, "t3": 1})],
            protocol=protocol(),
            evaluator=MissingTaskEvaluator(),
        )


def test_snapshot_mutation_is_detected():
    class MutatingEvaluator(FrozenSnapshotEvaluator):
        def evaluate(self, snapshot, manifest, budget):
            snapshot.metadata["mutated"] = True
            return EvaluationBatch(
                per_task={task: 1.0 for task in manifest.task_ids},
                usage=ResourceUsage(task_trials=3),
            )

    with pytest.raises(RuntimeError):
        EvolutionEvaluationProtocol().evaluate_run(
            system_name="ours",
            snapshots=[snapshot(0, {"t1": 1, "t2": 1, "t3": 1})],
            protocol=protocol(),
            evaluator=MutatingEvaluator(),
        )


def test_same_start_comparison_ranks_and_rejects_mismatch():
    engine = EvolutionEvaluationProtocol()
    ours = engine.evaluate_run(
        system_name="ours",
        snapshots=[
            snapshot(0, {"t1": 0, "t2": 0, "t3": 1}),
            snapshot(1, {"t1": 1, "t2": 1, "t3": 1}),
        ],
        protocol=protocol(),
        evaluator=SyntheticFrozenEvaluator(),
    )
    baseline = engine.evaluate_run(
        system_name="baseline",
        snapshots=[
            snapshot(0, {"t1": 0, "t2": 0, "t3": 1}),
            snapshot(1, {"t1": 1, "t2": 0, "t3": 1}),
        ],
        protocol=protocol(),
        evaluator=SyntheticFrozenEvaluator(),
    )
    comparison = SameStartComparator().compare([baseline, ours])
    assert comparison.rankings[0].system_name == "ours"

    incompatible = ours.model_copy(
        update={"protocol": protocol(initial_model="public/other-model")}
    )
    with pytest.raises(ValueError):
        SameStartComparator().compare([ours, incompatible])
