from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_ENV_NAME_PATTERN = r"^[A-Z_][A-Z0-9_]{0,127}$"


class ExecutionAdapter(str, Enum):
    HARBOR = "harbor"
    ML_INTERN = "ml_intern"
    RESOURCE2SKILL = "resource2skill"


class ExecutionBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_cost_usd: float = Field(default=0.0, ge=0.0)
    max_gpu_hours: float = Field(default=0.0, ge=0.0)
    max_wall_seconds: int = Field(default=3600, gt=0)
    max_trials: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=0, ge=0)

    def within(self, allowed: "ExecutionBudget") -> bool:
        return (
            self.max_cost_usd <= allowed.max_cost_usd
            and self.max_gpu_hours <= allowed.max_gpu_hours
            and self.max_wall_seconds <= allowed.max_wall_seconds
            and self.max_trials <= allowed.max_trials
            and self.max_iterations <= allowed.max_iterations
        )


class ExecutionInvocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    adapter: ExecutionAdapter
    command: tuple[str, ...]
    workspace: str
    required_environment_variables: tuple[str, ...] = ()
    network_access: bool = False
    upload: bool = False
    public: bool = False
    training: bool = False
    workspace_must_be_empty: bool = True
    budget: ExecutionBudget
    version_arguments: tuple[str, ...] = ("--version",)
    expected_version_pattern: str

    @field_validator("command", "version_arguments")
    @classmethod
    def validate_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() or "\x00" in item for item in value):
            raise ValueError("Execution argv must contain non-empty arguments without NUL bytes.")
        return value

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: str) -> str:
        from pathlib import Path

        path = Path(value)
        if "\x00" in value or not path.is_absolute() or ".." in path.parts:
            raise ValueError("Execution workspace must be a safe absolute path.")
        return str(path)

    @field_validator("required_environment_variables")
    @classmethod
    def validate_environment_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        import re

        if len(set(value)) != len(value):
            raise ValueError("Required environment-variable names must be unique.")
        if any(not re.fullmatch(_ENV_NAME_PATTERN, item) for item in value):
            raise ValueError("Required environment-variable name is invalid.")
        return tuple(sorted(value))

    @field_validator("expected_version_pattern")
    @classmethod
    def validate_version_pattern(cls, value: str) -> str:
        import re

        if not value.strip() or len(value) > 256:
            raise ValueError("Executable version pattern must be non-empty and bounded.")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError("Executable version pattern is invalid.") from exc
        return value

    @model_validator(mode="after")
    def validate_risk_flags(self):
        approved_probes = {
            ExecutionAdapter.HARBOR: ("--version",),
            ExecutionAdapter.ML_INTERN: ("--help",),
            ExecutionAdapter.RESOURCE2SKILL: ("--version",),
        }
        if self.version_arguments != approved_probes[self.adapter]:
            raise ValueError(
                f"{self.adapter.value} version probe arguments are fixed and side-effect-free."
            )
        if self.public and not (self.upload and self.network_access):
            raise ValueError("Public execution requires upload and network access.")
        if self.upload and not self.network_access:
            raise ValueError("Upload requires network access.")
        return self


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-execution-request-v1"] = (
        "evoagent-execution-request-v1"
    )
    request_id: str = Field(pattern=_SAFE_ID_PATTERN)
    requester_id: str = Field(pattern=_SAFE_ID_PATTERN)
    purpose: str
    issued_at: datetime
    expires_at: datetime
    invocation: ExecutionInvocation
    request_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Execution request times must include a timezone.")
        return value

    @field_validator("purpose")
    @classmethod
    def require_purpose(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Execution request purpose must not be empty.")
        return normalized

    @model_validator(mode="after")
    def validate_expiry(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("Execution request expiry must follow its issue time.")
        if self.expires_at - self.issued_at > timedelta(hours=24):
            raise ValueError("Execution request validity cannot exceed 24 hours.")
        return self


class ExecutionApproval(BaseModel):
    model_config = ConfigDict(frozen=True)

    approver_id: str = Field(pattern=_SAFE_ID_PATTERN)
    approved_at: datetime
    approved_request_hash: str = Field(pattern=_SHA256_PATTERN)
    reason: str

    @field_validator("approved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Approval time must include a timezone.")
        return value

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Execution approval reason must not be empty.")
        return normalized


class ExecutionAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-execution-authorization-v1"] = (
        "evoagent-execution-authorization-v1"
    )
    request: ExecutionRequest
    approvals: tuple[ExecutionApproval, ...]
    max_uses: Literal[1] = 1
    authorization_hash: str = Field(pattern=_SHA256_PATTERN)


class ExecutionPreflightResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    authorization_hash: str = Field(pattern=_SHA256_PATTERN)
    request_hash: str = Field(pattern=_SHA256_PATTERN)
    command_hash: str = Field(pattern=_SHA256_PATTERN)
    adapter: ExecutionAdapter
    executable_path: str
    executable_version_output: str
    workspace: str
    environment_presence: dict[str, bool]
    required_approvals: int = Field(gt=0)
    approver_ids: tuple[str, ...]
    network_access: bool
    upload: bool
    public: bool
    training: bool
    budget: ExecutionBudget
    ready: Literal[True] = True


class ExecutionUseStatus(str, Enum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionUseReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    authorization_hash: str = Field(pattern=_SHA256_PATTERN)
    request_id: str
    command_hash: str = Field(pattern=_SHA256_PATTERN)
    status: ExecutionUseStatus
    started_at: datetime
    completed_at: datetime | None = None
    return_code: int | None = None


__all__ = [
    "ExecutionAdapter",
    "ExecutionApproval",
    "ExecutionAuthorization",
    "ExecutionBudget",
    "ExecutionInvocation",
    "ExecutionPreflightResult",
    "ExecutionRequest",
    "ExecutionUseReceipt",
    "ExecutionUseStatus",
]
