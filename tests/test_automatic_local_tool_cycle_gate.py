from __future__ import annotations

import pytest

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignGovernanceService,
    CampaignState,
    PersistentModelEvidenceAccumulator,
    SQLiteCampaignRepository,
)
from evoagent.campaigns.cycle import GovernedEvolutionCycleService
from evoagent.cycles import CycleStatus, EvolutionCycleRequest, StructuredVerifierSkillBackend
from evoagent.domain.models import Task
from evoagent.lab import AutomaticLocalToolEvolutionLab, IdempotentJsonlTraceStore
from evoagent.runtime import (
    DocumentSkillPolicy,
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    LocalToolCounterfactualRunner,
    RuntimeLimits,
    ToolAgentRuntime,
    snapshot_from_skill_spec,
)
from evoagent.skills import SQLiteSkillRegistry, SkillEvaluationDecision, SkillSpec
from evoagent.traces import TraceTrustLevel


def initial_skill() -> SkillSpec:
    return SkillSpec(
        skill_id="local_document_writer",
        name="Local Document Writer",
        version="1.0.0",
        description="Write a local document and verify it.",
        rules=("verify_after_write",),
        allowed_tools=("read_document", "write_document", "list_documents"),
        provenance="synthetic-test",
    )


def training_task() -> Task:
    return Task(
        task_id="local:cycle-protected",
        task_type="local-document-evolution-train",
        input={
            "initial_documents": {
                "runtime.txt": {"content": "stable", "protected": True}
            },
            "target_path": "runtime.txt",
            "content": "replacement",
            "expected_status": "blocked",
            "require_verification": True,
        },
    )


def runtime(root):
    return ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(root / "episodes"),
        policy=DocumentSkillPolicy(),
        verifier=DocumentTaskVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=5),
        seed=41,
    )


def create_candidate_stage(tmp_path):
    skills = SQLiteSkillRegistry(tmp_path / "skills.db")
    skills.register_initial(initial_skill())
    campaigns = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    traces = IdempotentJsonlTraceStore(tmp_path / "traces.jsonl")
    governance = CampaignGovernanceService(campaigns)
    task = training_task()
    base_snapshot = snapshot_from_skill_spec(
        skills.active("local_document_writer").spec,
        snapshot_id="A0-cycle",
        round_index=0,
        model_id="synthetic/local-document-policy-v1",
    )
    baseline = runtime(tmp_path).run(task, base_snapshot)
    runner = LocalToolCounterfactualRunner(
        runtime_factory=lambda: runtime(tmp_path),
        task=task,
        baseline_snapshot=base_snapshot,
        baseline_trace=baseline,
    )
    service = GovernedEvolutionCycleService(
        trace_store=traces,
        skill_registry=skills,
        skill_backend=StructuredVerifierSkillBackend(),
        evidence_accumulator=PersistentModelEvidenceAccumulator(campaigns),
        campaign_governance=governance,
    )
    result = service.process(
        EvolutionCycleRequest(
            trace=baseline,
            source="synthetic-cycle-gate-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=runner,
    )
    return skills, campaigns, governance, result


def test_candidate_generation_keeps_base_active_and_campaign_unevaluated(tmp_path):
    skills, campaigns, _, result = create_candidate_stage(tmp_path)

    assert result.status == CycleStatus.SKILL_CANDIDATE
    assert result.skill_candidate is not None
    assert result.skill_patch is not None
    assert result.skill_patch.add_rules == ("inspect_before_write",)
    assert result.skill_candidate.version == "1.1.0"
    assert result.skill_candidate.rules == (
        "verify_after_write",
        "inspect_before_write",
    )
    assert skills.active("local_document_writer").spec.version == "1.0.0"
    candidate = skills.get("local_document_writer", "1.1.0")
    assert candidate.parent_version == "1.0.0"
    assert candidate.status.value == "candidate"

    campaign = campaigns.get(result.campaign_id)
    assert campaign.state == CampaignState.CANDIDATE_READY
    assert campaigns.approvals(campaign.campaign_id) == []

    decision = SkillEvaluationDecision(
        skill_id="local_document_writer",
        base_version="1.0.0",
        candidate_version="1.1.0",
        promote=True,
        base_score=0.5,
        candidate_score=1.0,
        regression_count=0,
        reason="synthetic gate passed",
    )
    with pytest.raises(RuntimeError, match="AUTHORIZED"):
        AutomaticLocalToolEvolutionLab.validate_authorized_candidate(
            campaign=campaign,
            candidate=candidate.spec,
            active=skills.active("local_document_writer").spec,
            decision=decision,
        )
    assert skills.active("local_document_writer").spec.version == "1.0.0"


def test_authorized_candidate_still_rejects_a_stale_active_parent(tmp_path):
    skills, campaigns, governance, result = create_candidate_stage(tmp_path)
    campaign = campaigns.get(result.campaign_id)
    campaign = governance.submit_evaluation(
        campaign.campaign_id,
        passed=True,
        expected_revision=campaign.revision,
        actor_id="independent-evaluator",
        reason="held-out evaluation passed",
    )
    campaign = governance.approve(
        campaign.campaign_id,
        actor_id="independent-reviewer",
        decision=ApprovalDecision.APPROVE,
        reason="risk review passed",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    candidate = skills.get("local_document_writer", "1.1.0").spec
    stale_active = skills.active("local_document_writer").spec.model_copy(
        update={"version": "0.9.0"}
    )
    decision = SkillEvaluationDecision(
        skill_id="local_document_writer",
        base_version="1.0.0",
        candidate_version="1.1.0",
        promote=True,
        base_score=0.5,
        candidate_score=1.0,
        regression_count=0,
        reason="held-out evaluation passed",
    )
    with pytest.raises(RuntimeError, match="stale"):
        AutomaticLocalToolEvolutionLab.validate_authorized_candidate(
            campaign=campaign,
            candidate=candidate,
            active=stale_active,
            decision=decision,
        )
    assert skills.active("local_document_writer").spec.version == "1.0.0"
