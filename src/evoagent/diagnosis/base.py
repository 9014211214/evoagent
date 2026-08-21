from abc import ABC, abstractmethod
from evoagent.domain.models import AgentSnapshot, ExecutionTrace, FailureReport

class FailureAttributor(ABC):
    @abstractmethod
    def diagnose(self, trace: ExecutionTrace, snapshot: AgentSnapshot) -> FailureReport:
        raise NotImplementedError
