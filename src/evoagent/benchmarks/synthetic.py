from evoagent.benchmarks.models import BenchmarkManifest, EvaluationBatch, ResourceBudget, ResourceUsage
from evoagent.benchmarks.protocol import FrozenSnapshotEvaluator
from evoagent.domain.models import AgentSnapshot


class SyntheticFrozenEvaluator(FrozenSnapshotEvaluator):
    """Deterministic evaluator for protocol tests, not a public benchmark."""

    def evaluate(
        self,
        snapshot: AgentSnapshot,
        manifest: BenchmarkManifest,
        budget: ResourceBudget,
    ) -> EvaluationBatch:
        scores = snapshot.metadata.get("synthetic_task_scores", {})
        usage = snapshot.metadata.get("synthetic_usage", {})
        return EvaluationBatch(
            per_task={task_id: float(scores[task_id]) for task_id in manifest.task_ids},
            usage=ResourceUsage(
                task_trials=usage.get(
                    "task_trials", len(manifest.task_ids) * manifest.trials_per_task
                ),
                tokens=usage.get("tokens", 0),
                tool_calls=usage.get("tool_calls", 0),
                wall_seconds=usage.get("wall_seconds", 0.0),
                cost_usd=usage.get("cost_usd", 0.0),
            ),
        )
