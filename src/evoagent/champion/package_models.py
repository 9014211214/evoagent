from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.benchmark_evidence.package import (
    BenchmarkComparisonPackageManifest,
)
from evoagent.campaigns import (
    CampaignApproval,
    CampaignAuditEvent,
    CampaignCheckpoint,
    CampaignRecord,
)
from evoagent.champion.models import (
    ChampionAuditEvent,
    ChampionPromotionPolicy,
    ChampionRegistryCheckpoint,
    ChampionSelectionDecision,
    ChampionSnapshotRecord,
)


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class ChampionDecisionPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-champion-decision-package-v1"] = (
        "evoagent-champion-decision-package-v1"
    )
    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    benchmark_package: BenchmarkComparisonPackageManifest
    policy: ChampionPromotionPolicy
    decision: ChampionSelectionDecision
    promotion_campaign: CampaignRecord
    approvals: tuple[CampaignApproval, ...]
    champion_records: tuple[ChampionSnapshotRecord, ...]
    champion_events: tuple[ChampionAuditEvent, ...]
    champion_checkpoint: ChampionRegistryCheckpoint
    campaign_events: tuple[CampaignAuditEvent, ...]
    campaign_checkpoint: CampaignCheckpoint
    active_family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    active_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    active_revision: int = Field(ge=0)
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    synthetic_fixture: bool
    harbor_execution_performed_by_evoagent: Literal[False] = False
    external_model_call_performed_by_evoagent: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    checkpoint_downloaded_or_loaded: Literal[False] = False
    upload_performed: Literal[False] = False
    official_submission_performed: Literal[False] = False
    official_submission_accepted: Literal[False] = False
    production_deployment_performed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Champion decision package time must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_approval_event_binding(self):
        approval_events = tuple(
            event
            for event in self.campaign_events
            if event.event_type == "approval_recorded"
        )
        if len(approval_events) != len(self.approvals):
            raise ValueError(
                "Champion package approval records differ from Campaign audit events."
            )
        expected = sorted(
            (
                item.actor_id,
                item.decision.value,
                item.reason,
                item.created_at.isoformat(),
                item.campaign_id,
            )
            for item in self.approvals
        )
        observed = sorted(
            (
                event.actor_id,
                str(event.payload.get("decision")),
                str(event.payload.get("reason")),
                event.created_at.isoformat(),
                event.campaign_id,
            )
            for event in approval_events
        )
        if observed != expected:
            raise ValueError(
                "Champion package approval identity or reason was substituted."
            )
        return self


__all__ = ["ChampionDecisionPackageManifest"]
