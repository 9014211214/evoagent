from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import LocalPolicyCandidateManifest


_MAX_CLOCK_SKEW = timedelta(minutes=2)
from .repository import (
    LocalPolicyAuditIntegrityError,
    LocalPolicyRegistryConflictError,
    StaleLocalPolicyRevision,
)
from .repository_final import (
    SQLiteLocalPolicyRegistry as _RetrySafeRegistry,
)


class SQLiteLocalPolicyRegistry(_RetrySafeRegistry):
    """Final Registry with complete pre-transaction business chronology."""

    @staticmethod
    def _effective_now(value: datetime | None) -> datetime:
        now = value or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError(
                "Local-policy Registry write time must include a timezone."
            )
        if now > datetime.now(timezone.utc) + _MAX_CLOCK_SKEW:
            raise ValueError(
                "Local-policy Registry write time exceeds the bounded clock-skew window."
            )
        return now

    def register_initial(self, manifest, *, actor_id, now=None):
        effective = self._effective_now(now)
        if manifest.created_at > effective:
            raise ValueError(
                "Initial local-policy manifest postdates its Registry write."
            )
        return super().register_initial(
            manifest,
            actor_id=actor_id,
            now=effective,
        )

    def admit_candidate(self, manifest, *, actor_id, now=None):
        if not isinstance(manifest, LocalPolicyCandidateManifest):
            raise TypeError(
                "Local-policy candidate admission requires a candidate manifest."
            )
        effective = self._effective_now(now)
        parent = self.get(manifest.family_id, manifest.base_policy_id)
        if (
            parent.manifest.optimizer_config_hash
            != manifest.optimizer_config_hash
            or parent.manifest.source_commit != manifest.source_commit
        ):
            raise ValueError(
                "Candidate parent optimizer configuration or source commit differs."
            )
        if not (
            parent.created_at <= manifest.created_at <= effective
        ):
            raise ValueError(
                "Candidate creation time differs from parent/write chronology."
            )
        return super().admit_candidate(
            manifest,
            actor_id=actor_id,
            now=effective,
        )

    def record_promotion(
        self,
        family_id,
        candidate_id,
        report,
        decision,
        *,
        campaign_id,
        actor_id,
        now=None,
    ):
        effective = self._effective_now(now)
        if not (
            report.evaluated_at <= decision.decided_at <= effective
        ):
            raise ValueError(
                "Promotion report, decision and Registry write chronology differs."
            )
        return super().record_promotion(
            family_id,
            candidate_id,
            report,
            decision,
            campaign_id=campaign_id,
            actor_id=actor_id,
            now=effective,
        )

    def mark_promotion_authorized(
        self,
        family_id,
        candidate_id,
        campaign,
        *,
        actor_id,
        now=None,
    ):
        effective = self._effective_now(now)
        if now is None:
            effective = max(effective, campaign.updated_at)
        if campaign.updated_at > effective:
            raise ValueError(
                "Promotion Campaign postdates Registry authorization."
            )
        return super().mark_promotion_authorized(
            family_id,
            candidate_id,
            campaign,
            actor_id=actor_id,
            now=effective,
        )

    def activate(
        self,
        family_id,
        candidate_id,
        campaign,
        *,
        expected_active_revision,
        actor_id,
        now=None,
    ):
        effective = self._effective_now(now)
        if now is None:
            effective = max(effective, campaign.updated_at)
        if campaign.updated_at > effective:
            raise ValueError(
                "Promotion Campaign postdates pointer activation."
            )
        return super().activate(
            family_id,
            candidate_id,
            campaign,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            now=effective,
        )

    def record_rollback_submission(
        self,
        family_id,
        candidate_id,
        request,
        report,
        *,
        campaign_id,
        actor_id,
        now=None,
    ):
        effective = self._effective_now(now)
        if not (
            request.requested_at <= report.evaluated_at <= effective
        ):
            raise ValueError(
                "Rollback request, report and Registry write chronology differs."
            )
        return super().record_rollback_submission(
            family_id,
            candidate_id,
            request,
            report,
            campaign_id=campaign_id,
            actor_id=actor_id,
            now=effective,
        )

    def mark_rollback_authorized(
        self,
        family_id,
        candidate_id,
        campaign,
        *,
        actor_id,
        now=None,
    ):
        effective = self._effective_now(now)
        if now is None:
            effective = max(effective, campaign.updated_at)
        if campaign.updated_at > effective:
            raise ValueError(
                "Rollback Campaign postdates Registry authorization."
            )
        return super().mark_rollback_authorized(
            family_id,
            candidate_id,
            campaign,
            actor_id=actor_id,
            now=effective,
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
        now=None,
    ):
        effective = self._effective_now(now)
        if now is None:
            effective = max(effective, campaign.updated_at)
        if campaign.updated_at > effective:
            raise ValueError(
                "Rollback Campaign postdates pointer rollback."
            )
        return super().rollback(
            family_id,
            from_policy_id=from_policy_id,
            to_policy_id=to_policy_id,
            campaign=campaign,
            expected_active_revision=expected_active_revision,
            actor_id=actor_id,
            now=effective,
        )


__all__ = [
    "LocalPolicyAuditIntegrityError",
    "LocalPolicyRegistryConflictError",
    "SQLiteLocalPolicyRegistry",
    "StaleLocalPolicyRevision",
]
