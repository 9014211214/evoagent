from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.domain.models import Task
from evoagent.model_registry.models import canonical_sha256
from evoagent.runtime import RuntimeLimits

from .builders import build_action_policy
from .models import ActionPolicy, UnifiedAgentSnapshot
from .runtime import UnifiedDocumentAgentRuntime


_HASH = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class PolicyOptimizationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_id: str = Field(pattern=_SAFE_ID)
    iterations: int = Field(default=16, ge=1, le=256)
    group_size: int = Field(default=16, ge=4, le=256)
    learning_rate: float = Field(default=0.5, gt=0.0, le=2.0)
    gradient_clip: float = Field(default=1.0, gt=0.0, le=10.0)
    exploration_probability: float = Field(default=0.2, ge=0.0, le=1.0)
    maximum_rollouts: int = Field(default=2048, ge=1)
    maximum_episode_steps: int = Field(default=16_384, ge=1)
    seed: int = Field(default=17, ge=0)
    config_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_hash(self):
        if self.iterations * self.group_size > self.maximum_rollouts:
            raise ValueError("Policy optimization configuration exceeds rollout budget.")
        payload = self.model_dump(mode="json", exclude={"config_hash"})
        if self.config_hash != canonical_sha256(payload):
            raise ValueError("Policy optimization config hash mismatch.")
        return self


class PolicyOptimizationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    result_id: str = Field(pattern=_SAFE_ID)
    source_snapshot_hash: str = Field(pattern=_HASH)
    source_policy_hash: str = Field(pattern=_HASH)
    config_hash: str = Field(pattern=_HASH)
    training_task_hash: str = Field(pattern=_HASH)
    candidate_policy: ActionPolicy
    iteration_mean_rewards: tuple[float, ...]
    rollout_count: int = Field(gt=0)
    episode_steps: int = Field(gt=0)
    parameter_delta_l2: float = Field(gt=0.0)
    result_hash: str = Field(pattern=_HASH)
    external_model_called: Literal[False] = False
    foundation_model_weights_changed: Literal[False] = False

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Policy optimization result hash mismatch.")
        return self


def build_policy_optimization_config(
    config_id: str = "unified-policy-optimizer-v1",
    **overrides,
) -> PolicyOptimizationConfig:
    payload = {
        "config_id": config_id,
        "iterations": 16,
        "group_size": 16,
        "learning_rate": 0.5,
        "gradient_clip": 1.0,
        "exploration_probability": 0.2,
        "maximum_rollouts": 2048,
        "maximum_episode_steps": 16_384,
        "seed": 17,
    }
    payload.update(overrides)
    return PolicyOptimizationConfig(**payload, config_hash=canonical_sha256(payload))


class BoundedObservablePolicyOptimizer:
    """Group-relative numeric updates from actual unified-runtime rollouts."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def train(
        self,
        snapshot: UnifiedAgentSnapshot,
        training_tasks: tuple[Task, ...],
        *,
        config: PolicyOptimizationConfig,
        result_id: str,
    ) -> PolicyOptimizationResult:
        if not training_tasks:
            raise ValueError("Policy optimization requires training Tasks.")
        task_ids = [item.task_id for item in training_tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Policy optimization Task IDs must be unique.")
        states = tuple(self._state_key(task) for task in training_tasks)
        unknown = set(states) - set(snapshot.action_policy.state_keys)
        if unknown:
            raise ValueError(f"Action policy lacks training states: {sorted(unknown)}")

        rng = random.Random(config.seed)
        logits = [list(row) for row in snapshot.action_policy.logits]
        state_index = {
            key: index for index, key in enumerate(snapshot.action_policy.state_keys)
        }
        actions = snapshot.action_policy.actions
        rollout_count = 0
        episode_steps = 0
        mean_rewards: list[float] = []
        runtime = UnifiedDocumentAgentRuntime(
            self.root / "rollouts",
            seed=config.seed,
            limits=RuntimeLimits(max_steps=8, max_tool_calls=5, max_wall_seconds=5.0),
        )

        for iteration in range(1, config.iterations + 1):
            task = training_tasks[(iteration - 1) % len(training_tasks)]
            row_index = state_index[self._state_key(task)]
            probabilities = self._softmax(logits[row_index])
            samples: list[tuple[int, float]] = []
            for _ in range(config.group_size):
                if rollout_count >= config.maximum_rollouts:
                    raise RuntimeError("Policy optimizer exhausted its rollout budget.")
                if episode_steps + runtime.limits.max_steps > config.maximum_episode_steps:
                    raise RuntimeError(
                        "Policy optimizer lacks budget for another bounded episode."
                    )
                if rng.random() < config.exploration_probability:
                    action_index = rng.randrange(len(actions))
                else:
                    action_index = self._sample(probabilities, rng)
                trace = runtime.run(
                    task,
                    snapshot,
                    initial_action_override=actions[action_index],
                )
                safety = self._safety_count(trace)
                reward = (
                    (1.0 if trace.verifier_passed else -0.5)
                    - 2.0 * safety
                    - 0.01 * float(trace.cost.get("tool_calls", 0.0))
                )
                samples.append((action_index, reward))
                rollout_count += 1
                episode_steps += int(trace.cost.get("steps", 0.0))
            group_mean = sum(reward for _, reward in samples) / len(samples)
            mean_rewards.append(group_mean)
            gradient = [0.0 for _ in actions]
            for action_index, reward in samples:
                advantage = reward - group_mean
                for index, probability in enumerate(probabilities):
                    indicator = 1.0 if index == action_index else 0.0
                    gradient[index] += advantage * (indicator - probability)
            for index in range(len(gradient)):
                value = gradient[index] / len(samples)
                value = max(-config.gradient_clip, min(config.gradient_clip, value))
                logits[row_index][index] += config.learning_rate * value
        candidate = build_action_policy(
            snapshot.action_policy.policy_id,
            version=snapshot.action_policy.version + 1,
            iteration=snapshot.action_policy.iteration + config.iterations,
            state_keys=snapshot.action_policy.state_keys,
            logits=tuple(tuple(value for value in row) for row in logits),
            parent=snapshot.action_policy,
        )
        delta = math.sqrt(
            sum(
                (after - before) ** 2
                for before_row, after_row in zip(snapshot.action_policy.logits, candidate.logits)
                for before, after in zip(before_row, after_row)
            )
        )
        if delta <= 0.0:
            raise RuntimeError("Policy optimizer did not update numeric parameters.")
        task_hash = canonical_sha256(
            [item.model_dump(mode="json") for item in training_tasks]
        )
        payload = {
            "result_id": result_id,
            "source_snapshot_hash": snapshot.snapshot_hash,
            "source_policy_hash": snapshot.action_policy.policy_hash,
            "config_hash": config.config_hash,
            "training_task_hash": task_hash,
            "candidate_policy": candidate,
            "iteration_mean_rewards": tuple(mean_rewards),
            "rollout_count": rollout_count,
            "episode_steps": episode_steps,
            "parameter_delta_l2": delta,
            "external_model_called": False,
            "foundation_model_weights_changed": False,
        }
        return PolicyOptimizationResult(**payload, result_hash=canonical_sha256(payload))

    @staticmethod
    def _state_key(task: Task) -> str:
        values = tuple(item.split(":", 1)[1] for item in task.tags if item.startswith("policy:"))
        if len(values) > 1:
            raise ValueError("Training Task has multiple policy-state tags.")
        return values[0] if values else task.task_type

    @staticmethod
    def _softmax(logits: list[float]) -> tuple[float, ...]:
        maximum = max(logits)
        values = [math.exp(value - maximum) for value in logits]
        total = sum(values)
        return tuple(value / total for value in values)

    @staticmethod
    def _sample(probabilities: tuple[float, ...], rng: random.Random) -> int:
        threshold = rng.random()
        cumulative = 0.0
        for index, probability in enumerate(probabilities):
            cumulative += probability
            if threshold <= cumulative:
                return index
        return len(probabilities) - 1

    @staticmethod
    def _safety_count(trace) -> int:
        verification = tuple(
            item for item in trace.observable_events if item.get("event") == "verification"
        )
        if len(verification) != 1:
            raise RuntimeError("Policy rollout lacks one verification event.")
        return len(verification[0].get("safety_violations", ()))


__all__ = [
    "BoundedObservablePolicyOptimizer",
    "PolicyOptimizationConfig",
    "PolicyOptimizationResult",
    "build_policy_optimization_config",
]
