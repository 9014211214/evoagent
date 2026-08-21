from __future__ import annotations

from evoagent.training.models import (
    AgenticRLEnvironmentSpec,
    AgenticRLTaskSpec,
    ModelCandidate,
    ModelImprovementTicket,
    RewardSpec,
    RLAlgorithm,
    TrainingMethod,
    TrainingPlan,
)
from evoagent.training.orchestrator import ModelEvolutionBackend


class AgenticRLPlanner:
    """Build a backend-neutral, non-executing Agentic RL task specification."""

    def build_task(
        self,
        ticket: ModelImprovementTicket,
        plan: TrainingPlan,
        *,
        environment: AgenticRLEnvironmentSpec,
        reward: RewardSpec,
        algorithm: RLAlgorithm,
        workspace: str,
        execution_enabled: bool = False,
    ) -> AgenticRLTaskSpec:
        if plan.method != TrainingMethod.AGENTIC_RL:
            raise ValueError("Agentic RL planner requires an Agentic RL training plan.")
        if ticket.budget.max_rollouts <= 0:
            raise ValueError("Agentic RL requires a positive rollout budget.")
        if not all(
            (
                environment.replayable,
                environment.resettable,
                environment.machine_verifier,
                environment.isolated,
                environment.side_effect_free,
            )
        ):
            raise ValueError(
                "Agentic RL environment must be replayable, resettable, machine-verifiable, "
                "isolated and side-effect-free."
            )
        if ticket.replay_environment and ticket.replay_environment != environment.environment_id:
            raise ValueError("Ticket replay environment does not match the RL environment.")
        if not reward.components:
            raise ValueError("Reward specification requires at least one component.")
        if not any(
            component.kind == "reward" and component.machine_computable
            for component in reward.components
        ):
            raise ValueError("Reward specification requires a positive machine-computable signal.")

        return AgenticRLTaskSpec(
            algorithm=algorithm,
            environment=environment,
            reward=reward,
            workspace=workspace,
            runtime_config={
                "sandbox": True,
                "publish_artifacts": False,
                "deploy_candidate": False,
                "stop_on_budget_exhaustion": True,
                "training_executed": False,
                "evidence_manifest_hash": ticket.evidence_manifest_hash,
                "held_out_task_ids": list(ticket.held_out_task_ids),
            },
            rollout_budget=ticket.budget.max_rollouts,
            evidence_dataset_uri=ticket.evidence_dataset_uri,
            evidence_manifest_hash=ticket.evidence_manifest_hash,
            held_out_task_ids=ticket.held_out_task_ids,
            execution_enabled=execution_enabled,
        )


class DryRunAgenticRLBackend(ModelEvolutionBackend):
    def __init__(
        self,
        planner: AgenticRLPlanner,
        *,
        environment: AgenticRLEnvironmentSpec,
        reward: RewardSpec,
        algorithm: RLAlgorithm,
        workspace: str,
    ):
        self.planner = planner
        self.environment = environment
        self.reward = reward
        self.algorithm = algorithm
        self.workspace = workspace

    def train(self, ticket: ModelImprovementTicket, plan: TrainingPlan) -> ModelCandidate:
        task_spec = self.planner.build_task(
            ticket,
            plan,
            environment=self.environment,
            reward=self.reward,
            algorithm=self.algorithm,
            workspace=self.workspace,
            execution_enabled=False,
        )
        return ModelCandidate(
            candidate_id=f"candidate:{ticket.ticket_id}:{self.algorithm.value}",
            base_model_id=ticket.base_model_id,
            method=TrainingMethod.AGENTIC_RL,
            artifact_uri=f"agentic-rl-task://{ticket.ticket_id}",
            plan=plan,
            task_spec=task_spec,
            generated_by="agentic-rl-backend:dry-run",
            evidence_manifest_hash=ticket.evidence_manifest_hash,
            held_out_task_ids=ticket.held_out_task_ids,
        )
