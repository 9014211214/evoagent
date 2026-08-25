from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent._io import atomic_temporary_path
from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApproval,
    CampaignAuditEvent,
    CampaignCheckpoint,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
    fingerprint_payload,
)
from evoagent.champion import ChampionDecisionPackageManager
from evoagent.champion.package_models import ChampionDecisionPackageManifest
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.release.lifecycle import ReleaseLifecycleService
from evoagent.release.models import (
    ReleaseAuditEvent,
    ReleaseDecisionAction,
    ReleaseEvidenceBatch,
    ReleaseEventType,
    ReleaseHead,
    ReleasePlan,
    ReleaseRegistryCheckpoint,
    ReleaseStageAssessment,
    ReleaseStageDecision,
    ReleaseState,
)
from evoagent.release.policy import ReleaseStageGate
from evoagent.release.repository import SQLiteReleaseRegistry


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENESIS_HASH = "0" * 64


class ReleaseEvidencePackageError(ValueError):
    pass


class ReleaseEvidencePackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-release-evidence-package-v1"] = (
        "evoagent-release-evidence-package-v1"
    )
    package_id: str
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    champion_package: ChampionDecisionPackageManifest
    plan: ReleasePlan
    batches: tuple[ReleaseEvidenceBatch, ...]
    assessments: tuple[ReleaseStageAssessment, ...]
    decisions: tuple[ReleaseStageDecision, ...]
    release_campaign: CampaignRecord
    release_approvals: tuple[CampaignApproval, ...]
    rollback_campaign: CampaignRecord | None = None
    rollback_approvals: tuple[CampaignApproval, ...] = ()
    final_head: ReleaseHead
    release_events: tuple[ReleaseAuditEvent, ...]
    release_checkpoint: ReleaseRegistryCheckpoint
    campaign_events: tuple[CampaignAuditEvent, ...]
    campaign_checkpoint: CampaignCheckpoint
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    synthetic_fixture: bool
    external_model_call_performed_by_evoagent: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    production_traffic_observed_by_evoagent: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollback_performed: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Release package time must include a timezone.")
        return value


class ReleaseEvidencePackageManager:
    def build(
        self,
        *,
        package_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        champion_package: ChampionDecisionPackageManifest,
        plan: ReleasePlan,
        batches: tuple[ReleaseEvidenceBatch, ...],
        assessments: tuple[ReleaseStageAssessment, ...],
        decisions: tuple[ReleaseStageDecision, ...],
        release_campaign: CampaignRecord,
        release_approvals: tuple[CampaignApproval, ...],
        rollback_campaign: CampaignRecord | None,
        rollback_approvals: tuple[CampaignApproval, ...],
        final_head: ReleaseHead,
        release_events: tuple[ReleaseAuditEvent, ...],
        release_checkpoint: ReleaseRegistryCheckpoint,
        campaign_events: tuple[CampaignAuditEvent, ...],
        campaign_checkpoint: CampaignCheckpoint,
    ) -> ReleaseEvidencePackageManifest:
        provisional = ReleaseEvidencePackageManifest(
            package_id=package_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            champion_package=champion_package,
            plan=plan,
            batches=batches,
            assessments=assessments,
            decisions=decisions,
            release_campaign=release_campaign,
            release_approvals=release_approvals,
            rollback_campaign=rollback_campaign,
            rollback_approvals=rollback_approvals,
            final_head=final_head,
            release_events=release_events,
            release_checkpoint=release_checkpoint,
            campaign_events=campaign_events,
            campaign_checkpoint=campaign_checkpoint,
            package_hash="0" * 64,
            synthetic_fixture=(plan.evidence_source.value == "synthetic_fixture"),
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: ReleaseEvidencePackageManifest) -> bool:
        payload = manifest.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if manifest.package_hash != canonical_sha256(payload):
            raise ReleaseEvidencePackageError("Release package hash mismatch.")
        ChampionDecisionPackageManager().verify(manifest.champion_package)
        ReleaseLifecycleService._validate_plan_binding(
            manifest.champion_package,
            manifest.plan,
        )
        if manifest.synthetic_fixture != (
            manifest.plan.evidence_source.value == "synthetic_fixture"
        ):
            raise ReleaseEvidencePackageError(
                "Release package synthetic-fixture flag is not derived."
            )
        self._verify_stage_evidence(manifest)
        self._verify_release_campaign(manifest)
        self._verify_rollback_campaign(manifest)
        self._verify_head(manifest)
        self._verify_release_audit(manifest)
        self._verify_campaign_audit(manifest)
        return True

    @staticmethod
    def _verify_stage_evidence(manifest: ReleaseEvidencePackageManifest) -> None:
        plan = manifest.plan
        if not (
            len(manifest.batches)
            == len(manifest.assessments)
            == len(manifest.decisions)
            == len(plan.stages)
        ):
            raise ReleaseEvidencePackageError(
                "Release package requires one batch, assessment, and decision per stage."
            )
        batches = {item.stage_id: item for item in manifest.batches}
        assessments = {item.stage_id: item for item in manifest.assessments}
        decisions = {item.stage_id: item for item in manifest.decisions}
        expected_stage_ids = {item.stage_id for item in plan.stages}
        if (
            set(batches) != expected_stage_ids
            or set(assessments) != expected_stage_ids
            or set(decisions) != expected_stage_ids
        ):
            raise ReleaseEvidencePackageError(
                "Release package stage evidence differs from the plan."
            )

        gate = ReleaseStageGate()
        for stage in plan.stages:
            batch = batches[stage.stage_id]
            expected_assessment = gate.assess(
                plan,
                batch,
                assessment_id=assessments[stage.stage_id].assessment_id,
            )
            if expected_assessment != assessments[stage.stage_id]:
                raise ReleaseEvidencePackageError(
                    "Packaged release assessment differs from admitted evidence."
                )
            expected_decision = gate.decide(
                plan,
                expected_assessment,
                decision_id=decisions[stage.stage_id].decision_id,
                decision_actor_id=decisions[stage.stage_id].decision_actor_id,
                decided_at=decisions[stage.stage_id].decided_at,
            )
            if expected_decision != decisions[stage.stage_id]:
                raise ReleaseEvidencePackageError(
                    "Packaged release decision differs from its assessment."
                )

        ordered = tuple(decisions[item.stage_id] for item in plan.stages)
        for index, decision in enumerate(ordered[:-1]):
            if (
                decision.action != ReleaseDecisionAction.ADVANCE
                or decision.next_stage_id != plan.stages[index + 1].stage_id
            ):
                raise ReleaseEvidencePackageError(
                    "Release stage sequence did not advance consecutively."
                )
        if ordered[-1].action not in {
            ReleaseDecisionAction.READY,
            ReleaseDecisionAction.ROLLBACK,
        }:
            raise ReleaseEvidencePackageError(
                "Release package must end in ready or rollback."
            )

    @staticmethod
    def _verify_release_campaign(manifest: ReleaseEvidencePackageManifest) -> None:
        plan = manifest.plan
        campaign = manifest.release_campaign
        expected_target = ReleaseLifecycleService._release_target(plan)
        expected_fingerprint = fingerprint_payload(
            ReleaseLifecycleService._release_fingerprint(
                manifest.champion_package,
                plan,
            )
        )
        if (
            campaign.campaign_type != CampaignType.CHAMPION_RELEASE
            or campaign.state != CampaignState.COMPLETED
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != plan.created_by
            or campaign.target_key != expected_target
            or campaign.fingerprint != expected_fingerprint
            or campaign.candidate_ref != f"release-plan:{plan.plan_id}"
            or campaign.metadata
            != ReleaseLifecycleService._release_metadata(
                manifest.champion_package,
                plan,
            )
            or campaign.artifact_payload
            != ReleaseLifecycleService._release_payload(
                manifest.champion_package,
                plan,
            )
        ):
            raise ReleaseEvidencePackageError(
                "Release Campaign differs from the exact plan evidence."
            )
        ReleaseEvidencePackageManager._verify_approvals(
            campaign,
            manifest.release_approvals,
            forbidden={plan.created_by},
        )

    @staticmethod
    def _verify_rollback_campaign(manifest: ReleaseEvidencePackageManifest) -> None:
        final_decision = next(
            item
            for item in manifest.decisions
            if item.stage_id == manifest.plan.stages[-1].stage_id
        )
        if final_decision.action == ReleaseDecisionAction.READY:
            if manifest.rollback_campaign is not None or manifest.rollback_approvals:
                raise ReleaseEvidencePackageError(
                    "Ready release package must not contain rollback governance."
                )
            return
        if manifest.rollback_campaign is None:
            raise ReleaseEvidencePackageError(
                "Rollback release package requires a rollback Campaign."
            )

        assessment = next(
            item
            for item in manifest.assessments
            if item.stage_id == final_decision.stage_id
        )
        batch = next(
            item for item in manifest.batches if item.stage_id == final_decision.stage_id
        )
        campaign = manifest.rollback_campaign
        expected_metadata = {
            "plan_id": manifest.plan.plan_id,
            "family_id": manifest.plan.family_id,
            "stage_id": final_decision.stage_id,
            "decision_hash": final_decision.decision_hash,
            "candidate_allocation_percent": assessment.candidate_traffic_percent,
            "production_deployment_performed": False,
        }
        if (
            campaign.campaign_type != CampaignType.CHAMPION_ROLLBACK
            or campaign.state != CampaignState.COMPLETED
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != final_decision.decision_actor_id
            or campaign.target_key
            != ReleaseLifecycleService._rollback_target(
                manifest.plan,
                final_decision,
            )
            or campaign.fingerprint
            != fingerprint_payload(
                ReleaseLifecycleService._rollback_fingerprint(
                    manifest.champion_package,
                    manifest.plan,
                    batch,
                    assessment,
                    final_decision,
                )
            )
            or campaign.candidate_ref
            != (
                f"release-rollback:{manifest.plan.plan_id}:"
                f"{final_decision.stage_id}"
            )
            or campaign.metadata != expected_metadata
            or campaign.artifact_payload
            != ReleaseLifecycleService._rollback_payload(
                manifest.champion_package,
                manifest.plan,
                batch,
                assessment,
                final_decision,
            )
        ):
            raise ReleaseEvidencePackageError(
                "Rollback Campaign differs from exact drift evidence."
            )
        ReleaseEvidencePackageManager._verify_approvals(
            campaign,
            manifest.rollback_approvals,
            forbidden={
                final_decision.decision_actor_id,
                final_decision.evidence_producer_id,
            },
        )

    @staticmethod
    def _verify_approvals(
        campaign: CampaignRecord,
        approvals: tuple[CampaignApproval, ...],
        *,
        forbidden: set[str],
    ) -> None:
        actors = tuple(item.actor_id for item in approvals)
        if (
            len(approvals) != 2
            or len(set(actors)) != 2
            or set(actors) & forbidden
            or any(
                item.campaign_id != campaign.campaign_id
                or item.decision != ApprovalDecision.APPROVE
                for item in approvals
            )
        ):
            raise ReleaseEvidencePackageError(
                "Release package requires exactly two independent approvals."
            )

    @staticmethod
    def _verify_head(manifest: ReleaseEvidencePackageManifest) -> None:
        head = manifest.final_head
        final_decision = next(
            item
            for item in manifest.decisions
            if item.stage_id == manifest.plan.stages[-1].stage_id
        )
        if (
            head.plan_id != manifest.plan.plan_id
            or head.family_id != manifest.plan.family_id
            or head.incumbent_snapshot_id != manifest.plan.incumbent_snapshot_id
            or head.challenger_snapshot_id != manifest.plan.challenger_snapshot_id
            or head.primary_snapshot_id != manifest.plan.incumbent_snapshot_id
            or head.release_campaign_id != manifest.release_campaign.campaign_id
        ):
            raise ReleaseEvidencePackageError(
                "Release final head differs from the frozen plan."
            )
        if final_decision.action == ReleaseDecisionAction.READY:
            if (
                head.state != ReleaseState.READY
                or head.active_stage_id != manifest.plan.stages[-1].stage_id
                or abs(
                    head.candidate_allocation_percent
                    - manifest.plan.stages[-1].candidate_traffic_percent
                )
                > 1e-12
                or head.rollback_campaign_id is not None
            ):
                raise ReleaseEvidencePackageError(
                    "Ready release head differs from final-stage evidence."
                )
        else:
            if (
                head.state != ReleaseState.ROLLED_BACK
                or head.active_stage_id is not None
                or head.candidate_allocation_percent != 0.0
                or manifest.rollback_campaign is None
                or head.rollback_campaign_id != manifest.rollback_campaign.campaign_id
            ):
                raise ReleaseEvidencePackageError(
                    "Rolled-back release head differs from rollback evidence."
                )

    @staticmethod
    def _verify_release_audit(manifest: ReleaseEvidencePackageManifest) -> None:
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(manifest.release_events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise ReleaseEvidencePackageError(
                    "Packaged release audit chain is broken."
                )
            if (
                event.plan_id != manifest.plan.plan_id
                or event.family_id != manifest.plan.family_id
            ):
                raise ReleaseEvidencePackageError(
                    "Packaged release audit references another plan or family."
                )
            expected_hash = SQLiteReleaseRegistry._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                plan_id=event.plan_id,
                family_id=event.family_id,
                stage_id=event.stage_id,
                reason=event.reason,
                payload=event.payload,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise ReleaseEvidencePackageError(
                    "Packaged release audit event content was modified."
                )
            previous_hash = event.event_hash

        checkpoint = ReleaseRegistryCheckpoint(
            event_count=len(manifest.release_events),
            head_hash=previous_hash,
        )
        if checkpoint != manifest.release_checkpoint:
            raise ReleaseEvidencePackageError(
                "Packaged release events do not match the checkpoint."
            )

        imported = {
            event.payload.get("batch_id"): event
            for event in manifest.release_events
            if event.event_type == ReleaseEventType.EVIDENCE_IMPORTED
        }
        assessed = {
            event.payload.get("assessment_id"): event
            for event in manifest.release_events
            if event.event_type == ReleaseEventType.STAGE_ASSESSED
        }
        decided = {
            event.payload.get("decision_id"): event
            for event in manifest.release_events
            if event.event_type == ReleaseEventType.DECISION_STORED
        }
        if (
            set(imported) != {item.batch_id for item in manifest.batches}
            or set(assessed) != {item.assessment_id for item in manifest.assessments}
            or set(decided) != {item.decision_id for item in manifest.decisions}
        ):
            raise ReleaseEvidencePackageError(
                "Release audit does not contain exact evidence lifecycle events."
            )
        for batch in manifest.batches:
            if imported[batch.batch_id].payload != {
                "batch_id": batch.batch_id,
                "evidence_hash": batch.evidence_hash,
                "source_file_sha256": batch.source_file_sha256,
            }:
                raise ReleaseEvidencePackageError(
                    "Release evidence import event differs from its batch."
                )
        for assessment in manifest.assessments:
            if assessed[assessment.assessment_id].payload != {
                "assessment_id": assessment.assessment_id,
                "assessment_hash": assessment.assessment_hash,
                "status": assessment.status.value,
            }:
                raise ReleaseEvidencePackageError(
                    "Release assessment event differs from its assessment."
                )
        for decision in manifest.decisions:
            if decided[decision.decision_id].payload != {
                "decision_id": decision.decision_id,
                "decision_hash": decision.decision_hash,
                "action": decision.action.value,
            }:
                raise ReleaseEvidencePackageError(
                    "Release decision event differs from its decision."
                )

        expected_types: list[ReleaseEventType] = [
            ReleaseEventType.PLAN_REGISTERED,
            ReleaseEventType.RELEASE_CAMPAIGN_BOUND,
            ReleaseEventType.RELEASE_AUTHORIZED,
            ReleaseEventType.STAGE_ACTIVATED,
        ]
        decisions_by_stage = {item.stage_id: item for item in manifest.decisions}
        for stage in manifest.plan.stages:
            expected_types.extend(
                (
                    ReleaseEventType.EVIDENCE_IMPORTED,
                    ReleaseEventType.STAGE_ASSESSED,
                    ReleaseEventType.DECISION_STORED,
                )
            )
            decision = decisions_by_stage[stage.stage_id]
            if decision.action == ReleaseDecisionAction.ADVANCE:
                expected_types.append(ReleaseEventType.STAGE_ADVANCED)
            elif decision.action == ReleaseDecisionAction.HOLD:
                expected_types.append(ReleaseEventType.HOLD_RECORDED)
            elif decision.action == ReleaseDecisionAction.READY:
                expected_types.append(ReleaseEventType.READY_RECORDED)
            elif decision.action == ReleaseDecisionAction.ROLLBACK:
                expected_types.extend(
                    (
                        ReleaseEventType.ROLLBACK_RECOMMENDED,
                        ReleaseEventType.ROLLBACK_CAMPAIGN_BOUND,
                        ReleaseEventType.ROLLED_BACK,
                    )
                )
        actual_types = [event.event_type for event in manifest.release_events]
        if actual_types != expected_types:
            raise ReleaseEvidencePackageError(
                "Release lifecycle event sequence is missing, duplicated, reordered, or truncated."
            )

    @staticmethod
    def _verify_campaign_audit(manifest: ReleaseEvidencePackageManifest) -> None:
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(manifest.campaign_events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise ReleaseEvidencePackageError(
                    "Packaged release Campaign audit chain is broken."
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
            if event.event_hash != expected_hash:
                raise ReleaseEvidencePackageError(
                    "Packaged release Campaign event content was modified."
                )
            previous_hash = event.event_hash

        checkpoint = CampaignCheckpoint(
            event_count=len(manifest.campaign_events),
            head_hash=previous_hash,
        )
        if checkpoint != manifest.campaign_checkpoint:
            raise ReleaseEvidencePackageError(
                "Release Campaign events do not match the checkpoint."
            )

        campaign_ids = {manifest.release_campaign.campaign_id}
        if manifest.rollback_campaign is not None:
            campaign_ids.add(manifest.rollback_campaign.campaign_id)
        if {event.campaign_id for event in manifest.campaign_events} != campaign_ids:
            raise ReleaseEvidencePackageError(
                "Release Campaign audit contains unrelated or missing Campaigns."
            )

        expected_campaign_types = (
            "campaign_created",
            "candidate_attached",
            "campaign_transitioned",
            "campaign_transitioned",
            "approval_recorded",
            "approval_recorded",
            "campaign_transitioned",
        )
        for campaign, approvals in (
            (manifest.release_campaign, manifest.release_approvals),
            (manifest.rollback_campaign, manifest.rollback_approvals),
        ):
            if campaign is None:
                continue
            campaign_events = [
                event
                for event in manifest.campaign_events
                if event.campaign_id == campaign.campaign_id
            ]
            if tuple(event.event_type for event in campaign_events) != expected_campaign_types:
                raise ReleaseEvidencePackageError(
                    "Release Campaign lifecycle event sequence is missing, duplicated, reordered, or truncated."
                )
            approval_events = [
                event
                for event in campaign_events
                if event.event_type == "approval_recorded"
            ]
            if len(approval_events) != len(approvals):
                raise ReleaseEvidencePackageError(
                    "Release Campaign approval events differ from approvals."
                )
            for event, approval in zip(approval_events, approvals, strict=True):
                if (
                    event.actor_id != approval.actor_id
                    or event.payload.get("decision") != approval.decision.value
                    or event.payload.get("reason") != approval.reason
                ):
                    raise ReleaseEvidencePackageError(
                        "Release Campaign approval identity was substituted."
                    )

    def export_file(
        self,
        manifest: ReleaseEvidencePackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ReleaseEvidencePackageError(
                "Release package output must not be a symlink."
            )
        temporary = atomic_temporary_path(destination)
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

    def load_file(self, path: str | Path) -> ReleaseEvidencePackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ReleaseEvidencePackageError(
                "Release evidence package must be a regular file."
            )
        try:
            manifest = ReleaseEvidencePackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ReleaseEvidencePackageError(
                f"Release evidence package is invalid: {exc}"
            ) from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "ReleaseEvidencePackageError",
    "ReleaseEvidencePackageManager",
    "ReleaseEvidencePackageManifest",
]
