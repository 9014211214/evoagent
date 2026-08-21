from __future__ import annotations

from .local_policy_promotion import (
    AcceptedLocalPolicyPromotionLab as _BaseLab,
    LocalPolicyPromotionLabResult,
)


class AcceptedLocalPolicyPromotionLab(_BaseLab):
    """Final Lab result includes the package path and stable audit verification."""

    @staticmethod
    def _verify_persistent_state(registry, campaigns, package) -> None:
        registry.verify_audit(package.local_policy_checkpoint)
        registry.verify_state(package.candidate_record.family_id)
        campaigns.verify_audit(package.campaign_checkpoint)
        campaign_events = tuple(campaigns.audit_events())
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
            or campaign_events != package.campaign_events
            or campaigns.checkpoint() != package.campaign_checkpoint
        ):
            raise RuntimeError(
                "Persistent local-policy Lab state differs from its package."
            )

    def _result(self, package, *, resumed: bool) -> LocalPolicyPromotionLabResult:
        result = super()._result(package, resumed=resumed)
        return result.model_copy(
            update={"package_path": str(self.package_path)}
        )


__all__ = [
    "AcceptedLocalPolicyPromotionLab",
    "LocalPolicyPromotionLabResult",
]
