from __future__ import annotations

from abc import ABC, abstractmethod

from evoagent.domain.models import Task
from evoagent.runtime.models import (
    AgentAction,
    AgentContext,
    EnvironmentObservation,
    EnvironmentState,
    ToolCall,
    VerificationContext,
    VerificationResult,
)


class ToolAgentPolicy(ABC):
    """Chooses the next observable action without exposing hidden reasoning."""

    @abstractmethod
    def next_action(self, context: AgentContext) -> AgentAction:
        raise NotImplementedError


class ResettableToolEnvironment(ABC):
    """One resettable, side-effect-contained tool episode."""

    @abstractmethod
    def reset(self, task: Task, *, seed: int) -> EnvironmentObservation:
        raise NotImplementedError

    @abstractmethod
    def execute(self, call: ToolCall) -> EnvironmentObservation:
        raise NotImplementedError

    @abstractmethod
    def inspect_state(self) -> EnvironmentState:
        raise NotImplementedError

    def close(self) -> None:
        """Release runtime resources without changing the verification result."""


class TaskVerifier(ABC):
    """Independent verifier over the task, final state and observable tool results."""

    @abstractmethod
    def verify(self, task: Task, context: VerificationContext) -> VerificationResult:
        raise NotImplementedError


__all__ = ["ResettableToolEnvironment", "TaskVerifier", "ToolAgentPolicy"]
