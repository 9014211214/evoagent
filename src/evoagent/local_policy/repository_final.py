from __future__ import annotations

from .models import LocalPolicyVersionStatus
from .repository import (
    LocalPolicyAuditIntegrityError,
    LocalPolicyRegistryConflictError,
    SQLiteLocalPolicyRegistry as _BaseRegistry,
    StaleLocalPolicyRevision,
)


class SQLiteLocalPolicyRegistry(_BaseRegistry):
    """Final Registry with exact optimistic-revision retry semantics."""

    def activate(
        self,
        family_id,
        candidate_id,
        campaign,
        *,
        expected_active_revision,
        actor_id,
        **kwargs,
    ):
        head = self.head(family_id)
        record = self.get(family_id, candidate_id)
        if (
            head.active_policy_id == candidate_id
            and record.status == LocalPolicyVersionStatus.ACTIVE
        ):
            if head.revision != expected_active_revision + 1:
                raise StaleLocalPolicyRevision(
                    "Applied activation does not match the retried optimistic revision."
                )
            if record.activated_by != actor_id:
                raise LocalPolicyRegistryConflictError(
                    "Activation retry used another actor."
                )
        return super().activate(
            family_id,
            candidate_id,
            campaign,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            **kwargs,
        )

    def rollback(
        self,
        family_id,
        *,
        from_policy_id,
        to_policy_id,
        campaign,
        expected_active_revision,
        actor_id,
        **kwargs,
    ):
        current = self.get(family_id, from_policy_id)
        if (
            current.parent_policy_id != to_policy_id
            or current.rollback_request is None
            or current.rollback_request.to_policy_id != to_policy_id
        ):
            raise ValueError(
                "Rollback target differs from the exact direct-parent request."
            )
        head = self.head(family_id)
        target = self.get(family_id, to_policy_id)
        if (
            head.active_policy_id == to_policy_id
            and current.status == LocalPolicyVersionStatus.ROLLED_BACK
            and target.status == LocalPolicyVersionStatus.ACTIVE
        ):
            if head.revision != expected_active_revision + 1:
                raise StaleLocalPolicyRevision(
                    "Applied rollback does not match the retried optimistic revision."
                )
            if current.rolled_back_by != actor_id:
                raise LocalPolicyRegistryConflictError(
                    "Rollback retry used another actor."
                )
        return super().rollback(
            family_id,
            from_policy_id=from_policy_id,
            to_policy_id=to_policy_id,
            campaign=campaign,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            **kwargs,
        )


__all__ = [
    "LocalPolicyAuditIntegrityError",
    "LocalPolicyRegistryConflictError",
    "SQLiteLocalPolicyRegistry",
    "StaleLocalPolicyRevision",
]
