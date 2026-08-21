from evoagent.domain.models import AgentSnapshot, EvaluationResult, Task
from evoagent.runtime.base import AgentRuntime

class FrozenEvaluator:
    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    def evaluate(self, snapshot: AgentSnapshot, tasks: list[Task]) -> EvaluationResult:
        outcomes = {}
        for task in tasks:
            outcomes[task.task_id] = self.runtime.run(task, snapshot).verifier_passed
        passed = sum(outcomes.values())
        total = len(tasks)
        return EvaluationResult(
            snapshot_id=snapshot.snapshot_id, total=total, passed=passed,
            score=(passed/total) if total else 0.0,
            failed_task_ids=[k for k,v in outcomes.items() if not v],
            per_task=outcomes,
        )

class PromotionGate:
    def should_promote(self, base: EvaluationResult, candidate: EvaluationResult, max_regressions: int=0) -> bool:
        regressions = sum(1 for task_id, base_ok in base.per_task.items()
                          if base_ok and not candidate.per_task.get(task_id, False))
        return candidate.score > base.score and regressions <= max_regressions
