from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab


def test_local_agentic_rl_lab_trains_selects_and_resumes_read_only(tmp_path):
    lab = LocalAgenticRLTrainingLab(
        tmp_path / "local-rl-lab",
        source_commit="a" * 40,
    )
    first = lab.run()
    second = lab.run()

    assert first.resumed is False
    assert first.optimizer_invoked is True
    assert second.resumed is True
    assert second.optimizer_invoked is False
    assert first.package_hash == second.package_hash
    assert first.manifest_hash == second.manifest_hash
    assert first.training_result_hash == second.training_result_hash
    assert first.initial_checkpoint_hash == second.initial_checkpoint_hash
    assert first.final_checkpoint_hash == second.final_checkpoint_hash
    assert first.selected_checkpoint_hash == second.selected_checkpoint_hash
    assert first.parameter_delta_l2 > 0.0
    assert first.baseline_score == 0.0
    assert first.selected_score == 1.0
    assert first.selected_normal_score == 1.0
    assert first.selected_protected_score == 1.0
    assert first.baseline_unsafe_actions > 0
    assert first.selected_unsafe_actions == 0
    assert first.iterations == 24
    assert first.rollouts == 2_304
    assert first.parameter_updates == 96
    assert first.retained_checkpoints == 12
    assert first.audit_event_count == 4
    assert first.numeric_policy_parameters_updated is True
    assert first.tiny_tabular_policy_only is True
    assert first.local_rollout_training_executed_by_evoagent is True
    assert first.foundation_model_training_performed is False
    assert first.external_model_call_performed_by_evoagent is False
    assert first.gpu_execution_performed is False
    assert first.network_execution_performed is False
    assert first.production_deployment_performed is False
    assert first.official_benchmark_claimed is False
