from __future__ import annotations

from evoagent.campaigns import CampaignOperatorView, CampaignState, SQLiteCampaignRepository
from evoagent.lab import AutomaticLocalToolEvolutionLab
from evoagent.skills import SQLiteSkillRegistry, SkillEventType
from evoagent.traces import JsonlTraceStore


def test_automatic_local_tool_lab_completes_and_resumes_without_duplicates(tmp_path):
    lab = AutomaticLocalToolEvolutionLab(tmp_path / "lab")
    first = lab.run()

    assert first.resumed is False
    assert first.training_task_id not in first.frozen_task_ids
    assert first.attribution.root_cause_layer.value == "skill"
    assert first.attribution.recommended_action.value == "update_skill"
    assert first.added_rules == ("inspect_before_write",)
    assert first.base_version == "1.0.0"
    assert first.candidate_version == "1.1.0"
    assert first.active_version == "1.1.0"
    assert first.campaign_state == CampaignState.COMPLETED.value
    assert first.summary.initial_score == 0.5
    assert first.summary.final_score == 1.0
    assert first.summary.evolution_gain == 0.5
    assert first.regression_count == 0
    assert first.external_execution_performed is False
    assert first.restart_verified is True
    assert first.skill_version_count == 2
    assert first.campaign_count == 1
    assert first.approval_count == 1
    assert first.persisted_trace_count == 1
    assert first.promotion_event_count == 1
    assert len(first.counterfactual_trace_ids) == 7

    base_eval, candidate_eval = first.evolution_run.evaluations
    assert base_eval.per_task == {
        "local:create-note": 1.0,
        "local:protected-policy": 0.0,
    }
    assert candidate_eval.per_task == {
        "local:create-note": 1.0,
        "local:protected-policy": 1.0,
    }
    assert base_eval.model_id == candidate_eval.model_id
    assert base_eval.manifest_fingerprint == candidate_eval.manifest_fingerprint
    assert first.snapshots[0].model_id == first.snapshots[1].model_id

    skills = SQLiteSkillRegistry(lab.skill_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    traces = JsonlTraceStore(lab.trace_file)
    version_count = len(skills.list_versions(first.skill_id))
    skill_events = skills.events(first.skill_id)
    campaign_count = len(CampaignOperatorView(campaigns).list_campaigns())
    approval_count = len(campaigns.approvals(first.campaign_id))
    trace_count = len(traces.list())

    second = lab.run()

    assert second.resumed is True
    assert second.campaign_id == first.campaign_id
    assert second.training_trace_id == first.training_trace_id
    assert second.active_version == first.active_version
    assert second.candidate_version == first.candidate_version
    assert second.summary == first.summary
    assert second.evolution_run.protocol == first.evolution_run.protocol
    assert second.added_rules == first.added_rules
    assert second.skill_checkpoint == first.skill_checkpoint
    assert second.campaign_checkpoint == first.campaign_checkpoint
    assert second.trace_checkpoint == first.trace_checkpoint
    assert len(SQLiteSkillRegistry(lab.skill_database).list_versions(first.skill_id)) == version_count
    assert len(SQLiteSkillRegistry(lab.skill_database).events(first.skill_id)) == len(skill_events)
    assert len(CampaignOperatorView(SQLiteCampaignRepository(lab.campaign_database)).list_campaigns()) == campaign_count
    assert len(SQLiteCampaignRepository(lab.campaign_database).approvals(first.campaign_id)) == approval_count
    assert len(JsonlTraceStore(lab.trace_file).list()) == trace_count
    assert second.skill_version_count == 2
    assert second.campaign_count == 1
    assert second.approval_count == 1
    assert second.persisted_trace_count == 1
    assert second.promotion_event_count == 1

    final_events = SQLiteSkillRegistry(lab.skill_database).events(first.skill_id)
    assert sum(
        item.event_type == SkillEventType.CANDIDATE_CREATED.value
        for item in final_events
    ) == 1
    assert sum(
        item.event_type == SkillEventType.PROMOTED.value
        for item in final_events
    ) == 1


def test_promoted_candidate_is_verifier_derived_not_a_prebuilt_a1(tmp_path):
    lab = AutomaticLocalToolEvolutionLab(tmp_path / "lab")
    result = lab.run()
    skills = SQLiteSkillRegistry(lab.skill_database)
    base = skills.get(result.skill_id, result.base_version)
    candidate = skills.get(result.skill_id, result.candidate_version)

    assert base.spec.generated_by == "automatic-local-tool-bootstrap:v1.2"
    assert candidate.spec.generated_by == "structured-verifier-skill-backend:v0.7"
    assert candidate.parent_version == base.spec.version
    assert candidate.spec.rules == (
        "verify_after_write",
        "inspect_before_write",
    )
    assert candidate.spec.procedure == base.spec.procedure
    assert candidate.spec.procedure_kinds == base.spec.procedure_kinds
    assert candidate.spec.allowed_tools == base.spec.allowed_tools
    assert candidate.spec.source_refs[-1] == result.training_trace_id


def test_counterfactual_and_held_out_traces_are_not_persisted_as_training_evidence(tmp_path):
    lab = AutomaticLocalToolEvolutionLab(tmp_path / "lab")
    result = lab.run()
    persisted = JsonlTraceStore(lab.trace_file).list()

    assert [item.trace.trace_id for item in persisted] == [result.training_trace_id]
    counterfactual_ids = set(result.counterfactual_trace_ids.values())
    held_out_ids = {
        trace_id
        for per_task in result.held_out_trace_ids.values()
        for trace_id in per_task.values()
    }
    assert result.training_trace_id not in counterfactual_ids
    assert result.training_trace_id not in held_out_ids
    assert counterfactual_ids.isdisjoint(held_out_ids)
