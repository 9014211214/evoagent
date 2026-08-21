from evoagent.champion import ChampionDecisionPackageManager
from evoagent.lab import BenchmarkGatedChampionLab


def test_complete_benchmark_gated_champion_lab_and_read_only_resume(tmp_path):
    lab = BenchmarkGatedChampionLab(
        tmp_path / "champion-lab",
        source_commit="b" * 40,
    )
    first = lab.run()
    second = lab.run()

    assert first.resumed is False
    assert second.resumed is True
    assert first.package_hash == second.package_hash
    assert first.decision_hash == second.decision_hash
    assert second.baseline_score == 0.25
    assert second.a1_score == 0.5
    assert second.a2_score == 0.75
    assert second.selected_run_id == "benchmark-run:a1"
    assert second.selected_snapshot_id == "evoagent-a1"
    assert second.selected_round == 1
    assert second.selected_score == 0.5
    assert second.a1_status == "eligible"
    assert second.a2_status == "rejected"
    assert "maximum_regressed_tasks_exceeded" in second.a2_reasons
    assert second.stop_recommendation == "stop"
    assert second.continue_evolution is False
    assert second.bootstrap_lower_bound <= 0.25
    assert second.bootstrap_upper_bound >= 0.25
    assert second.approval_count == 2
    assert second.required_approvals == 2
    assert second.campaign_state == "completed"
    assert second.active_snapshot_id == "evoagent-a1"
    assert second.active_revision == 1
    assert second.champion_record_count == 2
    assert second.champion_event_count == 6
    assert second.campaign_event_count == 7
    assert second.second_run_read_only is True
    assert second.synthetic_fixture is True
    assert second.harbor_execution_performed_by_evoagent is False
    assert second.external_model_call_performed_by_evoagent is False
    assert second.training_executed_by_evoagent is False
    assert second.checkpoint_downloaded_or_loaded is False
    assert second.upload_performed is False
    assert second.official_submission_performed is False
    assert second.official_submission_accepted is False
    assert second.production_deployment_performed is False

    package = ChampionDecisionPackageManager().load_file(lab.package_path)
    assert package.package_hash == second.package_hash
    assert package.active_snapshot_id == "evoagent-a1"
    assert package.active_revision == 1
    assert package.decision.selected_round == 1
    assert package.promotion_campaign.state.value == "completed"


def test_champion_package_contains_no_hidden_reasoning_or_secrets(tmp_path):
    lab = BenchmarkGatedChampionLab(
        tmp_path / "champion-lab",
        source_commit="c" * 40,
    )
    result = lab.run()
    text = open(result.package_path, encoding="utf-8").read().lower()

    for forbidden in (
        "chain_of_thought",
        "hidden_reasoning",
        "reasoning_content",
        "scratchpad",
        "private key",
        "api_key=",
        "password=",
        "exception_traceback",
        "exception_message",
    ):
        assert forbidden not in text
