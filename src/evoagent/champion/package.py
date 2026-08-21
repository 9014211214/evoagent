from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

from evoagent.benchmark_evidence.package import (
    BenchmarkComparisonPackageManager,
)
from evoagent.campaigns import (
    ApprovalDecision,
    CampaignRisk,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
    fingerprint_payload,
)
from evoagent.champion.lifecycle import ChampionLifecycleService
from evoagent.champion.models import (
    ChampionDecisionAction,
    ChampionEventType,
    ChampionPromotionPolicy,
    ChampionRegistryCheckpoint,
    ChampionSelectionDecision,
    ChampionSnapshotRecord,
    ChampionVersionStatus,
)
from evoagent.champion.package_models import (
    ChampionDecisionPackageManifest,
)
from evoagent.champion.policy import ChampionPromotionGate
from evoagent.champion.repository import SQLiteChampionRegistry
from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_GENESIS_HASH = "0" * 64


class ChampionDecisionPackageError(ValueError):
    pass


class ChampionDecisionPackageManager:
    def build(
        self,
        *,
        package_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        benchmark_package,
        policy: ChampionPromotionPolicy,
        decision: ChampionSelectionDecision,
        promotion_campaign,
        approvals,
        champion_records,
        champion_events,
        champion_checkpoint,
        campaign_events,
        campaign_checkpoint,
        active_family_id: str,
        active_snapshot_id: str,
        active_revision: int,
    ) -> ChampionDecisionPackageManifest:
        provisional = ChampionDecisionPackageManifest(
            package_id=package_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            benchmark_package=benchmark_package,
            policy=policy,
            decision=decision,
            promotion_campaign=promotion_campaign,
            approvals=tuple(approvals),
            champion_records=tuple(champion_records),
            champion_events=tuple(champion_events),
            champion_checkpoint=champion_checkpoint,
            campaign_events=tuple(campaign_events),
            campaign_checkpoint=campaign_checkpoint,
            active_family_id=active_family_id,
            active_snapshot_id=active_snapshot_id,
            active_revision=active_revision,
            package_hash="0" * 64,
            synthetic_fixture=benchmark_package.synthetic_fixture,
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: ChampionDecisionPackageManifest) -> bool:
        payload = manifest.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if manifest.package_hash != canonical_sha256(payload):
            raise ChampionDecisionPackageError(
                "Champion decision package hash mismatch."
            )
        BenchmarkComparisonPackageManager().verify(manifest.benchmark_package)
        if manifest.synthetic_fixture != manifest.benchmark_package.synthetic_fixture:
            raise ChampionDecisionPackageError(
                "Champion package synthetic-fixture flag is not derived."
            )
        if manifest.policy != manifest.decision.policy:
            raise ChampionDecisionPackageError(
                "Champion package policy differs from the decision."
            )
        comparator_report = manifest.benchmark_package.same_model_cross_agent
        expected_decision = ChampionPromotionGate().evaluate(
            manifest.benchmark_package,
            policy=manifest.policy,
            decision_id=manifest.decision.decision_id,
            decision_actor_id=manifest.decision.decision_actor_id,
            decided_at=manifest.decision.decided_at,
            comparator_reports={
                comparator_report.anchor_run_id: comparator_report
            },
        )
        if expected_decision != manifest.decision:
            raise ChampionDecisionPackageError(
                "Packaged Champion decision differs from benchmark evidence."
            )
        if manifest.decision.action != ChampionDecisionAction.PROMOTE:
            raise ChampionDecisionPackageError(
                "Champion activation package requires a promotion decision."
            )
        runs = {
            item.evidence_id: item for item in manifest.benchmark_package.runs
        }
        selected_run = runs[manifest.decision.selected_run_id]
        selected_assessment = next(
            item
            for item in manifest.decision.assessments
            if item.run_id == manifest.decision.selected_run_id
        )
        self._verify_campaign(
            manifest,
            selected_run=selected_run,
            selected_assessment=selected_assessment,
        )
        self._verify_approvals(manifest)
        self._verify_records(manifest, selected_run=selected_run)
        self._verify_champion_audit(manifest)
        self._verify_campaign_audit(manifest)
        return True

    @staticmethod
    def _verify_campaign(
        manifest: ChampionDecisionPackageManifest,
        *,
        selected_run,
        selected_assessment,
    ) -> None:
        campaign = manifest.promotion_campaign
        decision = manifest.decision
        family_id = manifest.active_family_id
        expected_target = ChampionLifecycleService._target_key(
            family_id,
            decision.baseline_snapshot_id,
            decision.selected_snapshot_id,
        )
        expected_fingerprint = fingerprint_payload(
            ChampionLifecycleService._fingerprint_source(
                manifest.benchmark_package,
                decision,
                selected_run,
            )
        )
        expected_payload = ChampionLifecycleService._artifact_payload(
            manifest.benchmark_package,
            decision,
            selected_run,
            selected_assessment,
        )
        expected_metadata = ChampionLifecycleService._metadata(
            manifest.benchmark_package,
            decision,
            family_id=family_id,
        )
        if (
            campaign.campaign_type != CampaignType.CHAMPION_PROMOTION
            or campaign.state != CampaignState.COMPLETED
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != decision.decision_actor_id
            or campaign.target_key != expected_target
            or campaign.fingerprint != expected_fingerprint
            or campaign.candidate_ref
            != ChampionLifecycleService._candidate_ref(
                family_id,
                decision.selected_snapshot_id,
            )
            or campaign.metadata != expected_metadata
            or campaign.artifact_payload != expected_payload
        ):
            raise ChampionDecisionPackageError(
                "Champion promotion Campaign differs from exact decision evidence."
            )

    @staticmethod
    def _verify_approvals(
        manifest: ChampionDecisionPackageManifest,
    ) -> None:
        approvals = manifest.approvals
        actors = tuple(item.actor_id for item in approvals)
        if (
            len(approvals) != 2
            or len(set(actors)) != 2
            or any(
                item.campaign_id != manifest.promotion_campaign.campaign_id
                or item.decision != ApprovalDecision.APPROVE
                for item in approvals
            )
            or manifest.decision.decision_actor_id in actors
        ):
            raise ChampionDecisionPackageError(
                "Champion package requires exactly two independent approvals."
            )

    @staticmethod
    def _verify_records(
        manifest: ChampionDecisionPackageManifest,
        *,
        selected_run,
    ) -> None:
        by_snapshot = {
            item.snapshot_id: item for item in manifest.champion_records
        }
        expected_ids = {
            manifest.decision.baseline_snapshot_id,
            manifest.decision.selected_snapshot_id,
        }
        if set(by_snapshot) != expected_ids:
            raise ChampionDecisionPackageError(
                "Champion package must contain exactly baseline and selected snapshots."
            )
        baseline = by_snapshot[manifest.decision.baseline_snapshot_id]
        selected = by_snapshot[manifest.decision.selected_snapshot_id]
        baseline_run = next(
            item
            for item in manifest.benchmark_package.runs
            if item.evidence_id == manifest.decision.baseline_run_id
        )
        if (
            baseline.status != ChampionVersionStatus.RETIRED
            or baseline.parent_snapshot_id is not None
            or baseline.run_id != baseline_run.evidence_id
            or baseline.benchmark_evidence_hash != baseline_run.evidence_hash
            or baseline.benchmark_package_hash
            != manifest.benchmark_package.package_hash
            or selected.status != ChampionVersionStatus.CHAMPION
            or selected.parent_snapshot_id != baseline.snapshot_id
            or selected.run_id != selected_run.evidence_id
            or selected.benchmark_evidence_hash != selected_run.evidence_hash
            or selected.benchmark_package_hash
            != manifest.benchmark_package.package_hash
            or selected.decision_id != manifest.decision.decision_id
            or selected.decision_hash != manifest.decision.decision_hash
            or selected.policy_hash != manifest.policy.policy_hash
            or selected.campaign_id
            != manifest.promotion_campaign.campaign_id
            or manifest.active_family_id != selected.family_id
            or manifest.active_snapshot_id != selected.snapshot_id
            or manifest.active_revision != 1
        ):
            raise ChampionDecisionPackageError(
                "Champion Registry records differ from explicit activation state."
            )

    @staticmethod
    def _verify_champion_audit(
        manifest: ChampionDecisionPackageManifest,
    ) -> None:
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(
            manifest.champion_events,
            start=1,
        ):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
            ):
                raise ChampionDecisionPackageError(
                    "Packaged Champion audit chain is broken."
                )
            expected_hash = SQLiteChampionRegistry._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                family_id=event.family_id,
                snapshot_id=event.snapshot_id,
                from_snapshot_id=event.from_snapshot_id,
                to_snapshot_id=event.to_snapshot_id,
                reason=event.reason,
                payload=event.payload,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise ChampionDecisionPackageError(
                    "Packaged Champion audit event content was modified."
                )
            previous_hash = event.event_hash
        checkpoint = ChampionRegistryCheckpoint(
            event_count=len(manifest.champion_events),
            head_hash=previous_hash,
        )
        if checkpoint != manifest.champion_checkpoint:
            raise ChampionDecisionPackageError(
                "Champion audit events do not match the checkpoint."
            )
        expected_types = (
            ChampionEventType.REGISTERED,
            ChampionEventType.DECISION_STORED,
            ChampionEventType.CHALLENGER_ADMITTED,
            ChampionEventType.EVALUATED,
            ChampionEventType.AUTHORIZED,
            ChampionEventType.ACTIVATED,
        )
        if tuple(item.event_type for item in manifest.champion_events) != expected_types:
            raise ChampionDecisionPackageError(
                "Champion lifecycle events are missing, duplicated, or reordered."
            )

    @staticmethod
    def _verify_campaign_audit(
        manifest: ChampionDecisionPackageManifest,
    ) -> None:
        previous_hash = _GENESIS_HASH
        campaign_id = manifest.promotion_campaign.campaign_id
        for expected_sequence, event in enumerate(
            manifest.campaign_events,
            start=1,
        ):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
                or event.campaign_id != campaign_id
            ):
                raise ChampionDecisionPackageError(
                    "Packaged Campaign audit chain is broken or cross-contaminated."
                )
            expected_hash = SQLiteCampaignRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                campaign_id=event.campaign_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if expected_hash != event.event_hash:
                raise ChampionDecisionPackageError(
                    "Packaged Campaign audit event content was modified."
                )
            previous_hash = event.event_hash
        if (
            manifest.campaign_checkpoint.event_count
            != len(manifest.campaign_events)
            or manifest.campaign_checkpoint.head_hash != previous_hash
        ):
            raise ChampionDecisionPackageError(
                "Campaign audit events do not match the checkpoint."
            )
        expected_types = (
            "campaign_created",
            "candidate_attached",
            "campaign_transitioned",
            "campaign_transitioned",
            "approval_recorded",
            "approval_recorded",
            "campaign_transitioned",
        )
        if tuple(item.event_type for item in manifest.campaign_events) != expected_types:
            raise ChampionDecisionPackageError(
                "Champion Campaign events are missing, duplicated, or reordered."
            )

    def export_file(
        self,
        manifest: ChampionDecisionPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ChampionDecisionPackageError(
                "Champion package output must not be a symlink."
            )
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(manifest.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def load_file(self, path: str | Path) -> ChampionDecisionPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ChampionDecisionPackageError(
                "Champion decision package must be a regular non-symlink file."
            )
        try:
            manifest = ChampionDecisionPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ChampionDecisionPackageError(
                "Champion decision package is invalid."
            ) from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "ChampionDecisionPackageError",
    "ChampionDecisionPackageManager",
    "ChampionDecisionPackageManifest",
]
