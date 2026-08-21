from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from evoagent.benchmarks.models import ResourceBudget
from evoagent.campaigns import (
    ApprovalDecision,
    CampaignGovernanceService,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
)
from evoagent.model_registry.adapters import ModelCandidateAdapter
from evoagent.model_registry.decision import ModelActivationPolicy
from evoagent.model_registry.evaluator import IndependentModelCandidateEvaluator
from evoagent.model_registry.models import (
    ExternalModelCandidateManifest,
    ExternalTrainingReceipt,
    ModelActivationDecision,
    ModelActivationThresholds,
    ModelCandidateEvaluationReport,
    ModelEvaluationSuite,
    ModelVersionRecord,
    ModelVersionStatus,
)
from evoagent.model_registry.sqlite_registry import SQLiteModelRegistry


class ModelEvaluationSubmission(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: ModelVersionRecord
    report: ModelCandidateEvaluationReport
    decision: ModelActivationDecision
    campaign: CampaignRecord
    reused: bool


class ModelActivationLifecycleService:
    """Keep evaluation, authorization, activation and rollback separate."""

    def __init__(
        self,
        *,
        registry: SQLiteModelRegistry,
        campaign_governance: CampaignGovernanceService,
        evaluator: IndependentModelCandidateEvaluator,
        activation_policy: ModelActivationPolicy | None = None,
    ):
        self.registry = registry
        self.campaign_governance = campaign_governance
        self.evaluator = evaluator
        self.activation_policy = activation_policy or ModelActivationPolicy()

    def evaluate_and_submit(
        self,
        *,
        family_id: str,
        candidate_id: str,
        adapter: ModelCandidateAdapter,
        suite: ModelEvaluationSuite,
        evaluator_id: str,
        budget: ResourceBudget,
        thresholds: ModelActivationThresholds,
        decision_actor_id: str,
        decided_at: datetime,
    ) -> ModelEvaluationSubmission:
        record = self.registry.get(family_id, candidate_id)
        if not isinstance(record.manifest, ExternalModelCandidateManifest):
            raise ValueError("Initial model cannot enter candidate evaluation.")
        if record.training_receipt is None or record.training_package_hash is None:
            raise ValueError(
                "Candidate is missing external training admission evidence."
            )
        trainer_id = record.training_receipt.trainer_id
        if decision_actor_id in {trainer_id, evaluator_id}:
            raise ValueError(
                "Trainer or evaluator cannot issue the activation gate decision."
            )
        report = self.evaluator.evaluate(
            candidate=record.manifest,
            adapter=adapter,
            suite=suite,
            evaluator_id=evaluator_id,
            trainer_id=trainer_id,
            budget=budget,
        )
        decision = self.activation_policy.decide(
            report,
            thresholds=thresholds,
            decided_by=decision_actor_id,
            decided_at=decided_at,
        )

        if (
            record.status != ModelVersionStatus.CANDIDATE
            and record.activation_campaign_id is not None
        ):
            campaign = self.campaign_governance.repository.get(
                record.activation_campaign_id
            )
            self._validate_campaign(
                campaign,
                record,
                report,
                decision,
            )
            return ModelEvaluationSubmission(
                record=record,
                report=report,
                decision=decision,
                campaign=campaign,
                reused=True,
            )

        reservation = self.campaign_governance.reserve(
            campaign_type=CampaignType.MODEL_ACTIVATION,
            target_key=f"model-activation:{family_id}:{candidate_id}",
            fingerprint_source={
                "candidate_manifest_hash": record.manifest.manifest_hash,
                "training_receipt_hash": record.training_receipt.receipt_hash,
                "training_package_hash": record.training_package_hash,
                "evaluation_report_hash": report.report_hash,
                "activation_decision_hash": decision.decision_hash,
            },
            risk=CampaignRisk.HIGH,
            generated_by=trainer_id,
            metadata={
                "family_id": family_id,
                "candidate_id": candidate_id,
                "base_model_id": record.parent_model_id,
                "adapter_id": report.adapter_id,
                "adapter_hash": report.adapter_hash,
                "evaluator_id": evaluator_id,
                "decision_actor_id": decision_actor_id,
                "training_executed_by_evoagent": False,
            },
        )
        campaign = reservation.campaign
        if campaign.state == CampaignState.OPEN:
            campaign = self.campaign_governance.attach_candidate(
                campaign,
                candidate_ref=f"model-activation:{candidate_id}",
                artifact_payload=self._artifact_payload(
                    record,
                    report,
                    decision,
                ),
                actor_id=evaluator_id,
            )
        else:
            self._validate_campaign(
                campaign,
                record,
                report,
                decision,
                allow_unrecorded_campaign=True,
            )

        current = self.registry.record_evaluation(
            family_id,
            candidate_id,
            report,
            decision,
            activation_campaign_id=campaign.campaign_id,
            actor_id=evaluator_id,
        )

        campaign = self.campaign_governance.repository.get(
            campaign.campaign_id
        )
        if campaign.state in {
            CampaignState.CANDIDATE_READY,
            CampaignState.EVALUATION_PENDING,
        }:
            campaign = self.campaign_governance.submit_evaluation(
                campaign.campaign_id,
                passed=decision.activate,
                expected_revision=campaign.revision,
                actor_id=evaluator_id,
                reason=(
                    f"Independent report {report.report_hash}: "
                    f"{decision.reason}"
                ),
            )
        expected_state = (
            CampaignState.APPROVAL_PENDING
            if decision.activate
            else CampaignState.REJECTED
        )
        if campaign.state != expected_state:
            raise ValueError(
                "Activation Campaign reached unexpected state: "
                f"{campaign.state.value}"
            )
        return ModelEvaluationSubmission(
            record=current,
            report=report,
            decision=decision,
            campaign=campaign,
            reused=reservation.reused,
        )

    def approve(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ) -> CampaignRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.campaign_type != CampaignType.MODEL_ACTIVATION:
            raise ValueError(
                "Model activation approval requires MODEL_ACTIVATION Campaign."
            )
        prohibited = {
            campaign.generated_by,
            str(campaign.metadata.get("evaluator_id", "")),
            str(campaign.metadata.get("decision_actor_id", "")),
        }
        if actor_id in prohibited:
            raise ValueError(
                "Trainer, evaluator, or decision actor cannot approve activation."
            )
        return self.campaign_governance.approve(
            campaign_id,
            actor_id=actor_id,
            decision=ApprovalDecision.APPROVE,
            reason=reason,
            expected_revision=expected_revision,
        )

    def synchronize_authorization(
        self,
        *,
        family_id: str,
        candidate_id: str,
        campaign_id: str,
        actor_id: str,
    ) -> ModelVersionRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError("Activation Campaign is not AUTHORIZED.")
        self._validate_persisted_approvals(campaign)
        return self.registry.mark_authorized(
            family_id,
            candidate_id,
            campaign,
            actor_id=actor_id,
            reason=(
                "Exact evaluation-bound Model Activation Campaign "
                "was authorized."
            ),
        )

    def activate(
        self,
        *,
        family_id: str,
        candidate_id: str,
        campaign_id: str,
        expected_active_revision: int,
        actor_id: str,
    ) -> ModelVersionRecord:
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state not in {
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            raise ValueError(
                "Model Activation Campaign is not authorized or completed."
            )
        # Re-read the durable decisions immediately before pointer mutation.
        # This prevents a caller from bypassing the model-specific approve()
        # method through the generic Campaign governance surface.
        self._validate_persisted_approvals(campaign)
        active = self.registry.activate(
            family_id,
            candidate_id,
            campaign,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            reason=(
                "Explicit operator activation after independent "
                "evaluation and authorization."
            ),
        )
        campaign = self.campaign_governance.repository.get(campaign_id)
        if campaign.state == CampaignState.AUTHORIZED:
            campaign = self.campaign_governance.repository.transition(
                campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id=actor_id,
                reason=(
                    "Authorized candidate became the active model pointer."
                ),
            )
        if campaign.state != CampaignState.COMPLETED:
            raise ValueError(
                "Activation Campaign did not reach COMPLETED."
            )
        return active

    def rollback(
        self,
        *,
        family_id: str,
        from_model_id: str,
        to_model_id: str,
        expected_active_revision: int,
        actor_id: str,
        reason: str,
    ) -> ModelVersionRecord:
        return self.registry.rollback(
            family_id,
            from_model_id=from_model_id,
            to_model_id=to_model_id,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            reason=reason,
        )

    def _validate_persisted_approvals(
        self,
        campaign: CampaignRecord,
    ) -> None:
        if campaign.campaign_type != CampaignType.MODEL_ACTIVATION:
            raise ValueError(
                "Persisted approvals do not belong to a Model Activation Campaign."
            )
        if campaign.risk != CampaignRisk.HIGH:
            raise ValueError("Model Activation Campaign must remain HIGH risk.")
        if campaign.required_approvals != 2:
            raise ValueError(
                "High-risk Model activation requires exactly two approvals."
            )

        approvals = self.campaign_governance.repository.approvals(
            campaign.campaign_id
        )
        if len(approvals) != campaign.required_approvals:
            raise ValueError(
                "Persisted approval count does not match the Campaign threshold."
            )
        if any(
            approval.campaign_id != campaign.campaign_id
            for approval in approvals
        ):
            raise ValueError("Persisted approval is bound to another Campaign.")
        if any(
            approval.decision != ApprovalDecision.APPROVE
            for approval in approvals
        ):
            raise ValueError(
                "Model activation contains a non-approval decision."
            )

        approver_ids = [approval.approver_id for approval in approvals]
        if len(set(approver_ids)) != len(approver_ids):
            raise ValueError("Model activation approvers must be distinct.")

        trainer_id = campaign.generated_by
        evaluator_id = str(campaign.metadata.get("evaluator_id", ""))
        decision_actor_id = str(
            campaign.metadata.get("decision_actor_id", "")
        )
        if trainer_id in approver_ids:
            raise ValueError(
                "Trainer approved activation through a generic Campaign path."
            )
        if evaluator_id in approver_ids:
            raise ValueError(
                "Evaluator approved activation through a generic Campaign path."
            )
        if decision_actor_id in approver_ids:
            raise ValueError(
                "Decision actor approved activation through a generic Campaign path."
            )

    @staticmethod
    def _artifact_payload(
        record: ModelVersionRecord,
        report: ModelCandidateEvaluationReport,
        decision: ModelActivationDecision,
    ) -> dict:
        if record.training_receipt is None:
            raise ValueError("Candidate training receipt is missing.")
        return {
            "kind": "model_activation_candidate",
            "candidate_manifest": record.manifest.model_dump(mode="json"),
            "training_receipt": record.training_receipt.model_dump(
                mode="json"
            ),
            "training_package_hash": record.training_package_hash,
            "evaluation_report": report.model_dump(mode="json"),
            "activation_decision": decision.model_dump(mode="json"),
        }

    @classmethod
    def _validate_campaign(
        cls,
        campaign: CampaignRecord,
        record: ModelVersionRecord,
        report: ModelCandidateEvaluationReport,
        decision: ModelActivationDecision,
        *,
        allow_unrecorded_campaign: bool = False,
    ) -> None:
        payload = campaign.artifact_payload or {}
        if (
            campaign.campaign_type != CampaignType.MODEL_ACTIVATION
            or payload.get("kind") != "model_activation_candidate"
        ):
            raise ValueError(
                "Reused Campaign is not a Model activation Campaign."
            )
        if record.training_receipt is None:
            raise ValueError("Candidate training receipt is missing.")
        stored_manifest = ExternalModelCandidateManifest.model_validate(
            payload.get("candidate_manifest")
        )
        stored_receipt = ExternalTrainingReceipt.model_validate(
            payload.get("training_receipt")
        )
        stored_report = ModelCandidateEvaluationReport.model_validate(
            payload.get("evaluation_report")
        )
        stored_decision = ModelActivationDecision.model_validate(
            payload.get("activation_decision")
        )
        if (
            stored_manifest != record.manifest
            or stored_receipt != record.training_receipt
            or payload.get("training_package_hash")
            != record.training_package_hash
            or stored_report != report
            or stored_decision != decision
        ):
            raise ValueError(
                "Reused activation Campaign differs from immutable evidence."
            )
        if not allow_unrecorded_campaign and (
            record.evaluation != report
            or record.activation_decision != decision
            or record.activation_campaign_id != campaign.campaign_id
        ):
            raise ValueError(
                "Registry evaluation differs from the activation Campaign."
            )


__all__ = [
    "ModelActivationLifecycleService",
    "ModelEvaluationSubmission",
]
