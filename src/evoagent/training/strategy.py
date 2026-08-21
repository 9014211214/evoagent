from evoagent.training.models import ModelImprovementTicket, TrainingMethod, TrainingPlan


class NoTrainingStrategyError(ValueError):
    pass


class TrainingStrategySelector:
    """Choose the strongest supported method within the explicit ticket budget."""

    def select(self, ticket: ModelImprovementTicket) -> TrainingPlan:
        allowed = set(ticket.allowed_methods)
        signals = ticket.dataset_signals

        if (
            TrainingMethod.AGENTIC_RL in allowed
            and signals.replayable_environment
            and signals.resettable_environment
            and signals.machine_verifier
            and ticket.budget.max_rollouts > 0
            and ticket.replay_environment
        ):
            return TrainingPlan(
                method=TrainingMethod.AGENTIC_RL,
                rationale="Replayable, resettable, machine-verifiable environment is available.",
                dataset_plan=(
                    "cluster verified model failures",
                    "construct rollout seeds from evidence traces",
                    "exclude quarantined and low-trust traces",
                ),
                evaluation_plan=(
                    "frozen held-out task success",
                    "regression suite",
                    "reward-hacking checks",
                    "safety suite",
                ),
                budget=ticket.budget,
            )

        if TrainingMethod.DPO in allowed and signals.preference_pairs > 0:
            return TrainingPlan(
                method=TrainingMethod.DPO,
                rationale="Verified chosen/rejected trajectory pairs are available.",
                dataset_plan=(
                    "build chosen/rejected pairs",
                    "deduplicate by task and failure cluster",
                    "hold out evaluation pairs",
                ),
                evaluation_plan=("held-out preference accuracy", "task success", "regression suite"),
                budget=ticket.budget,
            )

        if TrainingMethod.SFT in allowed and signals.gold_trajectories > 0:
            return TrainingPlan(
                method=TrainingMethod.SFT,
                rationale="Verified gold trajectories are available.",
                dataset_plan=(
                    "build supervised tool-use trajectories",
                    "mask non-target tokens",
                    "deduplicate and split by task family",
                ),
                evaluation_plan=("held-out task success", "tool correctness", "regression suite"),
                budget=ticket.budget,
            )

        raise NoTrainingStrategyError(
            "No allowed training method is supported by the available evidence and budget."
        )
