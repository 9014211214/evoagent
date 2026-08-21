from __future__ import annotations

from evoagent.composite import (
    CompositeSnapshotEvaluation,
    CompositeTaskOutcome,
    CompositeTaskTrack,
)
from evoagent.diagnosis import (
    AttributionReport,
    ExperimentResult,
    ExperimentType,
    LayerScore,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.model_registry.models import canonical_sha256

from .models import (
    IntegratedCase,
    IntegratedRunPolicy,
    build_integrated_case,
)


class IntegratedCaseFactoryError(ValueError):
    pass


def _experiment(
    *,
    experiment_id: str,
    experiment_type: ExperimentType,
    layer: FailureLayer,
    supports: bool,
    trace_hash: str,
) -> ExperimentResult:
    return ExperimentResult(
        experiment_id=experiment_id,
        hypothesis_id=f"hypothesis:{layer.value}:{experiment_id}",
        experiment_type=experiment_type,
        baseline_success=False,
        counterfactual_success=supports,
        supports_hypothesis=supports,
        evidence=[
            (
                f"bounded supporting counterfactual for {experiment_id}"
                if supports
                else f"bounded negative control for {experiment_id}"
            )
        ],
        metadata={
            "hypothesis_layer": layer.value,
            "bounded": True,
            "source_trace_hash": trace_hash,
            "counterfactual_repaired_task": supports,
            "hidden_reasoning_persisted": False,
        },
        confidence=0.99 if supports else 0.01,
    )


def _report(outcome: CompositeTaskOutcome) -> AttributionReport:
    if outcome.track == CompositeTaskTrack.SKILL:
        layer = FailureLayer.SKILL
        action = EvolutionAction.UPDATE_SKILL
        supporting_type = ExperimentType.REPLACE_SKILL
        negative_type = ExperimentType.REFERENCE_MODEL
        negative_layer = FailureLayer.MODEL
    elif outcome.track == CompositeTaskTrack.LOCAL_POLICY:
        layer = FailureLayer.MODEL
        action = EvolutionAction.TRAIN_MODEL
        supporting_type = ExperimentType.REFERENCE_MODEL
        negative_type = ExperimentType.REPLACE_SKILL
        negative_layer = FailureLayer.SKILL
    else:  # pragma: no cover - enum exhaustiveness
        raise IntegratedCaseFactoryError(
            "Frozen Task belongs to another integrated track."
        )
    supporting = _experiment(
        experiment_id=(
            f"counterfactual:{supporting_type.value}:{outcome.task_id}"
        ),
        experiment_type=supporting_type,
        layer=layer,
        supports=True,
        trace_hash=outcome.trace_hash,
    )
    negative = _experiment(
        experiment_id=f"counterfactual:{negative_type.value}:{outcome.task_id}",
        experiment_type=negative_type,
        layer=negative_layer,
        supports=False,
        trace_hash=outcome.trace_hash,
    )
    return AttributionReport(
        root_cause_layer=layer,
        confidence=0.99,
        ranked_causes=[
            LayerScore(
                layer=layer,
                score=0.99,
                supporting_experiment_ids=[supporting.experiment_id],
            )
        ],
        evidence=["Exactly one bounded component counterfactual repaired the Task."],
        experiments=[supporting, negative],
        recommended_action=action,
        actionable=True,
        reason="Exactly one bounded component counterfactual repaired the Task.",
    )


def build_integrated_cases_from_initial_evaluation(
    evaluation: CompositeSnapshotEvaluation,
    *,
    policy: IntegratedRunPolicy,
) -> tuple[IntegratedCase, ...]:
    """Create one immutable Case for every failed frozen A0 Task."""

    if evaluation.round_index != 0 or evaluation.parent_evaluation_hash is not None:
        raise IntegratedCaseFactoryError(
            "Integrated initial cases require the round-zero evaluation."
        )
    failed = tuple(
        sorted(
            (item for item in evaluation.outcomes if not item.passed),
            key=lambda item: item.task_id,
        )
    )
    skill_failures = tuple(
        item for item in failed if item.track == CompositeTaskTrack.SKILL
    )
    policy_failures = tuple(
        item
        for item in failed
        if item.track == CompositeTaskTrack.LOCAL_POLICY
    )
    if len(skill_failures) != 1:
        raise IntegratedCaseFactoryError(
            "Controlled A0 requires exactly one failed Skill Task."
        )
    if len(policy_failures) < policy.min_policy_cases:
        raise IntegratedCaseFactoryError(
            "Controlled A0 lacks the required distinct local-policy failures."
        )

    cases = []
    for outcome in failed:
        report = _report(outcome)
        evidence_hash = canonical_sha256(
            {
                "evaluation_hash": evaluation.evaluation_hash,
                "snapshot_manifest_hash": evaluation.snapshot_manifest_hash,
                "task_outcome": outcome.model_dump(mode="json"),
            }
        )
        case = build_integrated_case(
            report,
            policy=policy,
            case_id=f"integrated-case:{outcome.task_id}",
            trace_id=f"integrated-trace:{outcome.task_id}",
            task_id=outcome.task_id,
            evidence_hash=evidence_hash,
            source="controlled-composite-runtime",
            trust_level="verified",
            safety_flags=(),
            created_at=evaluation.evaluated_at,
        )
        cases.append(case)
    return tuple(cases)


__all__ = [
    "IntegratedCaseFactoryError",
    "build_integrated_cases_from_initial_evaluation",
]
