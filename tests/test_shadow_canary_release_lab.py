from evoagent.lab import ShadowCanaryReleaseLab


def test_shadow_canary_release_lab_is_restart_safe(tmp_path):
    lab = ShadowCanaryReleaseLab(tmp_path / "release-lab", source_commit="9" * 40)
    first = lab.run()
    second = lab.run()

    assert first.resumed is False
    assert second.resumed is True
    assert first.drift.package_hash == second.drift.package_hash
    assert first.passing.package_hash == second.passing.package_hash
    assert second.incumbent_snapshot_id == "evoagent-a0"
    assert second.challenger_snapshot_id == "evoagent-a1"

    assert second.drift.actions == ("advance", "advance", "rollback")
    assert second.drift.assessment_statuses == ("pass", "pass", "rollback")
    assert second.drift.final_state == "rolled_back"
    assert second.drift.final_primary_snapshot_id == "evoagent-a0"
    assert second.drift.final_active_stage_id is None
    assert second.drift.final_candidate_allocation_percent == 0.0
    assert second.drift.release_campaign_state == "completed"
    assert second.drift.release_approval_count == 2
    assert second.drift.rollback_campaign_state == "completed"
    assert second.drift.rollback_approval_count == 2
    assert "maximum_safety_violations_exceeded" in second.drift.rollback_reasons
    assert "protected_segment_regression:protected" in second.drift.rollback_reasons

    assert second.passing.actions == ("advance", "advance", "ready")
    assert second.passing.assessment_statuses == ("pass", "pass", "pass")
    assert second.passing.final_state == "ready"
    assert second.passing.final_primary_snapshot_id == "evoagent-a0"
    assert second.passing.final_active_stage_id == "canary-25"
    assert second.passing.final_candidate_allocation_percent == 25.0
    assert second.passing.rollback_campaign_id is None
    assert second.passing.rollback_approval_count == 0

    assert second.external_model_call_performed_by_evoagent is False
    assert second.training_executed_by_evoagent is False
    assert second.external_rollout_performed_by_evoagent is False
    assert second.production_traffic_observed_by_evoagent is False
    assert second.production_deployment_performed is False
    assert second.external_rollback_performed is False
    assert second.upload_performed is False
    assert second.official_benchmark_claimed is False