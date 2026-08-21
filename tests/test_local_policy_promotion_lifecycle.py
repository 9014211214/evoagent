from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evoagent import __version__
from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.local_policy import (
    LocalPolicyPromotionLifecycleService,
    LocalPolicyPromotionPackageManager,
    LocalPolicyRegistryConflictError,
    LocalPolicyVersionStatus,
    SQLiteLocalPolicyRegistry,
    StaleLocalPolicyRevision,
    build_initial_local_policy_manifest,
)
from tests.test_program_local_rl_full_lineage import _full_lineage


SOURCE_REPOSITORY = "https://github.com/9014211214/evoagent"
SOURCE_COMMIT = "f" * 40
FAMILY = "local-policy-family:v2.2"
P0 = "local-policy:p0"
P1 = "local-policy:p1"


def _base(package):
    return (
        package.runtime_attested_package
        .schema_attested_package
        .attested_package
        .base_package
    )


def _promotion_context(tmp_path, monkeypatch):
    _, _, accepted, anchors, receipt = _full_lineage(
        tmp_path / "accepted-evidence",
        monkeypatch,
    )
    base = _base(accepted)
    registry = SQLiteLocalPolicyRegistry(tmp_path / "local-policy.db")
    campaign_repository = SQLiteCampaignRepository(tmp_path / "campaigns.db")
    governance = CampaignGovernanceService(campaign_repository)
    service = LocalPolicyPromotionLifecycleService(registry, governance)
    start = max(
        receipt.accepted_at + timedelta(seconds=1),
        datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    initial = build_initial_local_policy_manifest(
        family_id=FAMILY,
        policy_id=P0,
        checkpoint_hash=base.result.initial_checkpoint_hash,
        optimizer_config_hash=base.intent.optimizer_config_hash,
        source_commit=base.source_commit,
        created_by="local-policy-bootstrap-owner",
        created_at=start,
    )
    registry.register_initial(
        initial,
        actor_id=initial.created_by,
        now=start,
    )
    candidate_record = service.admit_candidate(
        accepted,
        anchors,
        receipt,
        family_id=FAMILY,
        candidate_id=P1,
        base_policy_id=P0,
        created_by="local-policy-candidate-controller",
        created_at=start + timedelta(seconds=1),
    )
    submission = service.submit_promotion(
        accepted,
        anchors,
        receipt,
        family_id=FAMILY,
        candidate_id=P1,
        evaluator_id="independent-local-policy-promotion-evaluator",
        evaluated_at=start + timedelta(seconds=2),
        decision_actor_id="independent-local-policy-promotion-decider",
        decided_at=start + timedelta(seconds=3),
    )
    return {
        "accepted": accepted,
        "anchors": anchors,
        "receipt": receipt,
        "registry": registry,
        "campaign_repository": campaign_repository,
        "governance": governance,
        "service": service,
        "initial": initial,
        "candidate_record": candidate_record,
        "submission": submission,
    }


def _authorize_promotion(context):
    service = context["service"]
    campaign = context["submission"].campaign
    campaign = service.approve_promotion(
        campaign.campaign_id,
        actor_id="independent-local-policy-promotion-reviewer-a",
        reason="Candidate evidence and authority boundary passed review.",
        expected_revision=campaign.revision,
    )
    campaign = service.approve_promotion(
        campaign.campaign_id,
        actor_id="independent-local-policy-promotion-reviewer-b",
        reason="Candidate safety and rollback readiness passed review.",
        expected_revision=campaign.revision,
    )
    context["promotion_campaign"] = campaign
    context["service"].synchronize_promotion_authorization(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        actor_id="independent-local-policy-promotion-authorizer",
    )
    return campaign


def _activate(context, *, direct_registry=False):
    campaign = context["promotion_campaign"]
    actor = "independent-local-policy-activation-executor"
    if direct_registry:
        record = context["registry"].activate(
            FAMILY,
            P1,
            campaign,
            expected_active_revision=0,
            actor_id=actor,
        )
    else:
        record = context["service"].activate(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            expected_active_revision=0,
            actor_id=actor,
        )
    context["activated_record"] = record
    return record


def _authorize_rollback(context):
    service = context["service"]
    activated_at = context["registry"].get(FAMILY, P1).activated_at
    requested_at = activated_at
    evaluated_at = max(
        datetime.now(timezone.utc),
        activated_at + timedelta(milliseconds=1),
    )
    submission = service.submit_rollback(
        family_id=FAMILY,
        candidate_id=P1,
        evidence_hash="9" * 64,
        reason="Controlled rollback drill for the direct parent checkpoint.",
        requested_by="independent-local-policy-rollback-requester",
        requested_at=requested_at,
        evaluator_id="independent-local-policy-rollback-evaluator",
        evaluated_at=evaluated_at,
    )
    campaign = submission.campaign
    campaign = service.approve_rollback(
        campaign.campaign_id,
        actor_id="independent-local-policy-rollback-reviewer-a",
        reason="Rollback target and evidence lineage passed review.",
        expected_revision=campaign.revision,
    )
    campaign = service.approve_rollback(
        campaign.campaign_id,
        actor_id="independent-local-policy-rollback-reviewer-b",
        reason="Rollback pointer transition and audit plan passed review.",
        expected_revision=campaign.revision,
    )
    service.synchronize_rollback_authorization(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        actor_id="independent-local-policy-rollback-authorizer",
    )
    context["rollback_submission"] = submission
    context["rollback_campaign"] = campaign
    return campaign


def _build_full_package(context):
    return LocalPolicyPromotionPackageManager().build(
        package_id="local-policy-promotion-package:v2.2",
        created_at=max(
            datetime.now(timezone.utc),
            *(item.created_at for item in context["registry"].events()),
            *(
                item.created_at
                for item in context["campaign_repository"].audit_events()
            ),
        ) + timedelta(milliseconds=1),
        framework_version=__version__,
        source_repository=SOURCE_REPOSITORY,
        source_commit=SOURCE_COMMIT,
        third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
        accepted_program_package=context["accepted"],
        trusted_anchors=context["anchors"],
        acceptance_receipt=context["receipt"],
        registry=context["registry"],
        campaigns=context["campaign_repository"],
        family_id=FAMILY,
        candidate_id=P1,
    )


def test_complete_local_policy_promotion_and_rollback_is_reproducible(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)

    assert context["registry"].active(FAMILY).policy_id == P0
    assert context["registry"].get(FAMILY, P1).status == (
        LocalPolicyVersionStatus.AUTHORIZED
    )

    _activate(context)
    assert context["registry"].active(FAMILY).policy_id == P1
    assert context["registry"].head(FAMILY).revision == 1
    assert context["campaign_repository"].get(
        context["promotion_campaign"].campaign_id
    ).state == CampaignState.COMPLETED

    rollback_campaign = _authorize_rollback(context)
    target = context["service"].rollback(
        family_id=FAMILY,
        from_policy_id=P1,
        to_policy_id=P0,
        campaign_id=rollback_campaign.campaign_id,
        expected_active_revision=1,
        actor_id="independent-local-policy-rollback-executor",
    )

    assert target.policy_id == P0
    assert context["registry"].active(FAMILY).policy_id == P0
    assert context["registry"].head(FAMILY).revision == 2
    assert context["registry"].get(FAMILY, P1).status == (
        LocalPolicyVersionStatus.ROLLED_BACK
    )
    assert context["campaign_repository"].get(
        rollback_campaign.campaign_id
    ).state == CampaignState.COMPLETED
    assert context["registry"].verify_state(FAMILY) is True

    package = _build_full_package(context)
    manager = LocalPolicyPromotionPackageManager()
    assert manager.verify(package) is True
    assert package.local_policy_pointer_mutation_only is True
    assert package.foundation_model_weights_updated is False
    assert package.production_activation_performed is False
    assert package.production_deployment_performed is False
    path = manager.export_file(package, tmp_path / "promotion-package.json")
    assert manager.load_file(path) == package


def test_authorization_does_not_mutate_active_pointer(tmp_path, monkeypatch):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)

    assert context["registry"].active(FAMILY).policy_id == P0
    assert context["registry"].head(FAMILY).revision == 0
    assert context["registry"].get(FAMILY, P1).status == (
        LocalPolicyVersionStatus.AUTHORIZED
    )


def test_generic_campaign_approval_cannot_bypass_domain_roles(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    campaign = context["submission"].campaign
    evaluator = context["submission"].report.evaluator_id

    campaign = context["governance"].approve(
        campaign.campaign_id,
        actor_id=evaluator,
        reason="Generic approval intentionally bypasses the domain service.",
        expected_revision=campaign.revision,
    )
    campaign = context["governance"].approve(
        campaign.campaign_id,
        actor_id="otherwise-independent-reviewer",
        reason="Second generic approval.",
        expected_revision=campaign.revision,
    )
    assert campaign.state == CampaignState.AUTHORIZED

    with pytest.raises(ValueError, match="role separation"):
        context["service"].synchronize_promotion_authorization(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            actor_id="independent-promotion-sync",
        )
    assert context["registry"].active(FAMILY).policy_id == P0


def test_stale_active_revision_rejects_activation(tmp_path, monkeypatch):
    context = _promotion_context(tmp_path, monkeypatch)
    campaign = _authorize_promotion(context)

    with pytest.raises(StaleLocalPolicyRevision):
        context["service"].activate(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            expected_active_revision=99,
            actor_id="independent-local-policy-activation-executor",
        )
    assert context["registry"].active(FAMILY).policy_id == P0


def test_activation_recovery_completes_campaign_once_then_is_read_only(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    campaign = _authorize_promotion(context)
    actor = "independent-local-policy-activation-executor"

    context["registry"].activate(
        FAMILY,
        P1,
        campaign,
        expected_active_revision=0,
        actor_id=actor,
    )
    before = context["registry"].events()
    assert context["campaign_repository"].get(campaign.campaign_id).state == (
        CampaignState.AUTHORIZED
    )

    first = context["service"].activate(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        expected_active_revision=0,
        actor_id=actor,
    )
    after_recovery = context["registry"].events()
    second = context["service"].activate(
        family_id=FAMILY,
        candidate_id=P1,
        campaign_id=campaign.campaign_id,
        expected_active_revision=0,
        actor_id=actor,
    )

    assert first == second
    assert before == after_recovery == context["registry"].events()
    assert context["campaign_repository"].get(campaign.campaign_id).state == (
        CampaignState.COMPLETED
    )

    with pytest.raises(LocalPolicyRegistryConflictError, match="another actor"):
        context["service"].activate(
            family_id=FAMILY,
            candidate_id=P1,
            campaign_id=campaign.campaign_id,
            expected_active_revision=0,
            actor_id="different-activation-actor",
        )


@pytest.mark.parametrize(
    "overlapping_role",
    ("promotion_authorizer", "activation_executor"),
)
def test_promotion_control_roles_cannot_request_rollback(
    tmp_path,
    monkeypatch,
    overlapping_role,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    record = context["registry"].get(FAMILY, P1)
    actor = (
        record.promotion_authorized_by
        if overlapping_role == "promotion_authorizer"
        else record.activated_by
    )
    evaluated_at = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="overlaps promotion"):
        context["service"].submit_rollback(
            family_id=FAMILY,
            candidate_id=P1,
            evidence_hash="9" * 64,
            reason="Invalid overlapping rollback request.",
            requested_by=actor,
            requested_at=evaluated_at - timedelta(seconds=1),
            evaluator_id="independent-rollback-evaluator",
            evaluated_at=evaluated_at,
        )


def test_rollback_recovery_completes_campaign_once_then_is_read_only(
    tmp_path,
    monkeypatch,
):
    context = _promotion_context(tmp_path, monkeypatch)
    _authorize_promotion(context)
    _activate(context)
    campaign = _authorize_rollback(context)
    actor = "independent-local-policy-rollback-executor"

    context["registry"].rollback(
        FAMILY,
        from_policy_id=P1,
        to_policy_id=P0,
        campaign=campaign,
        expected_active_revision=1,
        actor_id=actor,
    )
    before = context["registry"].events()
    assert context["campaign_repository"].get(campaign.campaign_id).state == (
        CampaignState.AUTHORIZED
    )

    first = context["service"].rollback(
        family_id=FAMILY,
        from_policy_id=P1,
        to_policy_id=P0,
        campaign_id=campaign.campaign_id,
        expected_active_revision=1,
        actor_id=actor,
    )
    after = context["registry"].events()
    second = context["service"].rollback(
        family_id=FAMILY,
        from_policy_id=P1,
        to_policy_id=P0,
        campaign_id=campaign.campaign_id,
        expected_active_revision=1,
        actor_id=actor,
    )

    assert first == second
    assert before == after == context["registry"].events()
    assert context["campaign_repository"].get(campaign.campaign_id).state == (
        CampaignState.COMPLETED
    )
