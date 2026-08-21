from __future__ import annotations

from abc import ABC, abstractmethod

from evoagent.training.ml_intern import MLInternCLIAdapter
from evoagent.training.models import ModelCandidate, ModelImprovementTicket, TrainingPlan
from evoagent.training.strategy import TrainingStrategySelector


class ModelEvolutionBackend(ABC):
    @abstractmethod
    def train(self, ticket: ModelImprovementTicket, plan: TrainingPlan) -> ModelCandidate:
        raise NotImplementedError


class DryRunMLInternBackend(ModelEvolutionBackend):
    def __init__(self, adapter: MLInternCLIAdapter, *, workspace: str):
        self.adapter = adapter
        self.workspace = workspace

    def train(self, ticket: ModelImprovementTicket, plan: TrainingPlan) -> ModelCandidate:
        task_spec = self.adapter.build_task(ticket, plan, workspace=self.workspace)
        return ModelCandidate(
            candidate_id=f"candidate:{ticket.ticket_id}",
            base_model_id=ticket.base_model_id,
            method=plan.method,
            artifact_uri=f"ml-intern-task://{ticket.ticket_id}",
            plan=plan,
            task_spec=task_spec,
            generated_by="ml-intern-cli-adapter:dry-run",
            evidence_manifest_hash=ticket.evidence_manifest_hash,
            held_out_task_ids=ticket.held_out_task_ids,
        )


class ModelEvolutionOrchestrator:
    def __init__(self, selector: TrainingStrategySelector | None = None):
        self.selector = selector or TrainingStrategySelector()

    def run(
        self,
        ticket: ModelImprovementTicket,
        backend: ModelEvolutionBackend,
    ) -> ModelCandidate:
        plan = self.selector.select(ticket)
        candidate = backend.train(ticket, plan)
        if candidate.status != "candidate":
            raise ValueError("Training backend must return a non-deployed candidate artifact.")
        if candidate.training_executed:
            raise ValueError("Dry-run model candidate must not claim training execution.")
        if candidate.evidence_manifest_hash != ticket.evidence_manifest_hash:
            raise ValueError("Model candidate evidence manifest does not match its Ticket.")
        if candidate.held_out_task_ids != ticket.held_out_task_ids:
            raise ValueError("Model candidate held-out manifest does not match its Ticket.")
        return candidate
