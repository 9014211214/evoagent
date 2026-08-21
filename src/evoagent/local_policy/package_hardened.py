from __future__ import annotations

from evoagent.campaigns import CampaignRisk

from .package import (
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManager as _BasePackageManager,
    LocalPolicyPromotionPackageManifest,
)


class LocalPolicyPromotionPackageManager(_BasePackageManager):
    """Verifier with provenance, Campaign governance and role separation."""

    @classmethod
    def verify(cls, package: LocalPolicyPromotionPackageManifest) -> bool:
        _BasePackageManager.verify(package)
        cls._verify_accepted_provenance(package)
        cls._verify_campaign_governance(package)
        record = package.candidate_record
        if package.rollback_campaign is None:
            return True
        promotion_controls = {
            actor
            for actor in (
                record.promotion_authorized_by,
                record.activated_by,
            )
            if actor is not None
        }
        rollback_actors = {
            record.rollback_request.requested_by,
            record.rollback_report.evaluator_id,
            *(item.actor_id for item in package.rollback_approvals),
            record.rollback_authorized_by,
            record.rolled_back_by,
        }
        rollback_actors.discard(None)
        if promotion_controls & rollback_actors:
            raise LocalPolicyPromotionPackageError(
                "Promotion control actors overlap the governed rollback lifecycle."
            )
        return True

    @staticmethod
    def _verify_accepted_provenance(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        base = (
            package.accepted_program_package.runtime_attested_package
            .schema_attested_package.attested_package.base_package
        )
        if (
            package.framework_version != base.framework_version
            or package.source_repository != base.source_repository
            or package.third_party_lock_hash
            != base.third_party_lock_hash
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy package provenance differs from accepted v2.1 evidence."
            )

    @staticmethod
    def _verify_campaign_governance(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        campaigns = [package.promotion_campaign]
        if package.rollback_campaign is not None:
            campaigns.append(package.rollback_campaign)
        if any(
            item.risk != CampaignRisk.HIGH
            or item.required_approvals != 2
            for item in campaigns
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign governance is not HIGH risk with two approvals."
            )


__all__ = [
    "LocalPolicyPromotionPackageError",
    "LocalPolicyPromotionPackageManager",
    "LocalPolicyPromotionPackageManifest",
]
