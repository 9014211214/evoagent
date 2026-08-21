import pytest

from evoagent.diagnosis.counterfactual import ExperimentResult, ExperimentType
from evoagent.diagnosis.counterfactual_engine import CounterfactualAttributionEngine
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.evolution.controller import EvolutionController


@pytest.mark.parametrize(
    ("fault", "expected_action"),
    [
        (FailureLayer.SKILL, EvolutionAction.UPDATE_SKILL),
        (FailureLayer.ROUTER, EvolutionAction.UPDATE_ROUTER),
        (FailureLayer.TOOL, EvolutionAction.REPAIR_TOOL),
        (FailureLayer.CONTEXT, EvolutionAction.UPDATE_CONTEXT),
        (FailureLayer.VERIFIER, EvolutionAction.REPAIR_VERIFIER),
        (FailureLayer.ENVIRONMENT, EvolutionAction.ESCALATE),
        (FailureLayer.MODEL, EvolutionAction.TRAIN_MODEL),
    ],
)
def test_single_fault_is_correctly_attributed(fault, expected_action):
    engine = CounterfactualAttributionEngine()
    runner = SyntheticCounterfactualRunner(
        SyntheticFaultScenario(scenario_id=f"case:{fault.value}", fault_layers={fault})
    )

    report = engine.diagnose(runner)

    assert report.root_cause_layer == fault
    assert report.recommended_action == expected_action
    assert report.confidence == 1.0
    assert report.actionable is (expected_action != EvolutionAction.ESCALATE)


def test_model_is_not_attributed_without_ruling_out_external_layers():
    engine = CounterfactualAttributionEngine()
    hypotheses = engine.default_hypotheses()
    model_hypothesis = next(item for item in hypotheses if item.layer == FailureLayer.MODEL)
    result = ExperimentResult(
        experiment_id="exp:model",
        hypothesis_id=model_hypothesis.hypothesis_id,
        experiment_type=ExperimentType.REFERENCE_MODEL,
        baseline_success=False,
        counterfactual_success=True,
        supports_hypothesis=True,
        confidence=1.0,
        evidence=["Reference model passed."],
    )

    report = engine.attribute([result], hypotheses)

    assert report.root_cause_layer == FailureLayer.UNKNOWN
    assert report.recommended_action == EvolutionAction.ESCALATE
    assert report.actionable is False


def test_conflicting_evidence_escalates():
    engine = CounterfactualAttributionEngine()
    hypotheses = engine.default_hypotheses()
    selected = [
        item for item in hypotheses
        if item.layer in {FailureLayer.SKILL, FailureLayer.TOOL}
    ]
    results = [
        ExperimentResult(
            experiment_id=f"exp:{hypothesis.layer.value}",
            hypothesis_id=hypothesis.hypothesis_id,
            experiment_type=hypothesis.experiment_type,
            baseline_success=False,
            counterfactual_success=True,
            supports_hypothesis=True,
            confidence=1.0,
            evidence=[f"{hypothesis.layer.value} intervention passed."],
        )
        for hypothesis in selected
    ]

    report = engine.attribute(results, hypotheses)

    assert report.root_cause_layer == FailureLayer.UNKNOWN
    assert report.recommended_action == EvolutionAction.ESCALATE
    assert "Conflicting" in report.reason


def test_multiple_simultaneous_faults_are_not_overattributed():
    engine = CounterfactualAttributionEngine()
    runner = SyntheticCounterfactualRunner(
        SyntheticFaultScenario(
            scenario_id="case:multi",
            fault_layers={FailureLayer.SKILL, FailureLayer.TOOL},
        )
    )

    report = engine.diagnose(runner)

    assert report.root_cause_layer == FailureLayer.UNKNOWN
    assert report.actionable is False


def test_model_ticket_created_only_after_full_model_gate():
    engine = CounterfactualAttributionEngine()
    runner = SyntheticCounterfactualRunner(
        SyntheticFaultScenario(scenario_id="case:model", fault_layers={FailureLayer.MODEL})
    )
    report = engine.diagnose(runner)

    ticket = EvolutionController().create_ticket(
        report,
        ticket_id="ticket:model:1",
        target_id="model-v0",
        evidence_trace_ids=["trace:model:1"],
    )

    assert ticket.target_layer == FailureLayer.MODEL
    assert ticket.proposed_action == EvolutionAction.TRAIN_MODEL


def test_non_actionable_report_cannot_create_ticket():
    engine = CounterfactualAttributionEngine()
    runner = SyntheticCounterfactualRunner(
        SyntheticFaultScenario(
            scenario_id="case:multi",
            fault_layers={FailureLayer.SKILL, FailureLayer.TOOL},
        )
    )
    report = engine.diagnose(runner)

    with pytest.raises(ValueError):
        EvolutionController().create_ticket(
            report,
            ticket_id="ticket:invalid",
            target_id=None,
            evidence_trace_ids=["trace:invalid"],
        )
