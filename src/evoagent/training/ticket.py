from __future__ import annotations

from evoagent.diagnosis.counterfactual import AttributionReport, ExperimentType
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.training.models import (
    DatasetSignals,
    MetricTarget,
    ModelImprovementTicket,
    TrainingBudget,
    TrainingMethod,
)


_EXTERNAL_EXPERIMENTS = {
    ExperimentType.REPLACE_SKILL: FailureLayer.SKILL,
    ExperimentType.FORCE_ROUTER: FailureLayer.ROUTER,
    ExperimentType.REPLAY_TOOL: FailureLayer.TOOL,
    ExperimentType.COMPLETE_CONTEXT: FailureLayer.CONTEXT,
    ExperimentType.ORACLE_VERIFIER: FailureLayer.VERIFIER,
    ExperimentType.RESET_ENVIRONMENT: FailureLayer.ENVIRONMENT,
}


class ModelTicketFactory:
    def create(
        self,
        report: AttributionReport,
        *,
        ticket_id: str,
        base_model_id: str,
        problem_cluster: str,
        evidence_trace_ids: tuple[str, ...],
        target_metrics: tuple[MetricTarget, ...],
        dataset_signals: DatasetSignals,
        allowed_methods: tuple[TrainingMethod, ...],
        budget: TrainingBudget,
        replay_environment: str | None = None,
        safety_constraints: tuple[str, ...] = (),
        regression_suite: str = "default",
        evidence_dataset_uri: str | None = None,
        evidence_manifest_hash: str | None = None,
        held_out_task_ids: tuple[str, ...] = (),
    ) -> ModelImprovementTicket:
        if (
            report.root_cause_layer != FailureLayer.MODEL
            or report.recommended_action != EvolutionAction.TRAIN_MODEL
            or not report.actionable
        ):
            raise ValueError("Only actionable, verified model attribution can create a model ticket.")

        ruled_out: list[FailureLayer] = []
        for experiment_type, layer in _EXTERNAL_EXPERIMENTS.items():
            results = [item for item in report.experiments if item.experiment_type == experiment_type]
            if not results or any(item.supports_hypothesis for item in results):
                raise ValueError(f"External layer was not conclusively ruled out: {layer.value}")
            ruled_out.append(layer)

        if not evidence_trace_ids:
            raise ValueError("A model ticket requires at least one evidence trace.")
        if len(set(evidence_trace_ids)) != len(evidence_trace_ids):
            raise ValueError("Model ticket evidence Trace IDs must be unique.")
        if not target_metrics:
            raise ValueError("A model ticket requires target metrics.")
        if not allowed_methods:
            raise ValueError("A model ticket requires at least one allowed training method.")
        if (evidence_dataset_uri is None) != (evidence_manifest_hash is None):
            raise ValueError(
                "Model ticket evidence dataset URI and manifest hash must be supplied together."
            )

        return ModelImprovementTicket(
            ticket_id=ticket_id,
            base_model_id=base_model_id,
            problem_cluster=problem_cluster,
            evidence_trace_ids=evidence_trace_ids,
            ruled_out_layers=tuple(ruled_out),
            target_metrics=target_metrics,
            dataset_signals=dataset_signals,
            allowed_methods=allowed_methods,
            budget=budget,
            replay_environment=replay_environment,
            safety_constraints=safety_constraints,
            regression_suite=regression_suite,
            evidence_dataset_uri=evidence_dataset_uri,
            evidence_manifest_hash=evidence_manifest_hash,
            held_out_task_ids=held_out_task_ids,
        )
