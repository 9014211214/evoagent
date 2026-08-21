from abc import ABC, abstractmethod
from evoagent.domain.models import AgentSnapshot, ExecutionTrace, Task

class AgentRuntime(ABC):
    @abstractmethod
    def run(self, task: Task, snapshot: AgentSnapshot) -> ExecutionTrace:
        raise NotImplementedError
