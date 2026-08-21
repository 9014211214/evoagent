from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evoagent.diagnosis import (
    AttributionReport,
    ExperimentResult,
    ExperimentType,
    LayerScore,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.integrated import (
    IntegratedTrack,
    build_integrated_case,
    build_integrated_run_policy,
)


POLICY = build_integrated_run_policy()
NOW = datetime.now(timezone.utc) - timedelta(minutes=1)


def _experiment(
    experiment_id: str,
    experiment_type: ExperimentType,
    layer: FailureLayer,
    *,
    supports: bool,
) -> ExperimentResult:
    return ExperimentResult(
        experiment_id=experiment_id,
        hypothesis_id=f"hypothesis:{layer.value}:{experiment_id}",
        experiment_type=experiment_type,
        baseline_success=False,
        counterfactual_success=supports,
        supports_hypothesis=supports,
        evidence=(
            [f"bounded counterfactual evidence for {experiment_id}"]
            if supports
            else [f"negative counterfactual control for {experiment_id}"]
        ),
        metadata={"hypothesis_layer": layer.value},
        confidence=0.95 if supports else 0.2,
    )


def _report(
    *,
    layer: FailureLayer,
    action: EvolutionAction,
    confidence: float,
    experiments: tuple[ExperimentResult, ...],
    actionable: bool = True,
) -> AttributionReport:
    supporting = [
        item.experiment_id
        for item in experiments
        if item.supports_hypothesis
    ]
    return AttributionReport(
        root_cause_layer=layer,
        confidence=confidence,
        ranked_causes=[
            LayerScore(
                layer=layer,
                score=confidence,
                supporting_experiment_ids=supporting,
            )
        ],
        evidence=["Bounded observable counterfactual attribution."],
        experiments=list(experiments),
        recommended_action=action,
        actionable=actionable,
        reason="Exactly the persisted observable counterfactuals determine routing.",
    )


def _case(report, *, case_id, trust="verified", flags=()):
    return build_integrated_case(
        report,
        policy=POLICY,
        case_id=case_id,
        trace_id=f"trace:{case_id}",
        task_id=f"task:{case_id}",
        evidence_hash="a" * 64,
        source="controlled-local-runtime",
        trust_level=trust,
        safety_flags=flags,
        created_at=NOW,
    )


def test_unique_skill_counterfactual_routes_to_skill_track():
    report = _report(
        layer=FailureLayer.SKILL,
        action=EvolutionAction.UPDATE_SKILL,
        confidence=0.95,
        experiments=(
            _experiment(
                "experiment:replace-skill",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
            _experiment(
                "experiment:reference-model-negative",
                ExperimentType.REFERENCE_MODEL,
                FailureLayer.MODEL,
                supports=False,
            ),
        ),
    )

    case = _case(report, case_id="case:protected-document-skill")

    assert case.track == IntegratedTrack.SKILL
    assert case.supporting_experiment_ids == ("experiment:replace-skill",)
    assert case.root_cause_layer == FailureLayer.SKILL
    assert case.recommended_action == EvolutionAction.UPDATE_SKILL


def test_unique_reference_policy_counterfactual_routes_to_local_policy():
    report = _report(
        layer=FailureLayer.MODEL,
        action=EvolutionAction.TRAIN_MODEL,
        confidence=0.96,
        experiments=(
            _experiment(
                "experiment:replace-skill-negative",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=False,
            ),
            _experiment(
                "experiment:reference-policy",
                ExperimentType.REFERENCE_MODEL,
                FailureLayer.MODEL,
                supports=True,
            ),
        ),
    )

    case = _case(report, case_id="case:protected-document-policy")

    assert case.track == IntegratedTrack.LOCAL_POLICY
    assert case.supporting_experiment_ids == (
        "experiment:reference-policy",
    )
    assert case.root_cause_layer == FailureLayer.MODEL
    assert case.recommended_action == EvolutionAction.TRAIN_MODEL


def test_multiple_successful_counterfactuals_escalate():
    report = _report(
        layer=FailureLayer.MODEL,
        action=EvolutionAction.TRAIN_MODEL,
        confidence=0.96,
        experiments=(
            _experiment(
                "experiment:replace-skill-also-passes",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
            _experiment(
                "experiment:reference-policy-also-passes",
                ExperimentType.REFERENCE_MODEL,
                FailureLayer.MODEL,
                supports=True,
            ),
        ),
    )

    case = _case(report, case_id="case:ambiguous-mixed-failure")

    assert case.track == IntegratedTrack.ESCALATION
    assert len(case.supporting_experiment_ids) == 2


def test_low_confidence_or_non_actionable_report_escalates():
    low_confidence = _report(
        layer=FailureLayer.SKILL,
        action=EvolutionAction.UPDATE_SKILL,
        confidence=0.5,
        experiments=(
            _experiment(
                "experiment:low-confidence-skill",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
        ),
    )
    non_actionable = _report(
        layer=FailureLayer.SKILL,
        action=EvolutionAction.UPDATE_SKILL,
        confidence=0.95,
        experiments=(
            _experiment(
                "experiment:non-actionable-skill",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
        ),
        actionable=False,
    )

    assert _case(
        low_confidence,
        case_id="case:low-confidence",
    ).track == IntegratedTrack.ESCALATION
    assert _case(
        non_actionable,
        case_id="case:non-actionable",
    ).track == IntegratedTrack.ESCALATION


def test_untrusted_or_safety_marked_case_is_quarantined():
    report = _report(
        layer=FailureLayer.SKILL,
        action=EvolutionAction.UPDATE_SKILL,
        confidence=0.95,
        experiments=(
            _experiment(
                "experiment:trusted-shape",
                ExperimentType.REPLACE_SKILL,
                FailureLayer.SKILL,
                supports=True,
            ),
        ),
    )

    untrusted = _case(
        report,
        case_id="case:untrusted",
        trust="untrusted",
    )
    safety = _case(
        report,
        case_id="case:safety-flag",
        flags=("unsafe_tool_attempt",),
    )

    assert untrusted.track == IntegratedTrack.QUARANTINE
    assert safety.track == IntegratedTrack.QUARANTINE
    assert safety.safety_flags == ("unsafe_tool_attempt",)


def test_wrong_counterfactual_kind_does_not_enter_automatic_track():
    report = _report(
        layer=FailureLayer.SKILL,
        action=EvolutionAction.UPDATE_SKILL,
        confidence=0.95,
        experiments=(
            _experiment(
                "experiment:wrong-kind",
                ExperimentType.REFERENCE_MODEL,
                FailureLayer.MODEL,
                supports=True,
            ),
        ),
    )

    case = _case(report, case_id="case:wrong-counterfactual-kind")

    assert case.track == IntegratedTrack.ESCALATION
