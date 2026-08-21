from __future__ import annotations

import json

from evoagent.campaigns import (
    CampaignOperatorView,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
)
from evoagent.cycles import CycleStatus
from evoagent.domain.models import FailureLayer
from evoagent.lab import GovernedModelEvolutionLab
from evoagent.training import (
    AgenticRLTaskSpec,
    ModelEvidenceDatasetManager,
    ModelEvolutionPackageManager,
    TrainingMethod,
)


def test_governed_model_lab_threshold_dataset_campaign_and_dry_run_candidate(tmp_path):
    lab = GovernedModelEvolutionLab(tmp_path / "model-lab", source_commit="a" * 40)
    result = lab.run()

    assert result.resumed is False
    assert result.cycle_statuses == (
        CycleStatus.MODEL_EVIDENCE_ACCUMULATED,
        CycleStatus.MODEL_EVIDENCE_ACCUMULATED,
        CycleStatus.MODEL_EVIDENCE_ACCUMULATED,
        CycleStatus.MODEL_CANDIDATE,
    )
    assert result.follow_up_status == CycleStatus.MODEL_CANDIDATE
    assert result.campaign_state == CampaignState.CANDIDATE_READY.value
    assert result.campaign_count == 1
    assert result.approval_count == 0
    assert result.persisted_trace_count == 5
    assert len(result.evidence_cases) == 4
    assert len(set(result.evidence_task_ids)) == 4
    assert result.follow_up_case.task_id not in result.evidence_task_ids
    assert all(
        item.attribution.root_cause_layer == FailureLayer.MODEL
        and item.supported_experiments == ("reference_model",)
        for item in (*result.evidence_cases, result.follow_up_case)
    )
    assert result.held_out_baseline_passed == (False, False)
    assert result.held_out_reference_passed == (True, True)
    assert set(result.evidence_task_ids).isdisjoint(
        task.task_id for task in result.held_out_tasks
    )

    assert result.supervised_example_count == 4
    assert result.preference_pair_count == 4
    assert result.replay_seed_count == 4
    assert result.dataset_signals.gold_trajectories == 4
    assert result.dataset_signals.preference_pairs == 4
    assert result.dataset_signals.replayable_environment is True
    assert result.dataset_signals.resettable_environment is True
    assert result.dataset_signals.machine_verifier is True

    ticket = result.model_ticket
    candidate = result.model_candidate
    assert ticket.evidence_manifest_hash == result.dataset_manifest_hash
    assert ticket.evidence_trace_ids == tuple(
        item.baseline_trace_id for item in result.evidence_cases
    )
    assert ticket.held_out_task_ids == tuple(
        task.task_id for task in result.held_out_tasks
    )
    assert ticket.budget.max_rollouts == 64
    assert ticket.budget.max_gpu_hours == 0
    assert ticket.budget.max_cost_usd == 0
    assert candidate.method == TrainingMethod.AGENTIC_RL
    assert candidate.evidence_manifest_hash == result.dataset_manifest_hash
    assert candidate.held_out_task_ids == ticket.held_out_task_ids
    assert candidate.training_executed is False
    assert isinstance(candidate.task_spec, AgenticRLTaskSpec)
    assert candidate.task_spec.algorithm.value == "grpo"
    assert candidate.task_spec.rollout_budget == 64
    assert candidate.task_spec.execution_enabled is False
    assert candidate.task_spec.runtime_config["publish_artifacts"] is False
    assert candidate.task_spec.runtime_config["deploy_candidate"] is False

    repository = SQLiteCampaignRepository(lab.campaign_database)
    campaigns = CampaignOperatorView(repository).list_campaigns(
        campaign_type=CampaignType.MODEL
    )
    assert len(campaigns) == 1
    assert campaigns[0].campaign_id == result.campaign_id
    assert campaigns[0].required_approvals == 2
    assert repository.approvals(result.campaign_id) == []
    persistent_evidence = repository.get_model_evidence(
        base_model_id=lab.BASE_MODEL_ID,
        problem_cluster=lab.PROBLEM_CLUSTER,
        minimum_traces=4,
        minimum_distinct_tasks=4,
    )
    assert persistent_evidence.ready is True
    assert len(persistent_evidence.trace_ids) == 5
    assert len(persistent_evidence.task_ids) == 5

    dataset = ModelEvidenceDatasetManager().load_file(result.dataset_path)
    package = ModelEvolutionPackageManager().load_file(result.package_path)
    assert dataset.manifest_hash == result.dataset_manifest_hash
    assert package.package_hash == result.package_hash
    assert package.campaign.campaign_id == result.campaign_id
    assert package.dataset == dataset
    assert package.ticket == ticket
    assert package.candidate == candidate
    assert package.held_out_tasks == result.held_out_tasks
    assert package.training_executed is False
    assert package.external_execution_performed is False
    assert result.restart_verified is True
    assert result.training_executed is False
    assert result.external_execution_performed is False


def test_second_model_lab_run_is_read_only_and_idempotent(tmp_path):
    lab = GovernedModelEvolutionLab(tmp_path / "model-lab", source_commit="b" * 40)
    first = lab.run()

    repository = SQLiteCampaignRepository(lab.campaign_database)
    audit_before = repository.checkpoint()
    trace_before = first.trace_checkpoint
    package_before = ModelEvolutionPackageManager().load_file(first.package_path)

    second = lab.run()

    assert second.resumed is True
    assert second.campaign_id == first.campaign_id
    assert second.model_ticket == first.model_ticket
    assert second.model_candidate == first.model_candidate
    assert second.dataset_manifest_hash == first.dataset_manifest_hash
    assert second.package_hash == first.package_hash
    assert second.campaign_checkpoint == first.campaign_checkpoint
    assert second.trace_checkpoint == trace_before
    assert second.persisted_trace_count == 5
    assert second.campaign_count == 1
    assert second.approval_count == 0
    assert SQLiteCampaignRepository(lab.campaign_database).checkpoint() == audit_before
    assert ModelEvolutionPackageManager().load_file(second.package_path) == package_before


def test_model_evolution_evidence_and_package_contain_no_hidden_reasoning_or_secrets(tmp_path):
    result = GovernedModelEvolutionLab(
        tmp_path / "model-lab",
        source_commit="c" * 40,
    ).run()
    dataset_text = open(result.dataset_path, encoding="utf-8").read().lower()
    package_text = open(result.package_path, encoding="utf-8").read().lower()
    combined = dataset_text + package_text

    for forbidden in (
        "chain_of_thought",
        "scratchpad",
        "hidden_reasoning",
        "traceback",
        "sk-",
        "ghp_",
        "hf_",
    ):
        assert forbidden not in combined
    parsed = json.loads(package_text)
    assert parsed["training_executed"] is False
    assert parsed["external_execution_performed"] is False
