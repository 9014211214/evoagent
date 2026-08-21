import pytest

from evoagent.cycles import (
    CycleStatus,
    EvolutionCyclePolicy,
    EvolutionCycleRequest,
    EvolutionCycleService,
    ModelEvolutionSettings,
    StructuredVerifierSkillBackend,
)
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import ExecutionTrace, FailureLayer, Task
from evoagent.skills import SkillRegistry, SkillSpec, SkillVersionStatus
from evoagent.traces import DuplicateTraceError, JsonlTraceStore, TraceTrustLevel
from evoagent.training import (
    DatasetSignals,
    DryRunMLInternBackend,
    MetricTarget,
    MLInternCLIAdapter,
    TrainingBudget,
    TrainingMethod,
)


def make_trace(
    trace_id: str,
    *,
    passed: bool,
    task_id: str | None = None,
    model_id: str = "public/model-v0",
    skill_id: str | None = None,
    skill_version: str | None = None,
    feedback: str = "",
) -> ExecutionTrace:
    return ExecutionTrace(
        trace_id=trace_id,
        task=Task(
            task_id=task_id or f"task:{trace_id}",
            task_type="synthetic-decision",
            input={"trace_id": trace_id},
        ),
        model_id=model_id,
        skill_id=skill_id,
        skill_version=skill_version,
        observable_events=[{"event": "synthetic_execution"}],
        final_output={"status": "success" if passed else "failure"},
        verifier_passed=passed,
        verifier_feedback=feedback,
        cost={"llm_tokens": 10},
    )


def make_service(tmp_path, *, policy=None):
    registry = SkillRegistry()
    service = EvolutionCycleService(
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
        skill_registry=registry,
        skill_backend=StructuredVerifierSkillBackend(),
        policy=policy,
    )
    return service, registry


def runner(layer: FailureLayer):
    return SyntheticCounterfactualRunner(
        SyntheticFaultScenario(scenario_id=f"cycle:{layer.value}", fault_layers={layer})
    )


def test_successful_trace_is_stored_and_no_action(tmp_path):
    service, _ = make_service(tmp_path)
    result = service.process(
        EvolutionCycleRequest(
            trace=make_trace("trace:success", passed=True),
            source="synthetic-test",
            trust_level=TraceTrustLevel.SYNTHETIC,
        )
    )

    assert result.status == CycleStatus.NO_ACTION
    assert service.trace_store.get("trace:success").record_hash == result.trace_record_hash


def test_untrusted_or_blocking_safety_trace_is_quarantined(tmp_path):
    service, registry = make_service(tmp_path)
    untrusted = service.process(
        EvolutionCycleRequest(
            trace=make_trace("trace:untrusted", passed=False),
            source="external",
            trust_level=TraceTrustLevel.UNTRUSTED,
        ),
        counterfactual_runner=runner(FailureLayer.MODEL),
    )
    unsafe = service.process(
        EvolutionCycleRequest(
            trace=make_trace("trace:unsafe", passed=False),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
            safety_flags=("prompt_injection",),
        ),
        counterfactual_runner=runner(FailureLayer.SKILL),
    )

    assert untrusted.status == CycleStatus.QUARANTINED
    assert unsafe.status == CycleStatus.QUARANTINED
    assert registry.events() == []


def test_verified_skill_failure_creates_candidate_without_changing_active(tmp_path):
    service, registry = make_service(tmp_path)
    base = SkillSpec(
        skill_id="decision_skill",
        name="Decision Skill",
        version="1.0.0",
        description="Handle safe cases.",
        rules=("accept_safe",),
    )
    registry.register_initial(base)
    request = EvolutionCycleRequest(
        trace=make_trace(
            "trace:skill",
            passed=False,
            skill_id=base.skill_id,
            skill_version=base.version,
            feedback="missing_skill_rule: reject_unsafe",
        ),
        source="synthetic-test",
        trust_level=TraceTrustLevel.VERIFIED,
    )

    result = service.process(request, counterfactual_runner=runner(FailureLayer.SKILL))

    assert result.status == CycleStatus.SKILL_CANDIDATE
    assert result.skill_candidate.version == "1.1.0"
    assert result.skill_candidate.rules == ("accept_safe", "reject_unsafe")
    assert registry.active(base.skill_id).spec.version == "1.0.0"
    assert registry.get(base.skill_id, "1.1.0").status == SkillVersionStatus.CANDIDATE


def test_stale_skill_trace_is_escalated(tmp_path):
    service, registry = make_service(tmp_path)
    registry.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
            rules=("accept_safe",),
        )
    )
    result = service.process(
        EvolutionCycleRequest(
            trace=make_trace(
                "trace:stale",
                passed=False,
                skill_id="decision_skill",
                skill_version="0.9.0",
                feedback="missing_skill_rule: reject_unsafe",
            ),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=runner(FailureLayer.SKILL),
    )
    assert result.status == CycleStatus.ESCALATED
    assert registry.list_versions("decision_skill")[0].spec.version == "1.0.0"


def test_unknown_attribution_escalates(tmp_path):
    service, _ = make_service(tmp_path)
    result = service.process(
        EvolutionCycleRequest(
            trace=make_trace("trace:unknown", passed=False),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=SyntheticCounterfactualRunner(
            SyntheticFaultScenario(
                scenario_id="cycle:multi",
                fault_layers={FailureLayer.SKILL, FailureLayer.TOOL},
            )
        ),
    )
    assert result.status == CycleStatus.ESCALATED
    assert result.attribution.root_cause_layer == FailureLayer.UNKNOWN


def test_nonimplemented_external_repair_creates_ticket_only(tmp_path):
    service, _ = make_service(tmp_path)
    result = service.process(
        EvolutionCycleRequest(
            trace=make_trace("trace:tool", passed=False),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=runner(FailureLayer.TOOL),
    )
    assert result.status == CycleStatus.TICKET_CREATED
    assert result.evolution_ticket.target_layer == FailureLayer.TOOL
    assert result.model_candidate is None


def test_model_failure_requires_repeated_distinct_evidence(tmp_path):
    policy = EvolutionCyclePolicy(model_min_traces=3, model_min_distinct_tasks=3)
    service, _ = make_service(tmp_path, policy=policy)
    settings = ModelEvolutionSettings(
        problem_cluster="multi-step-planning",
        target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
        dataset_signals=DatasetSignals(gold_trajectories=3),
        allowed_methods=(TrainingMethod.SFT,),
        budget=TrainingBudget(max_gpu_hours=2, max_training_tokens=50000),
        safety_constraints=("public or synthetic data only", "no automatic deployment"),
    )
    backend = DryRunMLInternBackend(
        MLInternCLIAdapter(execution_enabled=False),
        workspace=str(tmp_path / "model-run"),
    )

    results = []
    for index in range(1, 4):
        results.append(
            service.process(
                EvolutionCycleRequest(
                    trace=make_trace(
                        f"trace:model:{index}",
                        task_id=f"task:model:{index}",
                        passed=False,
                    ),
                    source="synthetic-test",
                    trust_level=TraceTrustLevel.VERIFIED,
                    model_settings=settings,
                ),
                counterfactual_runner=runner(FailureLayer.MODEL),
                model_backend=backend,
            )
        )

    assert results[0].status == CycleStatus.MODEL_EVIDENCE_ACCUMULATED
    assert results[1].status == CycleStatus.MODEL_EVIDENCE_ACCUMULATED
    assert results[2].status == CycleStatus.MODEL_CANDIDATE
    assert results[2].model_evidence.ready is True
    assert results[2].model_candidate.status == "candidate"
    assert results[2].model_candidate.task_spec.execution_enabled is False


def test_duplicate_trace_id_is_rejected_before_second_cycle(tmp_path):
    service, _ = make_service(tmp_path)
    request = EvolutionCycleRequest(
        trace=make_trace("trace:duplicate", passed=True),
        source="synthetic-test",
        trust_level=TraceTrustLevel.SYNTHETIC,
    )
    service.process(request)
    with pytest.raises(DuplicateTraceError):
        service.process(request)


def test_failed_trace_without_counterfactual_runner_escalates(tmp_path):
    service, _ = make_service(tmp_path)
    result = service.process(
        EvolutionCycleRequest(
            trace=make_trace("trace:no-runner", passed=False),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        )
    )
    assert result.status == CycleStatus.ESCALATED
    assert service.trace_store.get("trace:no-runner").trace.verifier_passed is False


def test_repeated_failures_from_one_task_do_not_satisfy_distinct_task_threshold(tmp_path):
    policy = EvolutionCyclePolicy(model_min_traces=3, model_min_distinct_tasks=3)
    service, _ = make_service(tmp_path, policy=policy)
    settings = ModelEvolutionSettings(
        problem_cluster="multi-step-planning",
        target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
        dataset_signals=DatasetSignals(gold_trajectories=3),
        allowed_methods=(TrainingMethod.SFT,),
        budget=TrainingBudget(max_gpu_hours=2, max_training_tokens=50000),
    )
    last = None
    for index in range(1, 4):
        last = service.process(
            EvolutionCycleRequest(
                trace=make_trace(
                    f"trace:same-task:{index}",
                    task_id="task:repeated",
                    passed=False,
                ),
                source="synthetic-test",
                trust_level=TraceTrustLevel.VERIFIED,
                model_settings=settings,
            ),
            counterfactual_runner=runner(FailureLayer.MODEL),
        )
    assert last.status == CycleStatus.MODEL_EVIDENCE_ACCUMULATED
    assert len(last.model_evidence.trace_ids) == 3
    assert len(last.model_evidence.task_ids) == 1
    assert last.model_evidence.ready is False
