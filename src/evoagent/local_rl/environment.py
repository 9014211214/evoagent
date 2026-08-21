from __future__ import annotations

from dataclasses import dataclass

from evoagent.local_rl.models import (
    LocalRLAction,
    LocalRLEnvironmentContract,
    LocalRLTask,
    LocalRLTaskKind,
)
from evoagent.model_registry.models import canonical_sha256


@dataclass(frozen=True)
class LocalRLState:
    task_kind: LocalRLTaskKind
    inspected: bool = False
    terminal: bool = False

    @property
    def key(self) -> str:
        suffix = "inspected" if self.inspected else "uninspected"
        return f"{self.task_kind.value}:{suffix}"


@dataclass(frozen=True)
class LocalRLStepResult:
    state: LocalRLState
    reward: float
    done: bool
    success: bool
    unsafe: bool
    fingerprint: str


@dataclass(frozen=True)
class LocalRLEpisodeTrace:
    task: LocalRLTask
    actions: tuple[LocalRLAction, ...]
    state_keys: tuple[str, ...]
    action_probabilities: tuple[float, ...]
    rewards: tuple[float, ...]
    total_reward: float
    success: bool
    unsafe_action_count: int
    episode_hash: str

    @property
    def episode_steps(self) -> int:
        return len(self.actions)


class LocalSafeDocumentMDP:
    """A deterministic two-step MDP with no filesystem or external effects."""

    def __init__(self, contract: LocalRLEnvironmentContract):
        self.contract = contract

    def reset(self, task: LocalRLTask) -> LocalRLState:
        return LocalRLState(task_kind=task.kind)

    def step(
        self,
        state: LocalRLState,
        action: LocalRLAction,
        *,
        step_index: int,
    ) -> LocalRLStepResult:
        if state.terminal:
            raise ValueError("Cannot step a terminal Local RL state.")
        if step_index < 0 or step_index >= self.contract.max_episode_steps:
            raise ValueError("Local RL step index exceeds the frozen contract.")

        if not state.inspected:
            if action == LocalRLAction.INSPECT:
                next_state = LocalRLState(
                    task_kind=state.task_kind,
                    inspected=True,
                    terminal=False,
                )
                reward = self.contract.inspect_reward
                success = False
                unsafe = False
                done = False
            elif action == LocalRLAction.WRITE:
                next_state = LocalRLState(
                    task_kind=state.task_kind,
                    inspected=False,
                    terminal=True,
                )
                reward = self.contract.unsafe_write_reward
                success = False
                unsafe = True
                done = True
            else:
                next_state = LocalRLState(
                    task_kind=state.task_kind,
                    inspected=False,
                    terminal=True,
                )
                reward = self.contract.wrong_terminal_reward
                success = False
                unsafe = False
                done = True
        elif action == LocalRLAction.INSPECT:
            done = step_index + 1 >= self.contract.max_episode_steps
            next_state = LocalRLState(
                task_kind=state.task_kind,
                inspected=True,
                terminal=done,
            )
            reward = self.contract.redundant_inspect_reward
            success = False
            unsafe = False
        elif state.task_kind == LocalRLTaskKind.NORMAL:
            done = True
            success = action == LocalRLAction.WRITE
            unsafe = False
            reward = (
                self.contract.success_reward
                if success
                else self.contract.wrong_terminal_reward
            )
            next_state = LocalRLState(
                task_kind=state.task_kind,
                inspected=True,
                terminal=True,
            )
        else:
            done = True
            success = action == LocalRLAction.BLOCK
            unsafe = action == LocalRLAction.WRITE
            reward = (
                self.contract.success_reward
                if success
                else (
                    self.contract.unsafe_write_reward
                    if unsafe
                    else self.contract.wrong_terminal_reward
                )
            )
            next_state = LocalRLState(
                task_kind=state.task_kind,
                inspected=True,
                terminal=True,
            )

        if not done and step_index + 1 >= self.contract.max_episode_steps:
            done = True
            next_state = LocalRLState(
                task_kind=next_state.task_kind,
                inspected=next_state.inspected,
                terminal=True,
            )

        fingerprint = canonical_sha256(
            {
                "contract_hash": self.contract.contract_hash,
                "state": state.key,
                "action": action.value,
                "step_index": step_index,
                "next_state": next_state.key,
                "next_terminal": next_state.terminal,
                "reward": reward,
                "done": done,
                "success": success,
                "unsafe": unsafe,
            }
        )
        return LocalRLStepResult(
            state=next_state,
            reward=reward,
            done=done,
            success=success,
            unsafe=unsafe,
            fingerprint=fingerprint,
        )


__all__ = [
    "LocalRLEpisodeTrace",
    "LocalRLState",
    "LocalRLStepResult",
    "LocalSafeDocumentMDP",
]
