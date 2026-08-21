from __future__ import annotations

import json

from evoagent.campaigns import (
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
)
from evoagent.lab import ModelCandidateAdmissionLab
from evoagent.model_registry import (
    ModelAdmissionPackageManager,
    ModelEventType,
    ModelVersionStatus,
    SQLiteModelRegistry,
)


def test_complete_model_candidate_admission_activation_and_rollback(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="a" * 40,
    )

    first = lab.run()

    assert first.resumed is False
    assert first.lifecycle_statuses == (
        ModelVersionStatus.CANDIDATE.value,
        ModelVersionStatus.EVALUATED.value,
        ModelVersionStatus.AUTHORIZED.value,
        ModelVersionStatus.ACTIVE.value,
        ModelVersionStatus.ROLLED_BACK.value,
    )
    assert first.activation_campaign_state == CampaignState.COMPLETED.value
    assert first.required_approvals == 2
    assert first.approval_count == 2
    assert first.held_out_base_score == 0.0
    assert first.held_out_candidate_score == 1.0
    assert first.held_out_improvement == 1.0
    assert first.replay_candidate_score == 1.0
    assert first.retention_candidate_score == 1.0
    assert first.safety_candidate_score == 1.0
    assert first.regression_count == 0
    assert first.forgetting_rate == 0.0
    assert first.safety_violation_count == 0
    assert first.candidate_tool_calls > 0
    assert first.candidate_budget_ok is True
    assert first.active_model_after_activation == first.candidate_id
    assert first.active_revision_after_activation == 1
    assert first.active_model_after_rollback == first.initial_model_id
    assert first.active_revision_after_rollback == 2
    assert first.model_version_count == 2
    assert first.model_event_count == 6
    assert first.activation_campaign_count == 1
    assert first.campaign_event_count == 7
    assert first.restart_verified is True
    assert first.synthetic_fixture is True
    assert first.checkpoint_downloaded is False
    assert first.candidate_weights_loaded is False
    assert first.training_executed_by_evoagent is False
    assert first.external_execution_performed is False

    package = ModelAdmissionPackageManager().load_file(first.package_path)
    assert package.package_hash == first.package_hash
    assert package.training_receipt.external_training_attested is False
    assert package.training_executed_by_evoagent is False
    assert package.external_execution_performed is False
    assert package.candidate_weights_loaded is False
    assert package.checkpoint_downloaded is False

    registry = SQLiteModelRegistry(lab.model_database)
    versions = registry.list_versions(lab.FAMILY_ID)
    by_id = {item.model_id: item for item in versions}
    assert by_id[first.initial_model_id].status == ModelVersionStatus.ACTIVE
    assert (
        by_id[first.candidate_id].status
        == ModelVersionStatus.ROLLED_BACK
    )
    assert tuple(
        event.event_type for event in registry.events(lab.FAMILY_ID)
    ) == (
        ModelEventType.REGISTERED,
        ModelEventType.CANDIDATE_ADMITTED,
        ModelEventType.EVALUATED,
        ModelEventType.AUTHORIZED,
        ModelEventType.ACTIVATED,
        ModelEventType.ROLLED_BACK,
    )

    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    campaign = campaigns.get(first.activation_campaign_id)
    assert campaign.campaign_type == CampaignType.MODEL_ACTIVATION
    assert campaign.state == CampaignState.COMPLETED
    assert campaign.generated_by == lab.TRAINER_ID
    assert len(campaigns.approvals(campaign.campaign_id)) == 2
    assert campaigns.verify_audit(package.campaign_checkpoint) is True
    assert registry.verify_audit(package.model_registry_checkpoint) is True


def test_second_model_candidate_admission_run_is_read_only(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="b" * 40,
    )
    first = lab.run()

    registry_before = SQLiteModelRegistry(lab.model_database)
    campaign_before = SQLiteCampaignRepository(lab.campaign_database)
    model_checkpoint_before = registry_before.checkpoint()
    campaign_checkpoint_before = campaign_before.checkpoint()
    versions_before = tuple(registry_before.list_versions(lab.FAMILY_ID))
    model_events_before = tuple(registry_before.events(lab.FAMILY_ID))
    campaign_events_before = tuple(campaign_before.audit_events())
    approvals_before = tuple(
        campaign_before.approvals(first.activation_campaign_id)
    )
    package_before = ModelAdmissionPackageManager().load_file(
        first.package_path
    )

    second = lab.run()

    assert second.resumed is True
    assert second.package_hash == first.package_hash
    assert second.activation_campaign_id == first.activation_campaign_id
    assert second.lifecycle_statuses == first.lifecycle_statuses
    assert second.active_model_after_rollback == first.initial_model_id
    assert second.active_revision_after_rollback == 2

    registry_after = SQLiteModelRegistry(lab.model_database)
    campaign_after = SQLiteCampaignRepository(lab.campaign_database)
    assert registry_after.checkpoint() == model_checkpoint_before
    assert campaign_after.checkpoint() == campaign_checkpoint_before
    assert tuple(registry_after.list_versions(lab.FAMILY_ID)) == versions_before
    assert tuple(registry_after.events(lab.FAMILY_ID)) == model_events_before
    assert tuple(campaign_after.audit_events()) == campaign_events_before
    assert (
        tuple(campaign_after.approvals(first.activation_campaign_id))
        == approvals_before
    )
    assert (
        ModelAdmissionPackageManager().load_file(second.package_path)
        == package_before
    )


def test_model_admission_package_contains_no_hidden_reasoning_or_secrets(
    tmp_path,
):
    result = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="c" * 40,
    ).run()
    text = open(result.package_path, encoding="utf-8").read().lower()

    for forbidden in (
        "chain_of_thought",
        "scratchpad",
        "hidden_reasoning",
        "reasoning_content",
        "traceback",
        "stack_trace",
        "sk-",
        "ghp_",
        "hf_",
        "akia",
        "private key",
    ):
        assert forbidden not in text

    parsed = json.loads(text)
    assert parsed["checkpoint_downloaded"] is False
    assert parsed["candidate_weights_loaded"] is False
    assert parsed["training_executed_by_evoagent"] is False
    assert parsed["external_execution_performed"] is False
    assert parsed["training_receipt"]["external_training_attested"] is False
