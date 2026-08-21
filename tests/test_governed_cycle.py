from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    PersistentModelEvidenceAccumulator,
    SQLiteCampaignRepository,
)
from evoagent.campaigns.cycle import GovernedEvolutionCycleService
from evoagent.cycles import (
    CycleStatus,
    EvolutionCyclePolicy,
    EvolutionCycleRequest,
    ModelEvolutionSettings,
    StructuredVerifierSkillBackend,
)
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import ExecutionTrace, FailureLayer, Task
from evoagent.skills import SkillRegistry, SkillSpec
from evoagent.traces import JsonlTraceStore, TraceTrustLevel
from evoagent.training import (
    DatasetSignals,
    DryRunMLInternBackend,
    MetricTarget,
    MLInternCLIAdapter,
    TrainingBudget,
    TrainingMethod,
)


def trace(
    trace_id: str,
    *,
    task_id: str,
    skill_id: str | None = None,
    skill_version: str | None = None,
    feedback: str = "",
) -> ExecutionTrace:
    return ExecutionTrace(
        trace_id=trace_id,
        task=Task(task_id=task_id, task_type="synthetic-decision", input={}),
        model_id="public/model-v0",
        skill_id=skill_id,
        skill_version=skill_version,
        observable_events=[{"event": "synthetic_execution"}],
        final_output={"status": "failure"},
        verifier_passed=False,
        verifier_feedback=feedback,
        cost={"llm_tokens": 10},
    )


def runner(layer: FailureLayer):
    return SyntheticCounterfactualRunner(
        SyntheticFaultScenario(scenario_id=f"campaign:{layer.value}", fault_layers={layer})
    )


def service(tmp_path, registry, repository, *, policy=None):
    return GovernedEvolutionCycleService(
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
        skill_registry=registry,
        skill_backend=StructuredVerifierSkillBackend(),
        policy=policy,
        evidence_accumulator=PersistentModelEvidenceAccumulator(repository),
        campaign_governance=CampaignGovernanceService(repository),
    )


def test_duplicate_skill_patch_reuses_open_campaign_and_candidate(tmp_path):
    repository = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    registry = SkillRegistry()
    registry.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
            rules=("accept_safe",),
        )
    )
    cycle = service(tmp_path, registry, repository)

    first = cycle.process(
        EvolutionCycleRequest(
            trace=trace(
                "trace:skill:1",
                task_id="task:skill:1",
                skill_id="decision_skill",
                skill_version="1.0.0",
                feedback="missing_skill_rule: reject_unsafe",
            ),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=runner(FailureLayer.SKILL),
    )
    second = cycle.process(
        EvolutionCycleRequest(
            trace=trace(
                "trace:skill:2",
                task_id="task:skill:2",
                skill_id="decision_skill",
                skill_version="1.0.0",
                feedback="missing_skill_rule: reject_unsafe",
            ),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=runner(FailureLayer.SKILL),
    )

    assert first.status == CycleStatus.SKILL_CANDIDATE
    assert second.status == CycleStatus.SKILL_CANDIDATE
    assert second.reused is True
    assert first.campaign_id == second.campaign_id
    assert second.skill_candidate.version == "1.1.0"
    assert len(registry.list_versions("decision_skill")) == 2
    assert registry.active("decision_skill").spec.version == "1.0.0"


def test_model_evidence_and_campaign_survive_restart_without_duplicate_candidate(tmp_path):
    database = tmp_path / "campaigns.db"
    policy = EvolutionCyclePolicy(model_min_traces=3, model_min_distinct_tasks=3)
    settings = ModelEvolutionSettings(
        problem_cluster="multi-step-planning",
        target_metrics=(MetricTarget(name="task_success", minimum_improvement=0.1),),
        dataset_signals=DatasetSignals(gold_trajectories=4),
        allowed_methods=(TrainingMethod.SFT,),
        budget=TrainingBudget(max_gpu_hours=2, max_training_tokens=50000),
    )
    backend = DryRunMLInternBackend(
        MLInternCLIAdapter(execution_enabled=False),
        workspace=str(tmp_path / "model-run"),
    )

    first_repo = SQLiteCampaignRepository(database)
    first_service = service(tmp_path, SkillRegistry(), first_repo, policy=policy)
    for index in range(1, 3):
        result = first_service.process(
            EvolutionCycleRequest(
                trace=trace(f"trace:model:{index}", task_id=f"task:model:{index}"),
                source="synthetic-test",
                trust_level=TraceTrustLevel.VERIFIED,
                model_settings=settings,
            ),
            counterfactual_runner=runner(FailureLayer.MODEL),
            model_backend=backend,
        )
        assert result.status == CycleStatus.MODEL_EVIDENCE_ACCUMULATED

    restarted_repo = SQLiteCampaignRepository(database)
    restarted_service = service(tmp_path, SkillRegistry(), restarted_repo, policy=policy)
    third = restarted_service.process(
        EvolutionCycleRequest(
            trace=trace("trace:model:3", task_id="task:model:3"),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
            model_settings=settings,
        ),
        counterfactual_runner=runner(FailureLayer.MODEL),
        model_backend=backend,
    )
    fourth = restarted_service.process(
        EvolutionCycleRequest(
            trace=trace("trace:model:4", task_id="task:model:4"),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
            model_settings=settings,
        ),
        counterfactual_runner=runner(FailureLayer.MODEL),
        model_backend=backend,
    )

    assert third.status == CycleStatus.MODEL_CANDIDATE
    assert fourth.status == CycleStatus.MODEL_CANDIDATE
    assert fourth.reused is True
    assert third.campaign_id == fourth.campaign_id
    assert fourth.model_candidate.candidate_id == third.model_candidate.candidate_id
    campaign = restarted_repo.get(third.campaign_id)
    assert campaign.state == CampaignState.CANDIDATE_READY
    assert campaign.required_approvals == 2
    assert len(fourth.model_evidence.trace_ids) == 4


def test_repeated_external_fault_reuses_ticket_campaign(tmp_path):
    repository = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    cycle = service(tmp_path, SkillRegistry(), repository)

    first = cycle.process(
        EvolutionCycleRequest(
            trace=trace("trace:tool:1", task_id="task:tool:1"),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=runner(FailureLayer.TOOL),
    )
    second = cycle.process(
        EvolutionCycleRequest(
            trace=trace("trace:tool:2", task_id="task:tool:2"),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=runner(FailureLayer.TOOL),
    )

    assert first.status == CycleStatus.TICKET_CREATED
    assert second.status == CycleStatus.TICKET_CREATED
    assert second.reused is True
    assert first.campaign_id == second.campaign_id
    assert second.evolution_ticket.ticket_id == first.evolution_ticket.ticket_id
