from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent.skills.models import SkillEventType, SkillVersionRecord


class PersistentSkillEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str
    event_type: SkillEventType
    skill_id: str
    version: str
    from_version: str | None = None
    to_version: str | None = None
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor_id: str
    created_at: datetime
    previous_hash: str
    event_hash: str


class SkillRegistryCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str


class SkillRegistryBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-skill-state-v1"] = "evoagent-skill-state-v1"
    exported_at: datetime
    records: tuple[SkillVersionRecord, ...]
    active_versions: dict[str, str]
    active_revisions: dict[str, int]
    events: tuple[PersistentSkillEvent, ...]
    manifest_hash: str
