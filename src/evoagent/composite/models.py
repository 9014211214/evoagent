from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$"


class CompositeSnapshotStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class CompositeEventType(str, Enum):
    REGISTERED = "registered"
    COMMITTED = "committed"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _sorted_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates.")
    return tuple(sorted(values))


class SkillComponentBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version: str = Field(pattern=_VERSION_PATTERN)
    content_hash: str = Field(pattern=_SHA256_PATTERN)
    active_revision: int = Field(ge=0)


class LocalPolicyComponentBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    active_revision: int = Field(ge=0)


class CompositeSnapshotManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-composite-snapshot-v1"] = (
        "evoagent-composite-snapshot-v1"
    )
    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    parent_snapshot_id: str | None = Field(
        default=None,
        pattern=_SAFE_ID_PATTERN,
    )
    round_index: int = Field(ge=0)
    skill: SkillComponentBinding
    local_policy: LocalPolicyComponentBinding
    source_case_ids: tuple[str, ...] = ()
    source_decision_hashes: tuple[str, ...] = ()
    source_package_hashes: tuple[str, ...] = ()
    runtime_hash: str = Field(pattern=_SHA256_PATTERN)
    tool_contract_hash: str = Field(pattern=_SHA256_PATTERN)
    verifier_hash: str = Field(pattern=_SHA256_PATTERN)
    task_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    budget_hash: str = Field(pattern=_SHA256_PATTERN)
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    foundation_model_weights_updated: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False
    external_rollout_performed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite snapshot creation time")

    @field_validator("source_case_ids")
    @classmethod
    def validate_cases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value, "Composite source cases")

    @field_validator("source_decision_hashes", "source_package_hashes")
    @classmethod
    def validate_hash_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _sorted_unique(value, "Composite source hashes")
        if any(
            len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
            for item in normalized
        ):
            raise ValueError(
                "Composite source hashes must be lowercase SHA-256 values."
            )
        return normalized

    @model_validator(mode="after")
    def validate_manifest(self):
        if self.round_index == 0:
            if self.parent_snapshot_id is not None:
                raise ValueError(
                    "Initial composite snapshot must not have a parent."
                )
            if (
                self.source_case_ids
                or self.source_decision_hashes
                or self.source_package_hashes
            ):
                raise ValueError(
                    "Initial composite snapshot must not claim evolution evidence."
                )
        else:
            if self.parent_snapshot_id is None:
                raise ValueError(
                    "Evolved composite snapshot requires a direct parent."
                )
            if (
                not self.source_case_ids
                or not self.source_decision_hashes
                or not self.source_package_hashes
            ):
                raise ValueError(
                    "Evolved composite snapshot requires complete source evidence."
                )
        if self.parent_snapshot_id == self.snapshot_id:
            raise ValueError(
                "Composite snapshot cannot be its own parent."
            )
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        validate_safe_content(payload)
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("Composite snapshot manifest hash mismatch.")
        return self


class CompositeSnapshotRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    manifest: CompositeSnapshotManifest
    status: CompositeSnapshotStatus
    committed_by: str = Field(pattern=_SAFE_ID_PATTERN)
    committed_at: datetime

    @field_validator("committed_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite snapshot commit time")

    @model_validator(mode="after")
    def validate_record(self):
        if (
            self.lineage_id != self.manifest.lineage_id
            or self.snapshot_id != self.manifest.snapshot_id
        ):
            raise ValueError(
                "Composite record identity differs from its manifest."
            )
        if self.committed_at < self.manifest.created_at:
            raise ValueError(
                "Composite snapshot commit predates manifest creation."
            )
        validate_safe_content(self.model_dump(mode="json"))
        return self


class CompositeHead(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    active_snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    revision: int = Field(ge=0)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite head time")


class CompositeAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_type: CompositeEventType
    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    from_snapshot_id: str | None = Field(
        default=None,
        pattern=_SAFE_ID_PATTERN,
    )
    to_snapshot_id: str | None = Field(
        default=None,
        pattern=_SAFE_ID_PATTERN,
    )
    reason: str
    metadata: dict[str, Any]
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Composite audit time")


class CompositeRegistryCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lineage_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


__all__ = [
    "CompositeAuditEvent",
    "CompositeEventType",
    "CompositeHead",
    "CompositeRegistryCheckpoint",
    "CompositeSnapshotManifest",
    "CompositeSnapshotRecord",
    "CompositeSnapshotStatus",
    "LocalPolicyComponentBinding",
    "SkillComponentBinding",
]
