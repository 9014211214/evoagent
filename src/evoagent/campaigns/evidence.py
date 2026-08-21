from __future__ import annotations

from evoagent.campaigns.repository import SQLiteCampaignRepository
from evoagent.cycles.models import EvolutionCyclePolicy, ModelEvidenceCluster
from evoagent.domain.models import ExecutionTrace
from evoagent.traces.models import TraceTrustLevel


class PersistentModelEvidenceAccumulator:
    """Drop-in persistent replacement for the in-memory model evidence accumulator."""

    def __init__(self, repository: SQLiteCampaignRepository):
        self.repository = repository

    def add(
        self,
        trace: ExecutionTrace,
        *,
        problem_cluster: str,
        trust_level: TraceTrustLevel,
        policy: EvolutionCyclePolicy,
    ) -> ModelEvidenceCluster:
        snapshot = self.repository.add_model_evidence(
            base_model_id=trace.model_id,
            problem_cluster=problem_cluster,
            trace_id=trace.trace_id,
            task_id=trace.task.task_id,
            trust_level=trust_level.value,
            minimum_traces=policy.model_min_traces,
            minimum_distinct_tasks=policy.model_min_distinct_tasks,
        )
        return ModelEvidenceCluster(
            cluster_id=f"{snapshot.base_model_id}:{snapshot.problem_cluster}",
            base_model_id=snapshot.base_model_id,
            problem_cluster=snapshot.problem_cluster,
            trace_ids=snapshot.trace_ids,
            task_ids=snapshot.task_ids,
            ready=snapshot.ready,
        )

    def get(
        self,
        *,
        base_model_id: str,
        problem_cluster: str,
        policy: EvolutionCyclePolicy,
    ) -> ModelEvidenceCluster:
        snapshot = self.repository.get_model_evidence(
            base_model_id=base_model_id,
            problem_cluster=problem_cluster,
            minimum_traces=policy.model_min_traces,
            minimum_distinct_tasks=policy.model_min_distinct_tasks,
        )
        return ModelEvidenceCluster(
            cluster_id=f"{snapshot.base_model_id}:{snapshot.problem_cluster}",
            base_model_id=snapshot.base_model_id,
            problem_cluster=snapshot.problem_cluster,
            trace_ids=snapshot.trace_ids,
            task_ids=snapshot.task_ids,
            ready=snapshot.ready,
        )
