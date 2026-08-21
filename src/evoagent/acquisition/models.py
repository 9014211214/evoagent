from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.skills.models import SkillSpec


class ResourceType(str, Enum):
    VIDEO = "video"
    REPOSITORY = "repository"
    ARTICLE = "article"
    REFERENCE_ARTIFACT = "reference_artifact"
    DEMONSTRATION_TRACE = "demonstration_trace"


class SourceTrustLevel(str, Enum):
    PUBLIC = "public"
    SYNTHETIC = "synthetic"
    VERIFIED = "verified"
    UNTRUSTED = "untrusted"


class DemonstrationAction(str, Enum):
    TOOL_CALL = "tool_call"
    UI_ACTION = "ui_action"
    OBSERVE = "observe"
    CONFIRM = "confirm"


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingCode(str, Enum):
    MISSING_SOURCE = "missing_source"
    MISSING_LICENSE = "missing_license"
    INVALID_CHECKSUM = "invalid_checksum"
    CONSENT_REQUIRED = "consent_required"
    SECRET_DETECTED = "secret_detected"
    MISSING_STEPS = "missing_steps"
    MISSING_SUCCESS_CRITERIA = "missing_success_criteria"
    SUCCESS_NOT_OBSERVED = "success_not_observed"
    AMBIGUOUS_COORDINATE_ACTION = "ambiguous_coordinate_action"
    MISSING_SEMANTIC_TARGET = "missing_semantic_target"
    UNTRUSTED_SOURCE = "untrusted_source"


class SourceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    resource_type: ResourceType
    uri: str
    checksum: str
    license_id: str
    consent_to_process: bool
    trust_level: SourceTrustLevel
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("checksum")
    @classmethod
    def validate_checksum_shape(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("Checksum must use sha256:<64 lowercase hex characters>.")
        digest = value.split(":", 1)[1]
        if any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Checksum must use lowercase hexadecimal characters.")
        return value


class DemonstrationStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=1)
    action: DemonstrationAction
    semantic_target: str | None = None
    tool_name: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_observation: str | None = None
    narration: str | None = None


class DemonstrationArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    demonstration_id: str
    task_intent: str
    sources: tuple[SourceArtifact, ...]
    steps: tuple[DemonstrationStep, ...]
    preconditions: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    failure_handling: tuple[str, ...] = ()
    observed_success: bool
    observed_success_evidence: tuple[str, ...] = ()

    @field_validator("steps")
    @classmethod
    def steps_must_be_ordered(cls, value: tuple[DemonstrationStep, ...]) -> tuple[DemonstrationStep, ...]:
        indexes = [item.index for item in value]
        if indexes != sorted(indexes) or len(indexes) != len(set(indexes)):
            raise ValueError("Demonstration steps must have unique ascending indexes.")
        return value


class CompilationFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: FindingCode
    severity: FindingSeverity
    message: str
    source_id: str | None = None
    step_index: int | None = None


class AcceptanceCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    kind: Literal["success", "failure"]
    description: str
    expected_conditions: tuple[str, ...]


class SkillAcquisitionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    demonstration_id: str
    skill: SkillSpec
    acceptance_cases: tuple[AcceptanceCase, ...]
    findings: tuple[CompilationFinding, ...]
    status: Literal["candidate"] = "candidate"


class SandboxAcquisitionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    sandbox_id: str
    passed: bool
    per_case: dict[str, bool]
    evidence: tuple[str, ...] = ()


class AcquisitionPromotionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    skill_id: str
    version: str
    registered: bool
    reason: str
