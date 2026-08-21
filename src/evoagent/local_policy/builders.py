from __future__ import annotations

from datetime import datetime

from evoagent.model_registry.models import canonical_sha256
from evoagent.program_rl import (
    FullyAttestedProgramLocalRLBindingPackage,
    ProgramLocalRLAcceptanceManager,
    ProgramLocalRLAcceptanceReceipt,
    ProgramLocalRLTrustedAnchors,
)

from .models import (
    InitialLocalPolicyManifest,
    LocalPolicyCandidateManifest,
    LocalPolicyPromotionDecision,
    LocalPolicyPromotionReport,
    LocalPolicyRollbackReport,
    LocalPolicyRollbackRequest,
    LocalPolicyVersionRecord,
)


def _base_package(package: FullyAttestedProgramLocalRLBindingPackage):
    return (
        package.runtime_attested_package
        .schema_attested_package
        .attested_package
        .base_package
    )


def accepted_evidence_actor_ids(
    package: FullyAttestedProgramLocalRLBindingPackage,
    anchors: ProgramLocalRLTrustedAnchors,
    receipt: ProgramLocalRLAcceptanceReceipt,
) -> tuple[str, ...]:
    runtime_package = package.runtime_attested_package
    runtime = runtime_package.runtime_attestation
    schema = runtime.schema_attestation
    attested = runtime_package.schema_attested_package.attested_package
    base = attested.base_package
    running = package.running_attested_package.intent_binding
    return tuple(
        sorted(
            {
                *base.intent.governed_actor_ids,
                base.intent.created_by,
                base.authorization.authorized_by,
                base.result.executed_by,
                runtime.runtime_contract.reviewed_by,
                runtime.runtime_receipt.verified_by,
                schema.projection_spec.created_by,
                attested.attested_result.bound_by,
                runtime_package.accepted_by,
                running.running_attestor_id,
                running.bound_by,
                package.accepted_by,
                anchors.anchored_by,
                receipt.accepted_by,
            }
        )
    )


def verify_accepted_program_local_rl_evidence(
    package: FullyAttestedProgramLocalRLBindingPackage,
    anchors: ProgramLocalRLTrustedAnchors,
    receipt: ProgramLocalRLAcceptanceReceipt,
) -> bool:
    return ProgramLocalRLAcceptanceManager.verify(package, anchors, receipt)


def build_initial_local_policy_manifest(
    *,
    family_id: str,
    policy_id: str,
    checkpoint_hash: str,
    optimizer_config_hash: str,
    source_commit: str,
    created_by: str,
    created_at: datetime,
) -> InitialLocalPolicyManifest:
    payload = {
        "kind": "initial",
        "family_id": family_id,
        "policy_id": policy_id,
        "checkpoint_hash": checkpoint_hash,
        "optimizer_config_hash": optimizer_config_hash,
        "source_commit": source_commit,
        "created_by": created_by,
        "created_at": created_at,
        "tiny_local_agent_policy": True,
        "foundation_model_checkpoint": False,
        "production_activation_authorized": False,
        "production_deployment_authorized": False,
    }
    return InitialLocalPolicyManifest(
        **payload,
        manifest_hash=canonical_sha256(payload),
    )


def build_candidate_from_accepted_evidence(
    package: FullyAttestedProgramLocalRLBindingPackage,
    anchors: ProgramLocalRLTrustedAnchors,
    receipt: ProgramLocalRLAcceptanceReceipt,
    *,
    family_id: str,
    candidate_id: str,
    base_policy_id: str,
    created_by: str,
    created_at: datetime,
) -> LocalPolicyCandidateManifest:
    verify_accepted_program_local_rl_evidence(package, anchors, receipt)
    if created_at < receipt.accepted_at:
        raise ValueError(
            "Local-policy candidate predates final accepted optimizer evidence."
        )
    base = _base_package(package)
    actors = accepted_evidence_actor_ids(package, anchors, receipt)
    if created_by in set(actors):
        raise ValueError(
            "Local-policy candidate creator overlaps accepted evidence roles."
        )
    result = base.result
    intent = base.intent
    payload = {
        "kind": "candidate",
        "family_id": family_id,
        "candidate_id": candidate_id,
        "base_policy_id": base_policy_id,
        "base_checkpoint_hash": result.initial_checkpoint_hash,
        "selected_checkpoint_hash": result.selected_checkpoint_hash,
        "fully_attested_package_id": package.package_id,
        "fully_attested_package_hash": package.package_hash,
        "anchors_id": anchors.anchors_id,
        "anchors_hash": anchors.anchors_hash,
        "acceptance_receipt_id": receipt.receipt_id,
        "acceptance_receipt_hash": receipt.receipt_hash,
        "native_local_rl_package_hash": result.local_rl_package_hash,
        "optimizer_evidence_hash": result.optimizer_evidence_hash,
        "heldout_evaluation_hash": result.heldout_evaluation_hash,
        "optimizer_config_hash": intent.optimizer_config_hash,
        "training_task_set_hash": intent.training_task_set_hash,
        "heldout_task_set_hash": intent.heldout_task_set_hash,
        "governed_actor_ids": actors,
        "source_commit": base.source_commit,
        "created_by": created_by,
        "created_at": created_at,
        "evidence_accepted": True,
        "tiny_local_agent_policy": True,
        "foundation_model_checkpoint": False,
        "checkpoint_promotion_authorized": False,
        "production_activation_authorized": False,
        "production_deployment_authorized": False,
    }
    return LocalPolicyCandidateManifest(
        **payload,
        manifest_hash=canonical_sha256(payload),
    )


def verify_candidate_evidence(
    candidate: LocalPolicyCandidateManifest,
    package: FullyAttestedProgramLocalRLBindingPackage,
    anchors: ProgramLocalRLTrustedAnchors,
    receipt: ProgramLocalRLAcceptanceReceipt,
) -> bool:
    verify_accepted_program_local_rl_evidence(package, anchors, receipt)
    base = _base_package(package)
    result = base.result
    intent = base.intent
    expected = {
        "fully_attested_package_id": package.package_id,
        "fully_attested_package_hash": package.package_hash,
        "anchors_id": anchors.anchors_id,
        "anchors_hash": anchors.anchors_hash,
        "acceptance_receipt_id": receipt.receipt_id,
        "acceptance_receipt_hash": receipt.receipt_hash,
        "base_checkpoint_hash": result.initial_checkpoint_hash,
        "selected_checkpoint_hash": result.selected_checkpoint_hash,
        "native_local_rl_package_hash": result.local_rl_package_hash,
        "optimizer_evidence_hash": result.optimizer_evidence_hash,
        "heldout_evaluation_hash": result.heldout_evaluation_hash,
        "optimizer_config_hash": intent.optimizer_config_hash,
        "training_task_set_hash": intent.training_task_set_hash,
        "heldout_task_set_hash": intent.heldout_task_set_hash,
        "governed_actor_ids": accepted_evidence_actor_ids(
            package,
            anchors,
            receipt,
        ),
        "source_commit": base.source_commit,
    }
    if any(getattr(candidate, field) != value for field, value in expected.items()):
        raise ValueError(
            "Local-policy candidate differs from accepted Program/local-RL evidence."
        )
    return True


def build_local_policy_promotion_report(
    candidate: LocalPolicyCandidateManifest,
    package: FullyAttestedProgramLocalRLBindingPackage,
    anchors: ProgramLocalRLTrustedAnchors,
    receipt: ProgramLocalRLAcceptanceReceipt,
    *,
    evaluator_id: str,
    evaluated_at: datetime,
    report_id: str | None = None,
) -> LocalPolicyPromotionReport:
    verify_candidate_evidence(candidate, package, anchors, receipt)
    if evaluator_id in {
        *candidate.governed_actor_ids,
        candidate.created_by,
    }:
        raise ValueError(
            "Local-policy promotion evaluator overlaps candidate or evidence roles."
        )
    if evaluated_at < max(candidate.created_at, receipt.accepted_at):
        raise ValueError(
            "Local-policy promotion assessment predates accepted evidence."
        )
    result = _base_package(package).result
    passed = (
        result.heldout_reward_delta > 0.0
        and result.heldout_success_delta > 0.0
        and result.unsafe_action_count == 0
        and result.regression_count == 0
    )
    payload = {
        "report_id": report_id
        or f"local-policy-promotion-report:{candidate.family_id}:{candidate.candidate_id}",
        "family_id": candidate.family_id,
        "candidate_id": candidate.candidate_id,
        "base_policy_id": candidate.base_policy_id,
        "candidate_manifest_hash": candidate.manifest_hash,
        "fully_attested_package_hash": package.package_hash,
        "acceptance_receipt_hash": receipt.receipt_hash,
        "selected_checkpoint_hash": candidate.selected_checkpoint_hash,
        "heldout_evaluation_hash": candidate.heldout_evaluation_hash,
        "heldout_reward_delta": result.heldout_reward_delta,
        "heldout_success_delta": result.heldout_success_delta,
        "unsafe_action_count": result.unsafe_action_count,
        "regression_count": result.regression_count,
        "evaluator_id": evaluator_id,
        "evaluated_at": evaluated_at,
        "passed": passed,
        "new_external_rollout_performed": False,
        "production_traffic_observed": False,
        "production_activation_authorized": False,
    }
    return LocalPolicyPromotionReport(
        **payload,
        report_hash=canonical_sha256(payload),
    )


def build_local_policy_promotion_decision(
    candidate: LocalPolicyCandidateManifest,
    report: LocalPolicyPromotionReport,
    *,
    decided_by: str,
    decided_at: datetime,
    decision_id: str | None = None,
) -> LocalPolicyPromotionDecision:
    if (
        report.family_id != candidate.family_id
        or report.candidate_id != candidate.candidate_id
        or report.base_policy_id != candidate.base_policy_id
        or report.candidate_manifest_hash != candidate.manifest_hash
    ):
        raise ValueError(
            "Local-policy promotion report differs from its candidate."
        )
    if decided_by in {
        *candidate.governed_actor_ids,
        candidate.created_by,
        report.evaluator_id,
    }:
        raise ValueError(
            "Local-policy promotion decision actor overlaps governed roles."
        )
    if decided_at < report.evaluated_at:
        raise ValueError(
            "Local-policy promotion decision predates its assessment."
        )
    reason = (
        "Accepted optimizer evidence satisfies strict safe promotion gates."
        if report.passed
        else "Accepted optimizer evidence does not satisfy promotion gates."
    )
    payload = {
        "decision_id": decision_id
        or f"local-policy-promotion-decision:{candidate.family_id}:{candidate.candidate_id}",
        "family_id": candidate.family_id,
        "candidate_id": candidate.candidate_id,
        "base_policy_id": candidate.base_policy_id,
        "report_id": report.report_id,
        "report_hash": report.report_hash,
        "report_passed": report.passed,
        "promote": report.passed,
        "reason": reason,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "checkpoint_promotion_authorized": False,
        "production_activation_authorized": False,
    }
    return LocalPolicyPromotionDecision(
        **payload,
        decision_hash=canonical_sha256(payload),
    )


def build_local_policy_rollback_request(
    record: LocalPolicyVersionRecord,
    *,
    evidence_hash: str,
    reason: str,
    requested_by: str,
    requested_at: datetime,
    forbidden_actor_ids: tuple[str, ...],
    request_id: str | None = None,
) -> LocalPolicyRollbackRequest:
    if record.parent_policy_id is None:
        raise ValueError("Initial local policy cannot be rolled back to a parent.")
    if record.promotion_campaign_id is None or record.promotion_decision is None:
        raise ValueError("Rollback requires completed promotion evidence.")
    if requested_by in set(forbidden_actor_ids):
        raise ValueError(
            "Local-policy rollback requester overlaps promotion or evidence roles."
        )
    if record.activated_at is None or requested_at < record.activated_at:
        raise ValueError("Local-policy rollback request predates activation.")
    payload = {
        "request_id": request_id
        or f"local-policy-rollback-request:{record.family_id}:{record.policy_id}",
        "family_id": record.family_id,
        "from_policy_id": record.policy_id,
        "to_policy_id": record.parent_policy_id,
        "promotion_campaign_id": record.promotion_campaign_id,
        "promotion_decision_hash": record.promotion_decision.decision_hash,
        "evidence_hash": evidence_hash,
        "reason": reason,
        "requested_by": requested_by,
        "requested_at": requested_at,
        "rollback_authorized": False,
        "production_deployment_authorized": False,
    }
    return LocalPolicyRollbackRequest(
        **payload,
        request_hash=canonical_sha256(payload),
    )


def build_local_policy_rollback_report(
    request: LocalPolicyRollbackRequest,
    *,
    evaluator_id: str,
    evaluated_at: datetime,
    forbidden_actor_ids: tuple[str, ...],
    report_id: str | None = None,
) -> LocalPolicyRollbackReport:
    if evaluator_id in {*forbidden_actor_ids, request.requested_by}:
        raise ValueError(
            "Local-policy rollback evaluator overlaps promotion or request roles."
        )
    if evaluated_at < request.requested_at:
        raise ValueError("Local-policy rollback assessment predates its request.")
    payload = {
        "report_id": report_id
        or f"local-policy-rollback-report:{request.family_id}:{request.from_policy_id}",
        "request_id": request.request_id,
        "request_hash": request.request_hash,
        "family_id": request.family_id,
        "from_policy_id": request.from_policy_id,
        "to_policy_id": request.to_policy_id,
        "evaluator_id": evaluator_id,
        "evaluated_at": evaluated_at,
        "source_is_active": True,
        "target_is_direct_parent": True,
        "target_is_superseded": True,
        "safe_to_rollback": True,
        "production_traffic_observed": False,
        "production_deployment_authorized": False,
    }
    return LocalPolicyRollbackReport(
        **payload,
        report_hash=canonical_sha256(payload),
    )


__all__ = [
    "accepted_evidence_actor_ids",
    "build_candidate_from_accepted_evidence",
    "build_initial_local_policy_manifest",
    "build_local_policy_promotion_decision",
    "build_local_policy_promotion_report",
    "build_local_policy_rollback_report",
    "build_local_policy_rollback_request",
    "verify_accepted_program_local_rl_evidence",
    "verify_candidate_evidence",
]
