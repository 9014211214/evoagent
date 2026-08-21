from __future__ import annotations

from collections import defaultdict

from evoagent.cycles.models import EvolutionCyclePolicy, ModelEvidenceCluster
from evoagent.domain.models import ExecutionTrace
from evoagent.traces.models import TraceTrustLevel


class ModelEvidenceAccumulator:
    def __init__(self):
        self._trace_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._task_ids: dict[tuple[str, str], list[str]] = defaultdict(list)

    def add(
        self,
        trace: ExecutionTrace,
        *,
        problem_cluster: str,
        trust_level: TraceTrustLevel,
        policy: EvolutionCyclePolicy,
    ) -> ModelEvidenceCluster:
        if trust_level == TraceTrustLevel.UNTRUSTED:
            raise ValueError("Untrusted traces cannot contribute model-training evidence.")
        key = (trace.model_id, problem_cluster)
        if trace.trace_id not in self._trace_ids[key]:
            self._trace_ids[key].append(trace.trace_id)
        if trace.task.task_id not in self._task_ids[key]:
            self._task_ids[key].append(trace.task.task_id)
        return self.get(
            base_model_id=trace.model_id,
            problem_cluster=problem_cluster,
            policy=policy,
        )

    def get(
        self,
        *,
        base_model_id: str,
        problem_cluster: str,
        policy: EvolutionCyclePolicy,
    ) -> ModelEvidenceCluster:
        key = (base_model_id, problem_cluster)
        trace_ids = tuple(self._trace_ids[key])
        task_ids = tuple(self._task_ids[key])
        ready = (
            len(trace_ids) >= policy.model_min_traces
            and len(task_ids) >= policy.model_min_distinct_tasks
        )
        return ModelEvidenceCluster(
            cluster_id=f"{base_model_id}:{problem_cluster}",
            base_model_id=base_model_id,
            problem_cluster=problem_cluster,
            trace_ids=trace_ids,
            task_ids=task_ids,
            ready=ready,
        )
