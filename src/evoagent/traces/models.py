from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from evoagent.domain.models import ExecutionTrace


class TraceTrustLevel(str, Enum):
    SYNTHETIC = "synthetic"
    PUBLIC = "public"
    VERIFIED = "verified"
    UNTRUSTED = "untrusted"


class TraceEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    previous_hash: str
    record_hash: str
    created_at: datetime
    trace: ExecutionTrace
    source: str
    trust_level: TraceTrustLevel
    safety_flags: tuple[str, ...] = ()


class TraceCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_count: int = Field(ge=0)
    head_hash: str
