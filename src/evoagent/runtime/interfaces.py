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

    def observable_metadata(self, context: AgentContext) -> dict[str, object]:
        """Return bounded decision metadata suitable for the observable Trace.

        Policies must never place prompts, raw task inputs, hidden reasoning,
        credentials, or trajectories in this mapping. The default is empty so
        existing policies retain their original Trace contract.
        """

        return {}


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
