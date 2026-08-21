import pytest

from evoagent.diagnosis.counterfactual_engine import CounterfactualAttributionEngine
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import FailureLayer
from evoagent.training import (
    DatasetSignals,
    DryRunMLInternBackend,
    MetricTarget,
    MLInternCLIAdapter,
    ModelEvolutionOrchestrator,
    ModelTicketFactory,
    NoTrainingStrategyError,
    TrainingBudget,
    TrainingMethod,
    TrainingStrategySelector,
)


def report_for(layer: FailureLayer):
    return CounterfactualAttributionEngine().diagnose(
        SyntheticCounterfactualRunner(
            SyntheticFaultScenario(scenario_id=f"case:{layer.value}", fault_layers={layer})
        )
    )


def make_ticket(signals: DatasetSignals, allowed: tuple[TrainingMethod, ...], budget=None):
    return ModelTicketFactory().create(
        report_for(FailureLayer.MODEL),
        ticket_id="model-ticket-1",
        base_model_id="public/model-v0",
        problem_cluster="multi-step constraint planning",
        evidence_trace_ids=("trace:1", "trace:2"),
        target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
        dataset_signals=signals,
        allowed_methods=allowed,
        budget=budget or TrainingBudget(max_gpu_hours=4, max_rollouts=100, max_training_tokens=100000),
        replay_environment="synthetic-env:v1" if signals.replayable_environment else None,
        safety_constraints=("no private data", "no automatic deployment"),
    )


def test_non_model_attribution_cannot_create_model_ticket():
    with pytest.raises(ValueError):
        ModelTicketFactory().create(
            report_for(FailureLayer.SKILL),
            ticket_id="bad-ticket",
            base_model_id="public/model-v0",
            problem_cluster="not a model problem",
            evidence_trace_ids=("trace:1",),
            target_metrics=(MetricTarget(name="task_success"),),
            dataset_signals=DatasetSignals(gold_trajectories=1),
            allowed_methods=(TrainingMethod.SFT,),
            budget=TrainingBudget(max_training_tokens=1000),
        )


def test_agentic_rl_requires_replay_reset_verifier_and_rollout_budget():
    ticket = make_ticket(
        DatasetSignals(
            gold_trajectories=10,
            replayable_environment=True,
            resettable_environment=True,
            machine_verifier=True,
        ),
        (TrainingMethod.SFT, TrainingMethod.AGENTIC_RL),
    )
    assert TrainingStrategySelector().select(ticket).method == TrainingMethod.AGENTIC_RL


def test_dpo_and_sft_fallbacks():
    dpo = make_ticket(
        DatasetSignals(preference_pairs=12),
        (TrainingMethod.SFT, TrainingMethod.DPO),
    )
    sft = make_ticket(
        DatasetSignals(gold_trajectories=12),
        (TrainingMethod.SFT,),
    )
    assert TrainingStrategySelector().select(dpo).method == TrainingMethod.DPO
    assert TrainingStrategySelector().select(sft).method == TrainingMethod.SFT


def test_missing_training_signal_escalates():
    ticket = make_ticket(DatasetSignals(), (TrainingMethod.SFT, TrainingMethod.DPO))
    with pytest.raises(NoTrainingStrategyError):
        TrainingStrategySelector().select(ticket)


def test_ml_intern_task_is_sandboxed_and_contains_no_secret_values():
    ticket = make_ticket(DatasetSignals(gold_trajectories=5), (TrainingMethod.SFT,))
    plan = TrainingStrategySelector().select(ticket)
    adapter = MLInternCLIAdapter(execution_enabled=False)
    spec = adapter.build_task(ticket, plan, workspace="/tmp/evoagent-model-ticket")

    assert "--sandbox-tools" in spec.command
    assert spec.runtime_config["share_traces"] is False
    assert spec.runtime_config["tool_runtime"] == "sandbox"
    assert spec.required_environment_variables == ("HF_TOKEN",)
    assert "HF_TOKEN=" not in spec.prompt
    assert spec.execution_enabled is False
    with pytest.raises(PermissionError):
        adapter.execute(spec, environment={"HF_TOKEN": "secret-value"})


def test_orchestrator_returns_candidate_without_deployment():
    ticket = make_ticket(DatasetSignals(gold_trajectories=5), (TrainingMethod.SFT,))
    backend = DryRunMLInternBackend(
        MLInternCLIAdapter(execution_enabled=False),
        workspace="/tmp/evoagent-model-ticket",
    )
    candidate = ModelEvolutionOrchestrator().run(ticket, backend)

    assert candidate.status == "candidate"
    assert candidate.artifact_uri.startswith("ml-intern-task://")
    assert candidate.task_spec is not None
    assert candidate.task_spec.execution_enabled is False


def test_ticket_records_all_external_layers_as_ruled_out():
    ticket = make_ticket(DatasetSignals(gold_trajectories=1), (TrainingMethod.SFT,))
    assert set(ticket.ruled_out_layers) == {
        FailureLayer.SKILL,
        FailureLayer.ROUTER,
        FailureLayer.TOOL,
        FailureLayer.CONTEXT,
        FailureLayer.VERIFIER,
        FailureLayer.ENVIRONMENT,
    }
