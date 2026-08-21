from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from evoagent import __version__
from evoagent.campaigns import (
    CampaignGovernanceService,
    SQLiteCampaignRepository,
)
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.local_policy import (
    LocalPolicyPromotionLifecycleService,
    LocalPolicyPromotionPackageManager,
    SQLiteLocalPolicyRegistry,
    build_initial_local_policy_manifest,
)
from evoagent.program_rl import (
    FullyAttestedProgramLocalRLBindingPackage,
    ProgramLocalRLAcceptanceReceipt,
    ProgramLocalRLTrustedAnchors,
)


class LocalPolicyPromotionLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    family_id: str
    candidate_policy_id: str
    active_policy_id: str
    active_revision: int
    resumed: bool
    promotion_completed: bool
    rollback_completed: bool
    local_policy_event_count: int
    campaign_event_count: int
    package_path: str
    package_hash: str
    local_policy_pointer_mutation_only: bool
    foundation_model_weights_updated: bool
    production_activation_performed: bool
    production_deployment_performed: bool


class AcceptedLocalPolicyPromotionLab:
    """Execute governed local-pointer promotion from accepted v2.1 evidence."""

    def __init__(
        self,
        root: str | Path,
        *,
        accepted_program_package: FullyAttestedProgramLocalRLBindingPackage,
        trusted_anchors: ProgramLocalRLTrustedAnchors,
        acceptance_receipt: ProgramLocalRLAcceptanceReceipt,
        family_id: str = "local-policy-family:accepted-lab",
        initial_policy_id: str = "local-policy:accepted:p0",
        candidate_policy_id: str = "local-policy:accepted:p1",
        source_commit: str = "0" * 40,
        perform_rollback: bool = True,
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Local-policy promotion Lab root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef"
            for character in source_commit
        ):
            raise ValueError(
                "source_commit must be lowercase 40-character Git hex."
            )
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.accepted_program_package = accepted_program_package
        self.trusted_anchors = trusted_anchors
        self.acceptance_receipt = acceptance_receipt
        self.family_id = family_id
        self.initial_policy_id = initial_policy_id
        self.candidate_policy_id = candidate_policy_id
        self.source_commit = source_commit
        self.perform_rollback = perform_rollback

    @property
    def registry_path(self) -> Path:
        return self.root / "local-policy.db"

    @property
    def campaign_path(self) -> Path:
        return self.root / "local-policy-campaigns.db"

    @property
    def package_path(self) -> Path:
        return self.root / "local-policy-promotion-package.json"

    @staticmethod
    def _base(package: FullyAttestedProgramLocalRLBindingPackage):
        return (
            package.runtime_attested_package
            .schema_attested_package
            .attested_package
            .base_package
        )

    def run(self) -> LocalPolicyPromotionLabResult:
        registry = SQLiteLocalPolicyRegistry(self.registry_path)
        campaigns = SQLiteCampaignRepository(self.campaign_path)
        governance = CampaignGovernanceService(campaigns)
        manager = LocalPolicyPromotionPackageManager()

        if self.package_path.exists():
            package = manager.load_file(self.package_path)
            if (
                package.accepted_program_package
                != self.accepted_program_package
                or package.trusted_anchors != self.trusted_anchors
                or package.acceptance_receipt != self.acceptance_receipt
                or package.candidate_record.family_id != self.family_id
                or package.candidate_record.policy_id
                != self.candidate_policy_id
            ):
                raise RuntimeError(
                    "Local-policy promotion Lab resume inputs differ from its package."
                )
            self._verify_persistent_state(registry, campaigns, package)
            return self._result(package, resumed=True)

        base = self._base(self.accepted_program_package)
        now = datetime.now(timezone.utc)
        initial_at = max(
            self.acceptance_receipt.accepted_at + timedelta(seconds=1),
            now - timedelta(minutes=5),
        )
        if initial_at > now + timedelta(minutes=2):
            raise RuntimeError(
                "Accepted v2.1 evidence exceeds the bounded Lab clock-skew window."
            )
        initial = build_initial_local_policy_manifest(
            family_id=self.family_id,
            policy_id=self.initial_policy_id,
            checkpoint_hash=base.result.initial_checkpoint_hash,
            optimizer_config_hash=base.intent.optimizer_config_hash,
            source_commit=base.source_commit,
            created_by="local-policy-lab-bootstrap-owner",
            created_at=initial_at,
        )
        registry.register_initial(
            initial,
            actor_id=initial.created_by,
            now=initial_at,
        )
        service = LocalPolicyPromotionLifecycleService(registry, governance)
        candidate_at = max(
            datetime.now(timezone.utc),
            initial_at + timedelta(milliseconds=1),
        )
        service.admit_candidate(
            self.accepted_program_package,
            self.trusted_anchors,
            self.acceptance_receipt,
            family_id=self.family_id,
            candidate_id=self.candidate_policy_id,
            base_policy_id=self.initial_policy_id,
            created_by="local-policy-lab-candidate-controller",
            created_at=candidate_at,
        )
        evaluation_at = max(
            datetime.now(timezone.utc),
            candidate_at + timedelta(milliseconds=1),
        )
        submission = service.submit_promotion(
            self.accepted_program_package,
            self.trusted_anchors,
            self.acceptance_receipt,
            family_id=self.family_id,
            candidate_id=self.candidate_policy_id,
            evaluator_id="local-policy-lab-promotion-evaluator",
            evaluated_at=evaluation_at,
            decision_actor_id="local-policy-lab-promotion-decider",
            decided_at=evaluation_at,
        )
        campaign = service.approve_promotion(
            submission.campaign.campaign_id,
            actor_id="local-policy-lab-promotion-reviewer-a",
            reason="Accepted evidence lineage passed independent review.",
            expected_revision=submission.campaign.revision,
        )
        campaign = service.approve_promotion(
            campaign.campaign_id,
            actor_id="local-policy-lab-promotion-reviewer-b",
            reason="Local pointer safety and rollback readiness passed review.",
            expected_revision=campaign.revision,
        )
        service.synchronize_promotion_authorization(
            family_id=self.family_id,
            candidate_id=self.candidate_policy_id,
            campaign_id=campaign.campaign_id,
            actor_id="local-policy-lab-promotion-authorizer",
        )
        service.activate(
            family_id=self.family_id,
            candidate_id=self.candidate_policy_id,
            campaign_id=campaign.campaign_id,
            expected_active_revision=0,
            actor_id="local-policy-lab-activation-executor",
        )

        if self.perform_rollback:
            active = registry.get(self.family_id, self.candidate_policy_id)
            rollback_submission = service.submit_rollback(
                family_id=self.family_id,
                candidate_id=self.candidate_policy_id,
                evidence_hash=base.result.heldout_evaluation_hash,
                reason="Controlled Lab rollback to the direct parent policy.",
                requested_by="local-policy-lab-rollback-requester",
                requested_at=active.activated_at,
                evaluator_id="local-policy-lab-rollback-evaluator",
                evaluated_at=max(
                    datetime.now(timezone.utc),
                    active.activated_at,
                ),
            )
            rollback_campaign = service.approve_rollback(
                rollback_submission.campaign.campaign_id,
                actor_id="local-policy-lab-rollback-reviewer-a",
                reason="Direct-parent rollback lineage passed review.",
                expected_revision=rollback_submission.campaign.revision,
            )
            rollback_campaign = service.approve_rollback(
                rollback_campaign.campaign_id,
                actor_id="local-policy-lab-rollback-reviewer-b",
                reason="Rollback pointer transaction passed independent review.",
                expected_revision=rollback_campaign.revision,
            )
            service.synchronize_rollback_authorization(
                family_id=self.family_id,
                candidate_id=self.candidate_policy_id,
                campaign_id=rollback_campaign.campaign_id,
                actor_id="local-policy-lab-rollback-authorizer",
            )
            service.rollback(
                family_id=self.family_id,
                from_policy_id=self.candidate_policy_id,
                to_policy_id=self.initial_policy_id,
                campaign_id=rollback_campaign.campaign_id,
                expected_active_revision=1,
                actor_id="local-policy-lab-rollback-executor",
            )

        evidence_times = [
            *(item.created_at for item in registry.events()),
            *(item.created_at for item in campaigns.audit_events()),
        ]
        package = manager.build(
            package_id="local-policy-promotion-package:accepted-lab",
            created_at=max(
                datetime.now(timezone.utc),
                *evidence_times,
            ) + timedelta(milliseconds=1),
            framework_version=__version__,
            source_repository=base.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            accepted_program_package=self.accepted_program_package,
            trusted_anchors=self.trusted_anchors,
            acceptance_receipt=self.acceptance_receipt,
            registry=registry,
            campaigns=campaigns,
            family_id=self.family_id,
            candidate_id=self.candidate_policy_id,
        )
        manager.export_file(package, self.package_path)
        self._verify_persistent_state(registry, campaigns, package)
        return self._result(package, resumed=False)

    @staticmethod
    def _verify_persistent_state(registry, campaigns, package) -> None:
        registry.verify_audit(package.local_policy_checkpoint)
        registry.verify_state(package.candidate_record.family_id)
        if (
            registry.head(package.candidate_record.family_id)
            != package.final_head
            or registry.get(
                package.initial_record.family_id,
                package.initial_record.policy_id,
            )
            != package.initial_record
            or registry.get(
                package.candidate_record.family_id,
                package.candidate_record.policy_id,
            )
            != package.candidate_record
            or registry.events() != package.local_policy_events
            or campaigns.events() != package.campaign_events
            or campaigns.checkpoint() != package.campaign_checkpoint
        ):
            raise RuntimeError(
                "Persistent local-policy Lab state differs from its package."
            )

    @staticmethod
    def _result(package, *, resumed: bool) -> LocalPolicyPromotionLabResult:
        return LocalPolicyPromotionLabResult(
            family_id=package.candidate_record.family_id,
            candidate_policy_id=package.candidate_record.policy_id,
            active_policy_id=package.final_head.active_policy_id,
            active_revision=package.final_head.revision,
            resumed=resumed,
            promotion_completed=True,
            rollback_completed=package.rollback_campaign is not None,
            local_policy_event_count=len(package.local_policy_events),
            campaign_event_count=len(package.campaign_events),
            package_path="",
            package_hash=package.package_hash,
            local_policy_pointer_mutation_only=(
                package.local_policy_pointer_mutation_only
            ),
            foundation_model_weights_updated=(
                package.foundation_model_weights_updated
            ),
            production_activation_performed=(
                package.production_activation_performed
            ),
            production_deployment_performed=(
                package.production_deployment_performed
            ),
        )


__all__ = [
    "AcceptedLocalPolicyPromotionLab",
    "LocalPolicyPromotionLabResult",
]
