from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evoagent.benchmarks.models import ResourceBudget
from evoagent.campaigns import (
    ApprovalDecision,
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab import GovernedModelEvolutionLab
from evoagent.model_registry import (
    AllowlistedTrainingAuthorizationVerifier,
    IndependentModelCandidateEvaluator,
    ModelActivationLifecycleService,
    ModelActivationThresholds,
    ModelCandidateAdmissionService,
    ModelCandidateValidationError,
    ModelVersionStatus,
    SQLiteModelRegistry,
    SyntheticCandidateProfile,
    SyntheticModelCandidateAdapter,
    TrainingReceiptKind,
    build_external_candidate_manifest,
    build_external_training_receipt,
    build_initial_model_manifest,
    build_model_evaluation_suite,
    build_training_authorization_reference,
)
from evoagent.training import (
    ModelEvolutionPackageManager,
    TrainingBudget,
)


FAMILY_ID = "authorization-hardening-family-v1"
CANDIDATE_ID = "synthetic/authorization-hardening-candidate-v1"
TRAINER_ID = "external-authorization-hardening-trainer"
EVALUATOR_ID = "independent-authorization-hardening-evaluator"
DECISION_ACTOR_ID = "independent-authorization-hardening-policy"
INITIAL_CREATED_AT = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
RECEIPT_STARTED_AT = datetime(2026, 8, 10, 15, 10, tzinfo=timezone.utc)
RECEIPT_COMPLETED_AT = datetime(2026, 8, 10, 15, 20, tzinfo=timezone.utc)
CANDIDATE_CREATED_AT = datetime(2026, 8, 10, 15, 21, tzinfo=timezone.utc)
DECIDED_AT = datetime(2026, 8, 10, 15, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def training_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("authorization-hardening")
    result = GovernedModelEvolutionLab(
        root / "training-intent",
        source_commit="a" * 40,
    ).run()
    return ModelEvolutionPackageManager().load_file(result.package_path)


def _candidate_and_receipt(package, authorization):
    candidate = build_external_candidate_manifest(
        package,
        family_id=FAMILY_ID,
        candidate_id=CANDIDATE_ID,
        version="1.1.0",
        authorization=authorization,
        generated_by=TRAINER_ID,
        training_commit="b" * 40,
        created_at=CANDIDATE_CREATED_AT,
        synthetic_profile=SyntheticCandidateProfile.PASSING,
    )
    receipt = build_external_training_receipt(
        package,
        candidate,
        receipt_id="authorization-hardening-receipt-v1",
        trainer_id=TRAINER_ID,
        started_at=RECEIPT_STARTED_AT,
        completed_at=RECEIPT_COMPLETED_AT,
        budget_used=TrainingBudget(
            max_gpu_hours=0.0,
            max_rollouts=32,
            max_training_tokens=0,
            max_cost_usd=0.0,
        ),
        receipt_kind=TrainingReceiptKind.SYNTHETIC_LIFECYCLE_FIXTURE,
    )
    return candidate, receipt


def _registry(tmp_path, package):
    registry = SQLiteModelRegistry(tmp_path / "models.db")
    initial = build_initial_model_manifest(
        package,
        family_id=FAMILY_ID,
        version="1.0.0",
        created_at=INITIAL_CREATED_AT,
    )
    registry.register_initial(
        initial,
        reason="Register authorization-hardening base model.",
        actor_id="authorization-hardening-bootstrap",
    )
    return registry, initial


def test_allowlisted_reference_for_another_candidate_is_rejected(
    tmp_path,
    training_package,
):
    authorization = build_training_authorization_reference(
        reference_id="wrong-candidate-authorization-v1",
        signer_identity="external-authorization-authority",
        external_verification_uri=(
            "synthetic://authorization/wrong-candidate-v1"
        ),
        authorization_payload={
            "candidate_id": "synthetic/different-candidate-v1",
            "training_intent_package_hash": training_package.package_hash,
        },
    )
    candidate, receipt = _candidate_and_receipt(
        training_package,
        authorization,
    )
    registry, initial = _registry(tmp_path, training_package)

    with pytest.raises(
        ModelCandidateValidationError,
        match="authorization scope differs",
    ):
        ModelCandidateAdmissionService(
            registry=registry,
            authorization_verifier=(
                AllowlistedTrainingAuthorizationVerifier(
                    {authorization.reference_hash}
                )
            ),
            allow_synthetic_fixture=True,
        ).admit(
            package=training_package,
            candidate=candidate,
            receipt=receipt,
        )

    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert len(registry.list_versions(FAMILY_ID)) == 1


def test_generic_campaign_approval_bypass_is_rejected_before_registry_authorization(
    tmp_path,
    training_package,
):
    authorization = build_training_authorization_reference(
        reference_id="exact-authorization-hardening-v1",
        signer_identity="external-authorization-authority",
        external_verification_uri=(
            "synthetic://authorization/exact-hardening-v1"
        ),
        authorization_payload={
            "candidate_id": CANDIDATE_ID,
            "training_intent_package_hash": training_package.package_hash,
        },
    )
    candidate, receipt = _candidate_and_receipt(
        training_package,
        authorization,
    )
    registry, initial = _registry(tmp_path, training_package)
    ModelCandidateAdmissionService(
        registry=registry,
        authorization_verifier=AllowlistedTrainingAuthorizationVerifier(
            {authorization.reference_hash}
        ),
        allow_synthetic_fixture=True,
    ).admit(
        package=training_package,
        candidate=candidate,
        receipt=receipt,
    )

    campaign_repository = SQLiteCampaignRepository(
        tmp_path / "campaigns.db"
    )
    generic_governance = CampaignGovernanceService(campaign_repository)
    lifecycle = ModelActivationLifecycleService(
        registry=registry,
        campaign_governance=generic_governance,
        evaluator=IndependentModelCandidateEvaluator(
            tmp_path / "evaluation"
        ),
    )
    suite = build_model_evaluation_suite(
        training_package,
        suite_id="authorization-hardening-suite-v1",
    )
    submission = lifecycle.evaluate_and_submit(
        family_id=FAMILY_ID,
        candidate_id=candidate.candidate_id,
        adapter=SyntheticModelCandidateAdapter(candidate),
        suite=suite,
        evaluator_id=EVALUATOR_ID,
        budget=ResourceBudget(
            max_task_trials=6,
            max_tokens=0,
            max_tool_calls=50,
            max_wall_seconds=0.0,
            max_cost_usd=0.0,
        ),
        thresholds=ModelActivationThresholds(),
        decision_actor_id=DECISION_ACTOR_ID,
        decided_at=DECIDED_AT,
    )
    campaign = submission.campaign
    assert campaign.state == CampaignState.APPROVAL_PENDING

    campaign = generic_governance.approve(
        campaign.campaign_id,
        actor_id=EVALUATOR_ID,
        decision=ApprovalDecision.APPROVE,
        reason="Deliberately bypass model-specific approval boundary.",
        expected_revision=campaign.revision,
    )
    campaign = generic_governance.approve(
        campaign.campaign_id,
        actor_id="independent-hardening-approver",
        decision=ApprovalDecision.APPROVE,
        reason="Supply the second generic approval.",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    with pytest.raises(
        ValueError,
        match="Evaluator.*approved activation|evaluator.*approved activation",
    ):
        lifecycle.synchronize_authorization(
            family_id=FAMILY_ID,
            candidate_id=candidate.candidate_id,
            campaign_id=campaign.campaign_id,
            actor_id="authorization-hardening-operator",
        )

    assert (
        registry.get(FAMILY_ID, candidate.candidate_id).status
        == ModelVersionStatus.EVALUATED
    )
    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert registry.active_revision(FAMILY_ID) == 0
