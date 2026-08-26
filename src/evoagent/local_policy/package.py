from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent._io import atomic_temporary_path
from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApproval,
    CampaignAuditEvent,
    CampaignCheckpoint,
    CampaignRecord,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
    fingerprint_payload,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program_rl import (
    FullyAttestedProgramLocalRLBindingPackage,
    ProgramLocalRLAcceptanceManager,
    ProgramLocalRLAcceptanceReceipt,
    ProgramLocalRLTrustedAnchors,
)

from .builders import build_candidate_from_accepted_evidence
from .lifecycle import (
    promotion_artifact,
    promotion_candidate_ref,
    promotion_fingerprint_source,
    promotion_metadata,
    promotion_target_key,
    rollback_artifact,
    rollback_candidate_ref,
    rollback_fingerprint_source,
    rollback_metadata,
    rollback_target_key,
)
from .models import (
    InitialLocalPolicyManifest,
    LocalPolicyAuditEvent,
    LocalPolicyCandidateManifest,
    LocalPolicyEventType,
    LocalPolicyHead,
    LocalPolicyRegistryCheckpoint,
    LocalPolicyVersionRecord,
    LocalPolicyVersionStatus,
)
from .repository import SQLiteLocalPolicyRegistry


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_GENESIS_HASH = "0" * 64


class LocalPolicyPromotionPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-local-policy-promotion-package-v1"] = (
        "evoagent-local-policy-promotion-package-v1"
    )
    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    accepted_program_package: FullyAttestedProgramLocalRLBindingPackage
    trusted_anchors: ProgramLocalRLTrustedAnchors
    acceptance_receipt: ProgramLocalRLAcceptanceReceipt
    initial_record: LocalPolicyVersionRecord
    candidate_record: LocalPolicyVersionRecord
    final_head: LocalPolicyHead
    promotion_campaign: CampaignRecord
    promotion_approvals: tuple[CampaignApproval, ...]
    rollback_campaign: CampaignRecord | None = None
    rollback_approvals: tuple[CampaignApproval, ...] = ()
    local_policy_events: tuple[LocalPolicyAuditEvent, ...]
    local_policy_checkpoint: LocalPolicyRegistryCheckpoint
    campaign_events: tuple[CampaignAuditEvent, ...]
    campaign_checkpoint: CampaignCheckpoint
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    local_policy_pointer_mutation_only: Literal[True] = True
    foundation_model_weights_updated: Literal[False] = False
    production_activation_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "Local-policy promotion package time must include a timezone."
            )
        return value

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != canonical_sha256(payload):
            raise ValueError("Local-policy promotion package hash mismatch.")
        return self


class LocalPolicyPromotionPackageError(ValueError):
    pass


class LocalPolicyPromotionPackageManager:
    """Reverify accepted optimizer evidence, promotion, activation and rollback."""

    def build(
        self,
        *,
        package_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        accepted_program_package: FullyAttestedProgramLocalRLBindingPackage,
        trusted_anchors: ProgramLocalRLTrustedAnchors,
        acceptance_receipt: ProgramLocalRLAcceptanceReceipt,
        registry: SQLiteLocalPolicyRegistry,
        campaigns: SQLiteCampaignRepository,
        family_id: str,
        candidate_id: str,
    ) -> LocalPolicyPromotionPackageManifest:
        records = registry.list_versions(family_id)
        initial_records = tuple(
            item
            for item in records
            if isinstance(item.manifest, InitialLocalPolicyManifest)
        )
        if len(initial_records) != 1:
            raise LocalPolicyPromotionPackageError(
                "Local-policy package requires exactly one initial policy."
            )
        candidate = registry.get(family_id, candidate_id)
        if candidate.promotion_campaign_id is None:
            raise LocalPolicyPromotionPackageError(
                "Local-policy candidate lacks a promotion Campaign."
            )
        promotion = campaigns.get(candidate.promotion_campaign_id)
        rollback = (
            campaigns.get(candidate.rollback_campaign_id)
            if candidate.rollback_campaign_id is not None
            else None
        )
        payload = {
            "format_version": "evoagent-local-policy-promotion-package-v1",
            "package_id": package_id,
            "created_at": created_at,
            "framework_version": framework_version,
            "source_repository": source_repository,
            "source_commit": source_commit,
            "third_party_lock_hash": third_party_lock_hash,
            "accepted_program_package": accepted_program_package,
            "trusted_anchors": trusted_anchors,
            "acceptance_receipt": acceptance_receipt,
            "initial_record": initial_records[0],
            "candidate_record": candidate,
            "final_head": registry.head(family_id),
            "promotion_campaign": promotion,
            "promotion_approvals": tuple(
                campaigns.approvals(promotion.campaign_id)
            ),
            "rollback_campaign": rollback,
            "rollback_approvals": (
                tuple(campaigns.approvals(rollback.campaign_id))
                if rollback is not None
                else ()
            ),
            "local_policy_events": registry.events(),
            "local_policy_checkpoint": registry.checkpoint(),
            "campaign_events": campaigns.events(),
            "campaign_checkpoint": campaigns.checkpoint(),
            "local_policy_pointer_mutation_only": True,
            "foundation_model_weights_updated": False,
            "production_activation_performed": False,
            "production_deployment_performed": False,
            "external_rollout_performed_by_evoagent": False,
            "upload_performed": False,
            "official_benchmark_claimed": False,
        }
        package = LocalPolicyPromotionPackageManifest(
            **payload,
            package_hash=canonical_sha256(payload),
        )
        self.verify(package)
        return package

    @classmethod
    def verify(cls, package: LocalPolicyPromotionPackageManifest) -> bool:
        ProgramLocalRLAcceptanceManager.verify(
            package.accepted_program_package,
            package.trusted_anchors,
            package.acceptance_receipt,
        )
        cls._verify_records(package)
        cls._verify_promotion(package)
        cls._verify_rollback(package)
        cls._verify_local_policy_audit(package)
        cls._verify_campaign_audit(package)
        if package.created_at < max(
            package.final_head.updated_at,
            package.promotion_campaign.updated_at,
            *(
                [package.rollback_campaign.updated_at]
                if package.rollback_campaign is not None
                else []
            ),
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy promotion package predates lifecycle evidence."
            )
        if (
            package.local_policy_pointer_mutation_only is not True
            or package.foundation_model_weights_updated
            or package.production_activation_performed
            or package.production_deployment_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
            or package.official_benchmark_claimed
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy promotion package widens its local evidence boundary."
            )
        expected_hash = canonical_sha256(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise LocalPolicyPromotionPackageError(
                "Local-policy promotion package hash mismatch."
            )
        return True

    @staticmethod
    def _verify_records(package: LocalPolicyPromotionPackageManifest) -> None:
        initial = package.initial_record
        candidate_record = package.candidate_record
        if not isinstance(initial.manifest, InitialLocalPolicyManifest):
            raise LocalPolicyPromotionPackageError(
                "Local-policy initial record contains another manifest kind."
            )
        if not isinstance(candidate_record.manifest, LocalPolicyCandidateManifest):
            raise LocalPolicyPromotionPackageError(
                "Local-policy candidate record contains another manifest kind."
            )
        candidate = candidate_record.manifest
        expected_candidate = build_candidate_from_accepted_evidence(
            package.accepted_program_package,
            package.trusted_anchors,
            package.acceptance_receipt,
            family_id=candidate.family_id,
            candidate_id=candidate.candidate_id,
            base_policy_id=candidate.base_policy_id,
            created_by=candidate.created_by,
            created_at=candidate.created_at,
        )
        if candidate != expected_candidate:
            raise LocalPolicyPromotionPackageError(
                "Candidate manifest differs from independently accepted evidence."
            )
        if (
            initial.family_id != candidate.family_id
            or initial.policy_id != candidate.base_policy_id
            or initial.manifest.checkpoint_hash != candidate.base_checkpoint_hash
            or initial.manifest.optimizer_config_hash
            != candidate.optimizer_config_hash
            or candidate_record.parent_policy_id != initial.policy_id
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy parent/candidate checkpoint lineage differs."
            )
        if package.final_head.family_id != candidate.family_id:
            raise LocalPolicyPromotionPackageError(
                "Local-policy final head belongs to another family."
            )
        if package.rollback_campaign is None:
            if (
                initial.status != LocalPolicyVersionStatus.SUPERSEDED
                or candidate_record.status != LocalPolicyVersionStatus.ACTIVE
                or package.final_head.active_policy_id != candidate.candidate_id
                or package.final_head.revision != 1
            ):
                raise LocalPolicyPromotionPackageError(
                    "Promotion-only package has an invalid final pointer state."
                )
        else:
            if (
                initial.status != LocalPolicyVersionStatus.ACTIVE
                or candidate_record.status != LocalPolicyVersionStatus.ROLLED_BACK
                or package.final_head.active_policy_id != initial.policy_id
                or package.final_head.revision != 2
            ):
                raise LocalPolicyPromotionPackageError(
                    "Rollback package has an invalid final pointer state."
                )

    @classmethod
    def _verify_promotion(
        cls,
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        record = package.candidate_record
        candidate = record.manifest
        report = record.promotion_report
        decision = record.promotion_decision
        campaign = package.promotion_campaign
        if report is None or decision is None or not decision.promote:
            raise LocalPolicyPromotionPackageError(
                "Candidate lacks a passing promotion decision."
            )
        if (
            record.promotion_campaign_id != campaign.campaign_id
            or campaign.campaign_type != CampaignType.LOCAL_POLICY_PROMOTION
            or campaign.state != CampaignState.COMPLETED
            or campaign.target_key != promotion_target_key(candidate)
            or campaign.candidate_ref != promotion_candidate_ref(candidate)
            or campaign.fingerprint
            != fingerprint_payload(
                promotion_fingerprint_source(candidate, report, decision)
            )
            or campaign.artifact_payload
            != promotion_artifact(candidate, report, decision)
            or campaign.metadata != promotion_metadata(candidate, report, decision)
        ):
            raise LocalPolicyPromotionPackageError(
                "Promotion Campaign differs from exact candidate evidence."
            )
        prohibited = set(campaign.metadata["prohibited_actor_ids"])
        approvers = cls._verify_approvals(
            campaign,
            package.promotion_approvals,
            prohibited,
        )
        if (
            record.promotion_authorized_by is None
            or record.promotion_authorized_by in prohibited | approvers
        ):
            raise LocalPolicyPromotionPackageError(
                "Promotion Registry authorizer overlaps a governed role."
            )
        if (
            record.activated_by is None
            or record.activated_at is None
            or record.activated_by
            in prohibited | approvers | {record.promotion_authorized_by}
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy activation actor overlaps promotion governance."
            )

    @classmethod
    def _verify_rollback(
        cls,
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        record = package.candidate_record
        campaign = package.rollback_campaign
        if campaign is None:
            if (
                record.rollback_request is not None
                or record.rollback_report is not None
                or record.rollback_campaign_id is not None
                or package.rollback_approvals
            ):
                raise LocalPolicyPromotionPackageError(
                    "Promotion-only package contains partial rollback evidence."
                )
            return
        request = record.rollback_request
        report = record.rollback_report
        if request is None or report is None:
            raise LocalPolicyPromotionPackageError(
                "Rollback Campaign lacks request and assessment evidence."
            )
        prohibited = set(campaign.metadata.get("prohibited_actor_ids", []))
        if (
            record.rollback_campaign_id != campaign.campaign_id
            or campaign.campaign_type != CampaignType.LOCAL_POLICY_ROLLBACK
            or campaign.state != CampaignState.COMPLETED
            or campaign.target_key != rollback_target_key(request)
            or campaign.candidate_ref != rollback_candidate_ref(request)
            or campaign.fingerprint
            != fingerprint_payload(rollback_fingerprint_source(request, report))
            or campaign.artifact_payload != rollback_artifact(request, report)
            or campaign.metadata
            != rollback_metadata(request, report, tuple(sorted(prohibited)))
        ):
            raise LocalPolicyPromotionPackageError(
                "Rollback Campaign differs from exact rollback evidence."
            )
        approvers = cls._verify_approvals(
            campaign,
            package.rollback_approvals,
            prohibited,
        )
        if (
            record.rollback_authorized_by is None
            or record.rollback_authorized_by in prohibited | approvers
        ):
            raise LocalPolicyPromotionPackageError(
                "Rollback Registry authorizer overlaps a governed role."
            )
        if (
            record.rolled_back_by is None
            or record.rolled_back_at is None
            or record.rolled_back_by
            in prohibited | approvers | {record.rollback_authorized_by}
        ):
            raise LocalPolicyPromotionPackageError(
                "Rollback executor overlaps rollback governance."
            )

    @staticmethod
    def _verify_approvals(
        campaign: CampaignRecord,
        approvals: tuple[CampaignApproval, ...],
        prohibited: set[str],
    ) -> set[str]:
        actors = {item.actor_id for item in approvals}
        if (
            campaign.required_approvals != 2
            or len(approvals) != 2
            or len(actors) != 2
            or any(item.campaign_id != campaign.campaign_id for item in approvals)
            or any(item.decision != ApprovalDecision.APPROVE for item in approvals)
            or actors & prohibited
            or campaign.generated_by in actors
            or tuple(item.created_at for item in approvals)
            != tuple(sorted(item.created_at for item in approvals))
        ):
            raise LocalPolicyPromotionPackageError(
                "Local-policy Campaign approvals are incomplete or non-independent."
            )
        return actors

    @staticmethod
    def _verify_local_policy_audit(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        previous = _GENESIS_HASH
        events = package.local_policy_events
        for sequence, event in enumerate(events, start=1):
            if event.sequence != sequence or event.previous_hash != previous:
                raise LocalPolicyPromotionPackageError(
                    "Local-policy audit sequence or chain is broken."
                )
            expected = SQLiteLocalPolicyRegistry._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                family_id=event.family_id,
                policy_id=event.policy_id,
                from_policy_id=event.from_policy_id,
                to_policy_id=event.to_policy_id,
                reason=event.reason,
                metadata=event.metadata,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected:
                raise LocalPolicyPromotionPackageError(
                    "Local-policy audit event content was modified."
                )
            previous = event.event_hash
        checkpoint = LocalPolicyRegistryCheckpoint(
            event_count=len(events),
            head_hash=previous,
        )
        if checkpoint != package.local_policy_checkpoint:
            raise LocalPolicyPromotionPackageError(
                "Local-policy audit differs from its checkpoint."
            )
        expected_types = [
            LocalPolicyEventType.REGISTERED,
            LocalPolicyEventType.CANDIDATE_ADMITTED,
            LocalPolicyEventType.EVALUATED,
            LocalPolicyEventType.AUTHORIZED,
            LocalPolicyEventType.ACTIVATED,
        ]
        if package.rollback_campaign is not None:
            expected_types.extend(
                [
                    LocalPolicyEventType.ROLLBACK_SUBMITTED,
                    LocalPolicyEventType.ROLLBACK_AUTHORIZED,
                    LocalPolicyEventType.ROLLED_BACK,
                ]
            )
        if [item.event_type for item in events] != expected_types:
            raise LocalPolicyPromotionPackageError(
                "Local-policy audit lifecycle is missing, duplicated, or reordered."
            )
        initial = package.initial_record
        candidate = package.candidate_record
        expected_actors = [
            initial.manifest.created_by,
            candidate.manifest.created_by,
            candidate.promotion_decision.decided_by,
            candidate.promotion_authorized_by,
            candidate.activated_by,
        ]
        if package.rollback_campaign is not None:
            expected_actors.extend(
                [
                    candidate.rollback_report.evaluator_id,
                    candidate.rollback_authorized_by,
                    candidate.rolled_back_by,
                ]
            )
        if [item.actor_id for item in events] != expected_actors:
            raise LocalPolicyPromotionPackageError(
                "Local-policy audit actors differ from governed lifecycle roles."
            )
        expected_reasons = [
            "Initial local policy registered.",
            "Accepted local-RL checkpoint admitted as immutable candidate.",
            candidate.promotion_decision.reason,
            "Exact high-risk promotion Campaign authorized.",
            "Explicit local policy pointer activation completed.",
        ]
        if package.rollback_campaign is not None:
            expected_reasons.extend(
                [
                    "Independent local policy rollback assessment submitted.",
                    "Exact high-risk rollback Campaign authorized.",
                    "Explicit local policy pointer rollback completed.",
                ]
            )
        if [item.reason for item in events] != expected_reasons:
            raise LocalPolicyPromotionPackageError(
                "Local-policy audit reasons differ from governed semantics."
            )

    @staticmethod
    def _verify_campaign_audit(
        package: LocalPolicyPromotionPackageManifest,
    ) -> None:
        previous = _GENESIS_HASH
        campaign_ids = {package.promotion_campaign.campaign_id}
        if package.rollback_campaign is not None:
            campaign_ids.add(package.rollback_campaign.campaign_id)
        seen = set()
        for sequence, event in enumerate(package.campaign_events, start=1):
            if event.sequence != sequence or event.previous_hash != previous:
                raise LocalPolicyPromotionPackageError(
                    "Campaign audit sequence or chain is broken."
                )
            expected = SQLiteCampaignRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                campaign_id=event.campaign_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected:
                raise LocalPolicyPromotionPackageError(
                    "Campaign audit event content was modified."
                )
            if event.campaign_id in campaign_ids:
                seen.add(event.campaign_id)
            previous = event.event_hash
        checkpoint = CampaignCheckpoint(
            event_count=len(package.campaign_events),
            head_hash=previous,
        )
        if checkpoint != package.campaign_checkpoint:
            raise LocalPolicyPromotionPackageError(
                "Campaign audit differs from its checkpoint."
            )
        if seen != campaign_ids:
            raise LocalPolicyPromotionPackageError(
                "Campaign audit omits promotion or rollback lifecycle evidence."
            )

    def export_file(
        self,
        package: LocalPolicyPromotionPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(package)
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise LocalPolicyPromotionPackageError(
                "Local-policy package output must not be a symlink."
            )
        temporary = atomic_temporary_path(destination)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(package.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def load_file(self, path: str | Path) -> LocalPolicyPromotionPackageManifest:
        target = Path(path).expanduser()
        if target.is_symlink() or not target.is_file():
            raise LocalPolicyPromotionPackageError(
                "Local-policy package must be a regular non-symlink file."
            )
        try:
            package = LocalPolicyPromotionPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise LocalPolicyPromotionPackageError(
                f"Local-policy promotion package is invalid: {exc}"
            ) from exc
        self.verify(package)
        return package


__all__ = [
    "LocalPolicyPromotionPackageError",
    "LocalPolicyPromotionPackageManager",
    "LocalPolicyPromotionPackageManifest",
]
