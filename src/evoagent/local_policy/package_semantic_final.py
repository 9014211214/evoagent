from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from evoagent.campaigns import CampaignState, CampaignType

from .package import (
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManifest,
)
from .package_hardened import (
    LocalPolicyPromotionPackageManager as _RoleSeparatedManager,
)


_MAX_CLOCK_SKEW = timedelta(minutes=2)


class _CampaignAuditReadAdapter:
    """Expose the immutable audit stream under the package builder contract."""

    def __init__(self, repository):
        self.repository = repository

    def events(self):
        return self.repository.audit_events()

    def __getattr__(self, name):
        return getattr(self.repository, name)


class LocalPolicyPromotionPackageManager(_RoleSeparatedManager):
    """Final verifier with exact audit metadata and monotonic chronology."""

    def build(self, *, campaigns, **kwargs):
        return super().build(
            campaigns=_CampaignAuditReadAdapter(campaigns),
            **kwargs,
        )

    def export_file(
        self,
        package: LocalPolicyPromotionPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(package)
        destination = Path(path).expanduser()
        if destination.is_symlink():
            raise LocalPolicyPromotionPackageError(
                "Local-policy package output must not be a symlink."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = (package.model_dump_json(indent=2) + "\n").encode("utf-8")
        if destination.exists():
            if not destination.is_file():
                raise LocalPolicyPromotionPackageError(
                    "Local-policy package output must be a regular file."
                )
            try:
                existing = destination.read_bytes()
            except OSError as exc:
                raise LocalPolicyPromotionPackageError(
                    f"Existing local-policy package cannot be read: {exc}"
                ) from exc
            if existing != encoded:
                raise LocalPolicyPromotionPackageError(
                    "Existing local-policy package differs from immutable evidence."
                )
            return destination
        return super().export_file(package, destination)

    @classmethod
    def verify(cls, package: LocalPolicyPromotionPackageManifest) -> bool:
        _RoleSeparatedManager.verify(package)
        cls._verify_campaign_scope(package)
        cls._verify_exact_local_metadata(package)
        cls._verify_campaign_semantics(package)
        cls._verify_monotonic_times(package)
        cls._verify_package_time(package)
        return True

    @staticmethod
    def _verify_campaign_scope(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        allowed = {package.promotion_campaign.campaign_id}
        if package.rollback_campaign is not None:
            allowed.add(package.rollback_campaign.campaign_id)
        if any(
            event.campaign_id not in allowed
            for event in package.campaign_events
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy package contains another Campaign audit event."
            )

    @staticmethod
    def _verify_exact_local_metadata(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        initial = package.initial_record
        candidate_record = package.candidate_record
        candidate = candidate_record.manifest
        promotion_report = candidate_record.promotion_report
        promotion_decision = candidate_record.promotion_decision
        expected = [
            {"manifest_hash": initial.manifest.manifest_hash},
            {
                "manifest_hash": candidate.manifest_hash,
                "fully_attested_package_hash": (
                    candidate.fully_attested_package_hash
                ),
                "acceptance_receipt_hash": candidate.acceptance_receipt_hash,
            },
            {
                "report_hash": promotion_report.report_hash,
                "decision_hash": promotion_decision.decision_hash,
                "campaign_id": package.promotion_campaign.campaign_id,
                "promote": True,
            },
            {
                "campaign_id": package.promotion_campaign.campaign_id,
                "campaign_revision": package.promotion_campaign.revision - 1,
                "decision_hash": promotion_decision.decision_hash,
            },
            {
                "campaign_id": package.promotion_campaign.campaign_id,
                "decision_hash": promotion_decision.decision_hash,
                "active_revision_before": 0,
            },
        ]
        if package.rollback_campaign is not None:
            request = candidate_record.rollback_request
            report = candidate_record.rollback_report
            expected.extend(
                [
                    {
                        "request_hash": request.request_hash,
                        "report_hash": report.report_hash,
                        "campaign_id": package.rollback_campaign.campaign_id,
                    },
                    {
                        "campaign_id": package.rollback_campaign.campaign_id,
                        "campaign_revision": (
                            package.rollback_campaign.revision - 1
                        ),
                        "request_hash": request.request_hash,
                        "report_hash": report.report_hash,
                    },
                    {
                        "campaign_id": package.rollback_campaign.campaign_id,
                        "request_hash": request.request_hash,
                        "active_revision_before": 1,
                    },
                ]
            )
        actual = [item.metadata for item in package.local_policy_events]
        if actual != expected:
            raise LocalPolicyPromotionPackageError(
                "Local-policy audit metadata differs from governed lifecycle evidence."
            )

    @classmethod
    def _verify_campaign_semantics(
        cls,
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        candidate = package.candidate_record
        cls._verify_one_campaign(
            campaign=package.promotion_campaign,
            events=tuple(
                item
                for item in package.campaign_events
                if item.campaign_id
                == package.promotion_campaign.campaign_id
            ),
            approvals=package.promotion_approvals,
            expected_type=CampaignType.LOCAL_POLICY_PROMOTION,
            expected_generator=candidate.manifest.created_by,
            expected_candidate_actor=(
                candidate.promotion_decision.decided_by
            ),
            expected_evaluator=candidate.promotion_report.evaluator_id,
            expected_executor=candidate.activated_by,
            expected_created_at=candidate.promotion_report.evaluated_at,
            completion_reason=(
                "Authorized local-policy promotion completed."
            ),
        )
        if package.rollback_campaign is not None:
            cls._verify_one_campaign(
                campaign=package.rollback_campaign,
                events=tuple(
                    item
                    for item in package.campaign_events
                    if item.campaign_id
                    == package.rollback_campaign.campaign_id
                ),
                approvals=package.rollback_approvals,
                expected_type=CampaignType.LOCAL_POLICY_ROLLBACK,
                expected_generator=candidate.rollback_request.requested_by,
                expected_candidate_actor=(
                    candidate.rollback_report.evaluator_id
                ),
                expected_evaluator=candidate.rollback_report.evaluator_id,
                expected_executor=candidate.rolled_back_by,
                expected_created_at=(
                    candidate.rollback_request.requested_at
                ),
                completion_reason=(
                    "Authorized local-policy rollback completed."
                ),
            )

    @staticmethod
    def _verify_one_campaign(
        *,
        campaign,
        events,
        approvals,
        expected_type,
        expected_generator,
        expected_candidate_actor,
        expected_evaluator,
        expected_executor,
        expected_created_at,
        completion_reason,
    ) -> None:
        if (
            campaign.campaign_type != expected_type
            or campaign.state != CampaignState.COMPLETED
            or campaign.generated_by != expected_generator
            or campaign.required_approvals != 2
            or campaign.revision != 6
            or campaign.created_at != expected_created_at
            or len(events) != 7
            or len(approvals) != 2
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign record differs from governed lifecycle evidence."
            )
        (
            created,
            candidate,
            evaluation_started,
            evaluation_completed,
            approval_a,
            approval_b,
            completed,
        ) = events
        if (
            created.event_type != "campaign_created"
            or created.actor_id != "evoagent-system"
            or created.created_at != campaign.created_at
            or created.payload
            != {
                "campaign_type": expected_type.value,
                "target_key": campaign.target_key,
                "fingerprint": campaign.fingerprint,
                "state": CampaignState.OPEN.value,
            }
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign creation audit semantics differ."
            )
        if (
            candidate.event_type != "candidate_attached"
            or candidate.actor_id != expected_candidate_actor
            or candidate.payload
            != {"candidate_ref": campaign.candidate_ref}
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign candidate audit semantics differ."
            )
        if (
            evaluation_started.event_type != "campaign_transitioned"
            or evaluation_started.actor_id != expected_evaluator
            or evaluation_started.payload
            != {
                "from_state": CampaignState.CANDIDATE_READY.value,
                "to_state": CampaignState.EVALUATION_PENDING.value,
                "reason": "Independent local-policy evaluation started.",
                "cooldown_until": None,
            }
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign evaluation-start audit semantics differ."
            )
        if (
            evaluation_completed.event_type != "campaign_transitioned"
            or evaluation_completed.actor_id != expected_evaluator
            or evaluation_completed.payload
            != {
                "from_state": CampaignState.EVALUATION_PENDING.value,
                "to_state": CampaignState.APPROVAL_PENDING.value,
                "reason": "Independent local-policy evaluation passed.",
                "cooldown_until": None,
            }
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign evaluation-result audit semantics differ."
            )
        for event, approval, resulting_state in zip(
            (approval_a, approval_b),
            approvals,
            (
                CampaignState.APPROVAL_PENDING.value,
                CampaignState.AUTHORIZED.value,
            ),
            strict=True,
        ):
            if (
                event.event_type != "approval_recorded"
                or event.actor_id != approval.actor_id
                or event.created_at != approval.created_at
                or event.payload
                != {
                    "decision": approval.decision.value,
                    "reason": approval.reason,
                    "resulting_state": resulting_state,
                }
            ):
                raise LocalPolicyPromotionPackageError(
                    "Local-policy Campaign approval audit semantics differ."
                )
        if (
            completed.event_type != "campaign_transitioned"
            or completed.actor_id != expected_executor
            or completed.created_at != campaign.updated_at
            or completed.payload
            != {
                "from_state": CampaignState.AUTHORIZED.value,
                "to_state": CampaignState.COMPLETED.value,
                "reason": completion_reason,
                "cooldown_until": None,
            }
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign completion audit semantics differ."
            )

    @staticmethod
    def _verify_monotonic_times(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        local_times = tuple(
            item.created_at for item in package.local_policy_events
        )
        if local_times != tuple(sorted(local_times)):
            raise LocalPolicyPromotionPackageError(
                "Local-policy audit timestamps are not monotonic."
            )
        campaign_times = tuple(
            item.created_at for item in package.campaign_events
        )
        if campaign_times != tuple(sorted(campaign_times)):
            raise LocalPolicyPromotionPackageError(
                "Campaign audit timestamps are not monotonic."
            )
        promotion_approval_times = tuple(
            item.created_at for item in package.promotion_approvals
        )
        if any(
            value < package.candidate_record.promotion_decision.decided_at
            for value in promotion_approval_times
        ):
            raise LocalPolicyPromotionPackageError(
                "Promotion approvals predate the promotion decision."
            )
        if package.rollback_campaign is not None:
            rollback_approval_times = tuple(
                item.created_at for item in package.rollback_approvals
            )
            if any(
                value
                < package.candidate_record.rollback_report.evaluated_at
                for value in rollback_approval_times
            ):
                raise LocalPolicyPromotionPackageError(
                    "Rollback approvals predate the rollback assessment."
                )

    @staticmethod
    def _verify_package_time(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        now = datetime.now(timezone.utc)
        if package.created_at > now + _MAX_CLOCK_SKEW:
            raise LocalPolicyPromotionPackageError(
                "Local-policy promotion package time must not be in the future beyond the bounded clock-skew window."
            )
        evidence_times = [
            package.acceptance_receipt.accepted_at,
            package.initial_record.created_at,
            package.candidate_record.created_at,
            package.promotion_campaign.updated_at,
            package.final_head.updated_at,
            *(item.created_at for item in package.local_policy_events),
            *(item.created_at for item in package.campaign_events),
            *(item.created_at for item in package.promotion_approvals),
        ]
        if package.rollback_campaign is not None:
            evidence_times.extend(
                [
                    package.rollback_campaign.updated_at,
                    *(item.created_at for item in package.rollback_approvals),
                ]
            )
        if package.created_at < max(evidence_times):
            raise LocalPolicyPromotionPackageError(
                "Local-policy promotion package predates complete evidence."
            )


__all__ = [
    "LocalPolicyPromotionPackageError",
    "LocalPolicyPromotionPackageManager",
    "LocalPolicyPromotionPackageManifest",
]
