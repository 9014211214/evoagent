from __future__ import annotations

import math
import random
from collections.abc import Iterable

from evoagent.local_rl.builders import build_checkpoint
from evoagent.local_rl.models import (
    LocalRLAction,
    LocalRLCheckpointStatus,
    LocalRLEnvironmentContract,
    LocalPolicyCheckpoint,
)


class TabularSoftmaxPolicy:
    """Small numeric policy used to validate real rollout-driven optimization."""

    def __init__(
        self,
        *,
        state_keys: tuple[str, ...],
        actions: tuple[LocalRLAction, ...],
        logits: Iterable[Iterable[float]],
    ):
        rows = [list(row) for row in logits]
        if len(rows) != len(state_keys):
            raise ValueError("Policy state/logit dimensions differ.")
        if not actions:
            raise ValueError("Policy requires at least one action.")
        for row in rows:
            if len(row) != len(actions):
                raise ValueError("Policy action/logit dimensions differ.")
            if any(not math.isfinite(value) for value in row):
                raise ValueError("Policy logits must be finite.")
        self.state_keys = state_keys
        self.actions = actions
        self._logits = rows
        self._state_index = {key: index for index, key in enumerate(state_keys)}
        if len(self._state_index) != len(state_keys):
            raise ValueError("Policy state keys must be unique.")

    @classmethod
    def initial(
        cls,
        contract: LocalRLEnvironmentContract,
    ) -> "TabularSoftmaxPolicy":
        # P0 intentionally prefers direct WRITE before inspection and also
        # prefers WRITE in the protected inspected state. Training must change
        # both action choices through observable rewards.
        logits = (
            (0.0, 1.5, -0.5),
            (-0.5, 0.5, 0.0),
            (0.0, 1.5, -0.5),
            (-0.5, 0.5, 0.0),
        )
        return cls(
            state_keys=contract.state_keys,
            actions=contract.actions,
            logits=logits,
        )

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: LocalPolicyCheckpoint,
    ) -> "TabularSoftmaxPolicy":
        return cls(
            state_keys=checkpoint.state_keys,
            actions=checkpoint.actions,
            logits=checkpoint.logits,
        )

    def copy(self) -> "TabularSoftmaxPolicy":
        return TabularSoftmaxPolicy(
            state_keys=self.state_keys,
            actions=self.actions,
            logits=self._logits,
        )

    def logits_for(self, state_key: str) -> tuple[float, ...]:
        try:
            row = self._logits[self._state_index[state_key]]
        except KeyError as exc:
            raise KeyError(f"Unknown Local RL state: {state_key}") from exc
        return tuple(row)

    def probabilities(
        self,
        state_key: str,
        *,
        temperature: float = 1.0,
    ) -> tuple[float, ...]:
        if not math.isfinite(temperature) or temperature <= 0.0:
            raise ValueError("Policy temperature must be finite and positive.")
        logits = [value / temperature for value in self.logits_for(state_key)]
        maximum = max(logits)
        exponentials = [math.exp(value - maximum) for value in logits]
        total = sum(exponentials)
        if not math.isfinite(total) or total <= 0.0:
            raise ValueError("Policy softmax normalization is invalid.")
        probabilities = tuple(value / total for value in exponentials)
        if any(not math.isfinite(value) or value <= 0.0 for value in probabilities):
            raise ValueError("Policy softmax produced an invalid probability.")
        if abs(sum(probabilities) - 1.0) > 1e-12:
            raise ValueError("Policy probabilities do not sum to one.")
        return probabilities

    def sample_action(
        self,
        state_key: str,
        rng: random.Random,
    ) -> tuple[LocalRLAction, int, float, tuple[float, ...]]:
        probabilities = self.probabilities(state_key)
        draw = rng.random()
        cumulative = 0.0
        index = len(probabilities) - 1
        for candidate, probability in enumerate(probabilities):
            cumulative += probability
            if draw <= cumulative:
                index = candidate
                break
        return self.actions[index], index, probabilities[index], probabilities

    def greedy_action(
        self,
        state_key: str,
    ) -> tuple[LocalRLAction, int, float, tuple[float, ...]]:
        probabilities = self.probabilities(state_key)
        index = max(
            range(len(probabilities)),
            key=lambda candidate: (probabilities[candidate], -candidate),
        )
        return self.actions[index], index, probabilities[index], probabilities

    def entropy(self, state_key: str) -> float:
        probabilities = self.probabilities(state_key)
        return -sum(
            probability * math.log(max(probability, 1e-15))
            for probability in probabilities
        )

    def apply_gradient(
        self,
        gradient: dict[str, list[float]],
        *,
        learning_rate: float,
    ) -> None:
        if not math.isfinite(learning_rate) or learning_rate <= 0.0:
            raise ValueError("Policy learning rate must be finite and positive.")
        if set(gradient) != set(self.state_keys):
            raise ValueError("Policy gradient state set differs from the policy.")
        for state_key, values in gradient.items():
            if len(values) != len(self.actions):
                raise ValueError("Policy gradient action dimension differs.")
            if any(not math.isfinite(value) for value in values):
                raise ValueError("Policy gradient must be finite.")
            row = self._logits[self._state_index[state_key]]
            for index, value in enumerate(values):
                updated = row[index] + learning_rate * value
                if not math.isfinite(updated):
                    raise ValueError("Policy parameter update produced a non-finite value.")
                row[index] = updated

    def parameter_l2_distance(self, other: "TabularSoftmaxPolicy") -> float:
        if self.state_keys != other.state_keys or self.actions != other.actions:
            raise ValueError("Cannot compare policies with different contracts.")
        total = 0.0
        for state_key in self.state_keys:
            left = self.logits_for(state_key)
            right = other.logits_for(state_key)
            total += sum((a - b) ** 2 for a, b in zip(left, right, strict=True))
        return math.sqrt(total)

    def as_logits(self) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple(row) for row in self._logits)

    def checkpoint(
        self,
        *,
        checkpoint_id: str,
        run_id: str,
        iteration: int,
        parent_checkpoint_hash: str | None,
        status: LocalRLCheckpointStatus,
    ) -> LocalPolicyCheckpoint:
        return build_checkpoint(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            iteration=iteration,
            state_keys=self.state_keys,
            actions=self.actions,
            logits=self.as_logits(),
            parent_checkpoint_hash=parent_checkpoint_hash,
            status=status,
        )


__all__ = ["TabularSoftmaxPolicy"]
