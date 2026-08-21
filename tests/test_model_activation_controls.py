from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evoagent.benchmarks.models import ResourceBudget
from evoagent.campaigns import (
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
    ModelVersionStatus,
    SQLiteModelRegistry,
    StaleModelRevision,
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


FAMILY_ID = "activation-control-family-v1"
TRAINER_ID = "external-activation-trainer"
EVALUATOR_ID = "independent-activation-evaluator"
DECISION_ACTOR_ID = "independent-activation-policy"
APPROVERS = (
    "independent-activation-approver-a",
    "independent-activation-approver-b",
)
INITIAL_CREATED_AT = datetime(
    2026,
    8,
    10,
    14,
    0,
    tzinfo=timezone.utc,
)
RECEIPT_STARTED_AT = datetime(
    2026,
    8,
    10,
    15,
    10,
    tzinfo=timezone.utc,
)
RECEIPT_COMPLETED_AT = datetime(
    2026,
    8,
    10,
    15,
    20,
    tzinfo=timezone.utc,
)
CANDIDATE_CREATED_AT = datetime(
    2026,
    8,
    10,
    15,
    21,
    tzinfo=timezone.utc,
)
DECIDED_AT = datetime(
    2026,
    8,
    10,
    15,
    30,
    tzinfo=timezone.utc,
)


@pytest.fixture(scope="module")
def training_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("model-activation-controls")
    result = GovernedModelEvolutionLab(
        root / "training-intent",
        source_commit="7" * 40,
    ).run()
    return ModelEvolutionPackageManager().load_file(result.package_path)


def _setup_lifecycle(
    tmp_path,
    package,
    *,
    profile: SyntheticCandidateProfile = SyntheticCandidateProfile.PASSING,
):
    candidate_id = f"synthetic/activation-{profile.value}-v1"
    authorization = build_training_authorization_reference(
        reference_id=f"activation-authorization-{profile.value}-v1",
        signer_identity="external-activation-authority",
        external_verification_uri=(
            f"synthetic://authorization/activation-{profile.value}-v1"
        ),
        authorization_payload={
            "candidate_id": candidate_id,
            "training_intent_package_hash": package.package_hash,
            "synthetic_fixture": True,
        },
    )
    candidate = build_external_candidate_manifest(
        package,
        family_id=FAMILY_ID,
        candidate_id=candidate_id,
        version="1.1.0",
        authorization=authorization,
        generated_by=TRAINER_ID,
        training_commit="8" * 40,
        created_at=CANDIDATE_CREATED_AT,
        synthetic_profile=profile,
    )
    receipt = build_external_training_receipt(
        package,
        candidate,
        receipt_id=f"activation-receipt-{profile.value}-v1",
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
    suite = build_model_evaluation_suite(
        package,
        suite_id=f"activation-suite-{profile.value}-v1",
    )
    registry = SQLiteModelRegistry(tmp_path / "models.db")
    initial = build_initial_model_manifest(
        package,
        family_id=FAMILY_ID,
        version="1.0.0",
        created_at=INITIAL_CREATED_AT,
    )
    registry.register_initial(
        initial,
        reason="Register activation-control base model.",
        actor_id="activation-control-bootstrap",
    )
    ModelCandidateAdmissionService(
        registry=registry,
        authorization_verifier=AllowlistedTrainingAuthorizationVerifier(
            {authorization.reference_hash}
        ),
        allow_synthetic_fixture=True,
    ).admit(
        package=package,
        candidate=candidate,
        receipt=receipt,
    )
    campaigns = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    lifecycle = ModelActivationLifecycleService(
        registry=registry,
        campaign_governance=CampaignGovernanceService(campaigns),
        evaluator=IndependentModelCandidateEvaluator(
            tmp_path / "evaluation"
        ),
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
    return {
        "initial": initial,
        "candidate": candidate,
        "suite": suite,
        "registry": registry,
        "campaigns": campaigns,
        "lifecycle": lifecycle,
        "submission": submission,
    }


def _approve_all(setup):
    campaign = setup["campaigns"].get(
        setup["submission"].campaign.campaign_id
    )
    for approver in APPROVERS:
        campaign = setup["lifecycle"].approve(
            campaign.campaign_id,
            actor_id=approver,
            reason="Approve exact frozen candidate evidence.",
            expected_revision=campaign.revision,
        )
    return setup["campaigns"].get(campaign.campaign_id)


def test_model_activation_rejects_trainer_evaluator_and_decision_actor_approvals(
    tmp_path,
    training_package,
):
    setup = _setup_lifecycle(tmp_path, training_package)
    campaign = setup["submission"].campaign
    assert campaign.state == CampaignState.APPROVAL_PENDING

    for actor_id in (TRAINER_ID, EVALUATOR_ID, DECISION_ACTOR_ID):
        with pytest.raises(ValueError, match="cannot approve activation"):
            setup["lifecycle"].approve(
                campaign.campaign_id,
                actor_id=actor_id,
                reason="Attempt prohibited self-review.",
                expected_revision=campaign.revision,
            )

    assert setup["campaigns"].approvals(campaign.campaign_id) == []
    assert (
        setup["campaigns"].get(campaign.campaign_id).state
        == CampaignState.APPROVAL_PENDING
    )


def test_authorized_campaign_does_not_activate_and_stale_revision_fails(
    tmp_path,
    training_package,
):
    setup = _setup_lifecycle(tmp_path, training_package)
    campaign = _approve_all(setup)
    registry = setup["registry"]
    candidate = setup["candidate"]
    initial = setup["initial"]

    assert campaign.state == CampaignState.AUTHORIZED
    assert (
        registry.get(FAMILY_ID, candidate.candidate_id).status
        == ModelVersionStatus.EVALUATED
    )
    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert registry.active_revision(FAMILY_ID) == 0

    authorized = setup["lifecycle"].synchronize_authorization(
        family_id=FAMILY_ID,
        candidate_id=candidate.candidate_id,
        campaign_id=campaign.campaign_id,
        actor_id="activation-control-operator",
    )
    assert authorized.status == ModelVersionStatus.AUTHORIZED
    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert registry.active_revision(FAMILY_ID) == 0

    with pytest.raises(StaleModelRevision):
        setup["lifecycle"].activate(
            family_id=FAMILY_ID,
            candidate_id=candidate.candidate_id,
            campaign_id=campaign.campaign_id,
            expected_active_revision=1,
            actor_id="activation-control-operator",
        )

    assert registry.active(FAMILY_ID).model_id == initial.model_id
    active = setup["lifecycle"].activate(
        family_id=FAMILY_ID,
        candidate_id=candidate.candidate_id,
        campaign_id=campaign.campaign_id,
        expected_active_revision=0,
        actor_id="activation-control-operator",
    )
    assert active.status == ModelVersionStatus.ACTIVE
    assert registry.active(FAMILY_ID).model_id == candidate.candidate_id
    assert registry.active_revision(FAMILY_ID) == 1
    assert (
        setup["campaigns"].get(campaign.campaign_id).state
        == CampaignState.COMPLETED
    )

    with pytest.raises(StaleModelRevision):
        setup["lifecycle"].rollback(
            family_id=FAMILY_ID,
            from_model_id=candidate.candidate_id,
            to_model_id=initial.model_id,
            expected_active_revision=0,
            actor_id="activation-control-operator",
            reason="Reject stale rollback revision.",
        )

    restored = setup["lifecycle"].rollback(
        family_id=FAMILY_ID,
        from_model_id=candidate.candidate_id,
        to_model_id=initial.model_id,
        expected_active_revision=1,
        actor_id="activation-control-operator",
        reason="Restore exact active parent.",
    )
    assert restored.status == ModelVersionStatus.ACTIVE
    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert registry.active_revision(FAMILY_ID) == 2


def test_forged_activation_campaign_payload_cannot_change_active_pointer(
    tmp_path,
    training_package,
):
    setup = _setup_lifecycle(tmp_path, training_package)
    campaign = _approve_all(setup)
    registry = setup["registry"]
    candidate = setup["candidate"]
    initial = setup["initial"]
    setup["lifecycle"].synchronize_authorization(
        family_id=FAMILY_ID,
        candidate_id=candidate.candidate_id,
        campaign_id=campaign.campaign_id,
        actor_id="activation-control-operator",
    )

    payload = dict(campaign.artifact_payload or {})
    payload["training_package_hash"] = "f" * 64
    forged = campaign.model_copy(
        deep=True,
        update={"artifact_payload": payload},
    )

    with pytest.raises(
        ValueError,
        match="differs from the registry candidate",
    ):
        registry.activate(
            FAMILY_ID,
            candidate.candidate_id,
            forged,
            expected_active_revision=0,
            actor_id="forged-activation-attempt",
            reason="Attempt activation with forged Campaign evidence.",
        )

    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert registry.active_revision(FAMILY_ID) == 0
    assert (
        registry.get(FAMILY_ID, candidate.candidate_id).status
        == ModelVersionStatus.AUTHORIZED
    )


def test_rejected_candidate_cannot_be_authorized_or_activated(
    tmp_path,
    training_package,
):
    setup = _setup_lifecycle(
        tmp_path,
        training_package,
        profile=SyntheticCandidateProfile.REGRESSING,
    )
    submission = setup["submission"]
    registry = setup["registry"]
    candidate = setup["candidate"]
    initial = setup["initial"]

    assert submission.decision.activate is False
    assert submission.campaign.state == CampaignState.REJECTED
    assert (
        registry.get(FAMILY_ID, candidate.candidate_id).status
        == ModelVersionStatus.REJECTED
    )

    with pytest.raises(ValueError, match="not AUTHORIZED"):
        setup["lifecycle"].synchronize_authorization(
            family_id=FAMILY_ID,
            candidate_id=candidate.candidate_id,
            campaign_id=submission.campaign.campaign_id,
            actor_id="activation-control-operator",
        )
    with pytest.raises(ValueError, match="not authorized or completed"):
        setup["lifecycle"].activate(
            family_id=FAMILY_ID,
            candidate_id=candidate.candidate_id,
            campaign_id=submission.campaign.campaign_id,
            expected_active_revision=0,
            actor_id="activation-control-operator",
        )

    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert registry.active_revision(FAMILY_ID) == 0


def test_matching_evaluation_retry_reuses_exact_campaign_without_new_events(
    tmp_path,
    training_package,
):
    setup = _setup_lifecycle(tmp_path, training_package)
    campaign_checkpoint = setup["campaigns"].checkpoint()
    model_checkpoint = setup["registry"].checkpoint()
    submission = setup["submission"]

    repeated = setup["lifecycle"].evaluate_and_submit(
        family_id=FAMILY_ID,
        candidate_id=setup["candidate"].candidate_id,
        adapter=SyntheticModelCandidateAdapter(setup["candidate"]),
        suite=setup["suite"],
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

    assert repeated.reused is True
    assert repeated.report == submission.report
    assert repeated.decision == submission.decision
    assert repeated.campaign == submission.campaign
    assert setup["campaigns"].checkpoint() == campaign_checkpoint
    assert setup["registry"].checkpoint() == model_checkpoint
