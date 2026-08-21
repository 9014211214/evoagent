from __future__ import annotations

from collections import defaultdict

from evoagent.diagnosis.counterfactual import (
    AttributionReport,
    CounterfactualExperiment,
    CounterfactualRunner,
    ExperimentResult,
    ExperimentType,
    FailureHypothesis,
    LayerScore,
)
from evoagent.domain.models import EvolutionAction, FailureLayer


_EXPERIMENT_BY_LAYER = {
    FailureLayer.SKILL: ExperimentType.REPLACE_SKILL,
    FailureLayer.ROUTER: ExperimentType.FORCE_ROUTER,
    FailureLayer.TOOL: ExperimentType.REPLAY_TOOL,
    FailureLayer.CONTEXT: ExperimentType.COMPLETE_CONTEXT,
    FailureLayer.VERIFIER: ExperimentType.ORACLE_VERIFIER,
    FailureLayer.ENVIRONMENT: ExperimentType.RESET_ENVIRONMENT,
    FailureLayer.MODEL: ExperimentType.REFERENCE_MODEL,
}

_ACTION_BY_LAYER = {
    FailureLayer.SKILL: EvolutionAction.UPDATE_SKILL,
    FailureLayer.ROUTER: EvolutionAction.UPDATE_ROUTER,
    FailureLayer.TOOL: EvolutionAction.REPAIR_TOOL,
    FailureLayer.CONTEXT: EvolutionAction.UPDATE_CONTEXT,
    FailureLayer.VERIFIER: EvolutionAction.REPAIR_VERIFIER,
    FailureLayer.MODEL: EvolutionAction.TRAIN_MODEL,
    FailureLayer.SAFETY: EvolutionAction.QUARANTINE,
    FailureLayer.ENVIRONMENT: EvolutionAction.ESCALATE,
}


class CounterfactualAttributionEngine:
    """Evidence-gated failure attribution.

    A counterfactual is causal evidence only when the original execution failed and
    a single controlled intervention makes it pass. Model attribution is gated:
    every cheaper external layer must be tested and ruled out first.
    """

    def __init__(self, *, min_confidence: float = 0.6, conflict_margin: float = 0.05):
        self.min_confidence = min_confidence
        self.conflict_margin = conflict_margin

    @staticmethod
    def default_hypotheses() -> list[FailureHypothesis]:
        descriptions = {
            FailureLayer.SKILL: "The selected skill is incomplete or incorrect.",
            FailureLayer.ROUTER: "The correct skill exists but routing selected the wrong one.",
            FailureLayer.TOOL: "The tool contract or adapter caused the failure.",
            FailureLayer.CONTEXT: "Required task context was missing or malformed.",
            FailureLayer.VERIFIER: "The verifier incorrectly judged the execution.",
            FailureLayer.ENVIRONMENT: "The execution environment was unhealthy or inconsistent.",
            FailureLayer.MODEL: "The current model lacks the required capability.",
        }
        return [
            FailureHypothesis(
                hypothesis_id=f"hyp:{layer.value}",
                layer=layer,
                description=descriptions[layer],
                experiment_type=experiment_type,
            )
            for layer, experiment_type in _EXPERIMENT_BY_LAYER.items()
        ]

    @staticmethod
    def plan(hypotheses: list[FailureHypothesis]) -> list[CounterfactualExperiment]:
        return [
            CounterfactualExperiment(
                experiment_id=f"exp:{hypothesis.layer.value}",
                hypothesis_id=hypothesis.hypothesis_id,
                experiment_type=hypothesis.experiment_type,
                intervention={"replace_layer": hypothesis.layer.value},
                controlled_variables=[
                    item.value
                    for item in FailureLayer
                    if item
                    not in {
                        hypothesis.layer,
                        FailureLayer.NONE,
                        FailureLayer.UNKNOWN,
                        FailureLayer.SAFETY,
                    }
                ],
            )
            for hypothesis in hypotheses
        ]

    def diagnose(
        self,
        runner: CounterfactualRunner,
        hypotheses: list[FailureHypothesis] | None = None,
    ) -> AttributionReport:
        hypotheses = hypotheses or self.default_hypotheses()
        results = [runner.run(experiment) for experiment in self.plan(hypotheses)]
        return self.attribute(results, hypotheses)

    def attribute(
        self,
        experiments: list[ExperimentResult],
        hypotheses: list[FailureHypothesis],
    ) -> AttributionReport:
        hypotheses_by_id = {hypothesis.hypothesis_id: hypothesis for hypothesis in hypotheses}
        scores: dict[FailureLayer, float] = defaultdict(float)
        supporting_ids: dict[FailureLayer, list[str]] = defaultdict(list)
        evidence_by_layer: dict[FailureLayer, list[str]] = defaultdict(list)

        for result in experiments:
            hypothesis = hypotheses_by_id.get(result.hypothesis_id)
            if hypothesis is None or hypothesis.experiment_type != result.experiment_type:
                continue
            causal_support = (
                result.supports_hypothesis
                and not result.baseline_success
                and result.counterfactual_success
            )
            if not causal_support:
                continue
            score = hypothesis.prior_confidence * result.confidence
            scores[hypothesis.layer] = max(scores[hypothesis.layer], score)
            supporting_ids[hypothesis.layer].append(result.experiment_id)
            evidence_by_layer[hypothesis.layer].extend(result.evidence)

        ranked = sorted(
            [
                LayerScore(
                    layer=layer,
                    score=score,
                    supporting_experiment_ids=supporting_ids[layer],
                )
                for layer, score in scores.items()
            ],
            key=lambda item: item.score,
            reverse=True,
        )

        if not ranked or ranked[0].score < self.min_confidence:
            return self._unknown(
                experiments,
                ranked,
                "No counterfactual produced sufficient causal evidence.",
            )

        if len(ranked) > 1 and ranked[0].score - ranked[1].score <= self.conflict_margin:
            return self._unknown(
                experiments,
                ranked,
                "Conflicting counterfactuals support multiple root causes.",
            )

        root = ranked[0].layer
        if root == FailureLayer.MODEL and not self._model_gate_passed(experiments, hypotheses):
            return self._unknown(
                experiments,
                ranked,
                "Model evidence exists, but cheaper external layers were not all tested and ruled out.",
            )

        action = _ACTION_BY_LAYER.get(root, EvolutionAction.ESCALATE)
        actionable = action not in {EvolutionAction.ESCALATE, EvolutionAction.NO_ACTION}
        return AttributionReport(
            root_cause_layer=root,
            confidence=ranked[0].score,
            ranked_causes=ranked,
            evidence=evidence_by_layer[root],
            experiments=experiments,
            recommended_action=action,
            actionable=actionable,
            reason=(
                f"A controlled {_EXPERIMENT_BY_LAYER[root].value} intervention "
                "changed failure to success."
            ),
        )

    @staticmethod
    def _model_gate_passed(
        experiments: list[ExperimentResult],
        hypotheses: list[FailureHypothesis],
    ) -> bool:
        hypothesis_by_layer = {hypothesis.layer: hypothesis for hypothesis in hypotheses}
        result_by_hypothesis: dict[str, list[ExperimentResult]] = defaultdict(list)
        for result in experiments:
            result_by_hypothesis[result.hypothesis_id].append(result)

        required_layers = {
            FailureLayer.SKILL,
            FailureLayer.ROUTER,
            FailureLayer.TOOL,
            FailureLayer.CONTEXT,
            FailureLayer.VERIFIER,
            FailureLayer.ENVIRONMENT,
        }
        for layer in required_layers:
            hypothesis = hypothesis_by_layer.get(layer)
            if hypothesis is None:
                return False
            layer_results = result_by_hypothesis.get(hypothesis.hypothesis_id, [])
            if not layer_results:
                return False
            if any(result.supports_hypothesis for result in layer_results):
                return False
        return True

    @staticmethod
    def _unknown(
        experiments: list[ExperimentResult],
        ranked: list[LayerScore],
        reason: str,
    ) -> AttributionReport:
        return AttributionReport(
            root_cause_layer=FailureLayer.UNKNOWN,
            confidence=0.0,
            ranked_causes=ranked,
            evidence=[reason],
            experiments=experiments,
            recommended_action=EvolutionAction.ESCALATE,
            actionable=False,
            reason=reason,
        )
