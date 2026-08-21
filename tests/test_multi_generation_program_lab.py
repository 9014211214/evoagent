from evoagent.lab import MultiGenerationEvolutionProgramLab


def test_multi_generation_program_closes_release_feedback_loop(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="a" * 40,
    )
    first = lab.run()
    second = lab.run()

    assert first.resumed is False
    assert second.resumed is True
    assert first.package_hash == second.package_hash
    assert second.program_state == "completed"
    assert second.decision_actions == ("continue", "stop_success")
    assert second.generation_statuses == ("rolled_back", "completed")
    assert second.generation_count == 2
    assert second.rollback_count == 1
    assert second.generation_campaign_count == 1
    assert second.generation_campaign_state == "completed"
    assert second.approval_count == 2
    assert second.active_generation_id == "program-generation:g1"
    assert second.final_revision == 6
    assert second.authorization_started_generation is False
    assert second.same_champion_snapshot is True
    assert second.g0_agent_identity_hash != second.g1_agent_identity_hash
    assert second.g0_runtime_config_sha256 != second.g1_runtime_config_sha256
    assert second.budget_control_action == "stop_budget"
    assert second.budget_control_state == "budget_exhausted"
    assert second.ambiguous_control_action == "escalate"
    assert second.ambiguous_control_state == "escalated"
    assert second.program_event_count == 12
    assert second.campaign_event_count == 7
    assert second.external_model_call_performed_by_evoagent is False
    assert second.training_executed_by_evoagent is False
    assert second.external_rollout_performed_by_evoagent is False
    assert second.production_traffic_observed_by_evoagent is False
    assert second.production_deployment_performed is False
    assert second.upload_performed is False
    assert second.official_benchmark_claimed is False
