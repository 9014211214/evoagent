from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.domain.models import AgentSnapshot, Task


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class AgentActionKind(str, Enum):
    TOOL_CALL = "tool_call"
    FINISH = "finish"


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("call_id", "tool_name")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("Tool call identifiers and names must be non-empty and NUL-free.")
        return normalized


class AgentAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: AgentActionKind
    tool_call: ToolCall | None = None
    final_output: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.kind == AgentActionKind.TOOL_CALL:
            if self.tool_call is None or self.final_output is not None:
                raise ValueError("A tool-call action requires only tool_call.")
        elif self.tool_call is not None or self.final_output is None:
            raise ValueError("A finish action requires only final_output.")
        return self

    @classmethod
    def call(cls, call_id: str, tool_name: str, **arguments: Any) -> "AgentAction":
        return cls(
            kind=AgentActionKind.TOOL_CALL,
            tool_call=ToolCall(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            ),
        )

    @classmethod
    def finish(cls, **final_output: Any) -> "AgentAction":
        return cls(kind=AgentActionKind.FINISH, final_output=final_output)


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    state_changed: bool = False
    state_fingerprint: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_error(self):
        if self.ok and (self.error_code is not None or self.error_message is not None):
            raise ValueError("Successful tool results cannot contain errors.")
        if not self.ok and not self.error_code:
            raise ValueError("Failed tool results require an error_code.")
        return self


class EnvironmentObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    episode_id: str
    step_index: int = Field(ge=0)
    state_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    available_tools: tuple[str, ...]
    last_tool_result: ToolResult | None = None

    @field_validator("available_tools")
    @classmethod
    def validate_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("Environment tools must be a non-empty unique tuple.")
        return value


class EnvironmentState(BaseModel):
    model_config = ConfigDict(frozen=True)

    state_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    public_state: dict[str, Any] = Field(default_factory=dict)


class VerificationContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    final_output: dict[str, Any]
    tool_results: tuple[ToolResult, ...]
    initial_state: EnvironmentState
    final_state: EnvironmentState
    steps_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    limit_exceeded: str | None = None


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    feedback: str
    evidence: tuple[str, ...] = ()
    safety_violations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_score(self):
        if self.passed and self.score != 1.0:
            raise ValueError("Passing verification must have score 1.0.")
        if not self.passed and self.score == 1.0:
            raise ValueError("Failed verification cannot have score 1.0.")
        return self


class RuntimeLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=12, gt=0)
    max_tool_calls: int = Field(default=8, ge=0)
    max_wall_seconds: float = Field(default=10.0, gt=0.0)


class AgentContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: Task
    snapshot: AgentSnapshot
    observation: EnvironmentObservation
    tool_results: tuple[ToolResult, ...] = ()
    step_index: int = Field(ge=0)


__all__ = [
    "AgentAction",
    "AgentActionKind",
    "AgentContext",
    "EnvironmentObservation",
    "EnvironmentState",
    "RuntimeLimits",
    "ToolCall",
    "ToolResult",
    "VerificationContext",
    "VerificationResult",
]
