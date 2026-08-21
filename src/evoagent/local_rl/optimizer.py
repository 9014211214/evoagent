from __future__ import annotations

import hashlib
import math
import random
import statistics
import time
from dataclasses import dataclass

from evoagent.local_rl.builders import (
    build_iteration_metrics,
    build_training_result,
)
from evoagent.local_rl.environment import LocalRLEpisodeTrace, LocalSafeDocumentMDP
from evoagent.local_rl.models import (
    LocalRLCheckpointStatus,
    LocalRLError,
    LocalRLRunManifest,
    LocalRLTrainingResult,
    LocalRLTrainingUsage,
    LocalPolicyCheckpoint,
)
from evoagent.local_rl.policy import TabularSoftmaxPolicy
from evoagent.model_registry.models import canonical_sha256


@dataclass(frozen=True)
class _TransitionSample:
    state_key: str
    action_index: int
    old_probability: float
    advantage: float


class LocalGroupRelativePolicyOptimizer:
    """Real, bounded rollout optimization for a tiny local Agent policy.

    This is an independently authored group-relative clipped policy-gradient
    implementation. It intentionally does not claim canonical GRPO equivalence.
    """

    def train(self, manifest: LocalRLRunManifest) -> LocalRLTrainingResult:
        self._validate_static_budget(manifest)
        environment = LocalSafeDocumentMDP(manifest.environment)
        policy = TabularSoftmaxPolicy.initial(manifest.environment)
        initial_checkpoint = policy.checkpoint(
            checkpoint_id=f"{manifest.run_id}:checkpoint:0",
            run_id=manifest.run_id,
            iteration=0,
            parent_checkpoint_hash=None,
            status=LocalRLCheckpointStatus.INITIAL,
        )
        retained: list[LocalPolicyCheckpoint] = []
        metrics = []
        rollout_count = 0
        episode_step_count = 0
        parameter_updates = 0
        parent_hash = initial_checkpoint.checkpoint_hash
        started = time.monotonic()

        for iteration in range(1, manifest.budget.maximum_iterations + 1):
            if time.monotonic() - started > manifest.budget.maximum_wall_seconds:
                raise LocalRLError("Local RL wall-clock budget was exceeded.")
            before = policy.copy()
            old_checkpoint = policy.checkpoint(
                checkpoint_id=f"{manifest.run_id}:working:{iteration - 1}",
                run_id=manifest.run_id,
                iteration=iteration - 1,
                parent_checkpoint_hash=(
                    None if iteration == 1 else parent_hash
                ),
                status=(
                    LocalRLCheckpointStatus.INITIAL
                    if iteration == 1
                    else LocalRLCheckpointStatus.RETAINED
                ),
            )
            samples: list[_TransitionSample] = []
            episodes: list[LocalRLEpisodeTrace] = []
            all_advantages: list[float] = []
            entropy_values: list[float] = []

            for task_index, task in enumerate(manifest.training_tasks):
                group: list[LocalRLEpisodeTrace] = []
                for rollout_index in range(manifest.hyperparameters.group_size):
                    seed = self._episode_seed(
                        manifest.hyperparameters.seed,
                        iteration,
                        task_index,
                        rollout_index,
                    )
                    episode = self._rollout(
                        environment,
                        policy,
                        task,
                        seed=seed,
                        checkpoint_hash=old_checkpoint.checkpoint_hash,
                    )
                    group.append(episode)
                    episodes.append(episode)
                    rollout_count += 1
                    episode_step_count += episode.episode_steps
                    self._check_dynamic_budget(
                        manifest,
                        iterations=iteration,
                        rollouts=rollout_count,
                        episode_steps=episode_step_count,
                        parameter_updates=parameter_updates,
                    )

                rewards = [episode.total_reward for episode in group]
                if any(not math.isfinite(value) for value in rewards):
                    raise LocalRLError("Local RL rollout produced a non-finite reward.")
                mean_reward = statistics.fmean(rewards)
                variance = statistics.fmean(
                    (value - mean_reward) ** 2 for value in rewards
                )
                deviation = math.sqrt(max(variance, 0.0))
                for episode in group:
                    advantage = (
                        (episode.total_reward - mean_reward) / (deviation + 1e-8)
                        if deviation > 1e-12
                        else 0.0
                    )
                    if not math.isfinite(advantage):
                        raise LocalRLError(
                            "Local RL group-relative advantage is non-finite."
                        )
                    all_advantages.append(advantage)
                    for state_key, action_index, old_probability in zip(
                        episode.state_keys,
                        self._action_indices(policy, episode),
                        episode.action_probabilities,
                        strict=True,
                    ):
                        samples.append(
                            _TransitionSample(
                                state_key=state_key,
                                action_index=action_index,
                                old_probability=old_probability,
                                advantage=advantage,
                            )
                        )
                        entropy_values.append(policy.entropy(state_key))

            if not samples:
                raise LocalRLError("Local RL iteration produced no transition samples.")
            epoch_gradient_norms: list[float] = []
            clipped_samples = 0
            sample_opportunities = 0
            for _ in range(manifest.hyperparameters.update_epochs):
                gradient = {
                    state_key: [0.0] * len(policy.actions)
                    for state_key in policy.state_keys
                }
                for sample in samples:
                    probabilities = policy.probabilities(sample.state_key)
                    current_probability = probabilities[sample.action_index]
                    ratio = current_probability / sample.old_probability
                    if not math.isfinite(ratio) or ratio <= 0.0:
                        raise LocalRLError(
                            "Local RL importance ratio is invalid."
                        )
                    sample_opportunities += 1
                    clipped = (
                        sample.advantage >= 0.0
                        and ratio > 1.0 + manifest.hyperparameters.clip_epsilon
                    ) or (
                        sample.advantage < 0.0
                        and ratio < 1.0 - manifest.hyperparameters.clip_epsilon
                    )
                    if clipped:
                        clipped_samples += 1
                    else:
                        coefficient = sample.advantage * ratio
                        for action_index, probability in enumerate(probabilities):
                            gradient[sample.state_key][action_index] += coefficient * (
                                (1.0 if action_index == sample.action_index else 0.0)
                                - probability
                            )

                    entropy = -sum(
                        probability * math.log(max(probability, 1e-15))
                        for probability in probabilities
                    )
                    for action_index, probability in enumerate(probabilities):
                        entropy_gradient = -probability * (
                            math.log(max(probability, 1e-15)) + entropy
                        )
                        gradient[sample.state_key][action_index] += (
                            manifest.hyperparameters.entropy_coefficient
                            * entropy_gradient
                        )

                scale = 1.0 / len(samples)
                for state_key in policy.state_keys:
                    gradient[state_key] = [
                        value * scale for value in gradient[state_key]
                    ]
                gradient_norm = math.sqrt(
                    sum(
                        value * value
                        for values in gradient.values()
                        for value in values
                    )
                )
                if not math.isfinite(gradient_norm):
                    raise LocalRLError("Local RL gradient norm is non-finite.")
                if gradient_norm > manifest.hyperparameters.max_gradient_norm:
                    clip_scale = (
                        manifest.hyperparameters.max_gradient_norm / gradient_norm
                    )
                    for state_key in policy.state_keys:
                        gradient[state_key] = [
                            value * clip_scale for value in gradient[state_key]
                        ]
                    gradient_norm = manifest.hyperparameters.max_gradient_norm
                policy.apply_gradient(
                    gradient,
                    learning_rate=manifest.hyperparameters.learning_rate,
                )
                parameter_updates += 1
                self._check_dynamic_budget(
                    manifest,
                    iterations=iteration,
                    rollouts=rollout_count,
                    episode_steps=episode_step_count,
                    parameter_updates=parameter_updates,
                )
                epoch_gradient_norms.append(gradient_norm)

            checkpoint = policy.checkpoint(
                checkpoint_id=f"{manifest.run_id}:checkpoint:{iteration}",
                run_id=manifest.run_id,
                iteration=iteration,
                parent_checkpoint_hash=parent_hash,
                status=LocalRLCheckpointStatus.RETAINED,
            )
            delta = policy.parameter_l2_distance(before)
            if not math.isfinite(delta):
                raise LocalRLError("Local RL parameter delta is non-finite.")
            iteration_metrics = build_iteration_metrics(
                iteration=iteration,
                rollout_count=len(episodes),
                episode_steps=sum(item.episode_steps for item in episodes),
                mean_reward=statistics.fmean(
                    item.total_reward for item in episodes
                ),
                success_rate=statistics.fmean(
                    1.0 if item.success else 0.0 for item in episodes
                ),
                unsafe_action_rate=(
                    sum(item.unsafe_action_count for item in episodes)
                    / sum(item.episode_steps for item in episodes)
                ),
                mean_entropy=statistics.fmean(entropy_values),
                gradient_norm=statistics.fmean(epoch_gradient_norms),
                clipped_sample_fraction=(
                    clipped_samples / sample_opportunities
                    if sample_opportunities
                    else 0.0
                ),
                parameter_delta_l2=delta,
                episode_hashes_hash=canonical_sha256(
                    [item.episode_hash for item in episodes]
                ),
                advantages_hash=canonical_sha256(all_advantages),
                checkpoint_hash=checkpoint.checkpoint_hash,
            )
            metrics.append(iteration_metrics)
            if (
                iteration % manifest.hyperparameters.retained_checkpoint_interval == 0
                or iteration == manifest.budget.maximum_iterations
            ):
                retained.append(checkpoint)
                parent_hash = checkpoint.checkpoint_hash

        usage = LocalRLTrainingUsage(
            iterations=len(metrics),
            rollouts=rollout_count,
            episode_steps=episode_step_count,
            parameter_updates=parameter_updates,
        )
        if not usage.fits(manifest.budget):
            raise LocalRLError("Local RL final usage exceeds its frozen budget.")
        return build_training_result(
            run_id=manifest.run_id,
            manifest_hash=manifest.manifest_hash,
            initial_checkpoint=initial_checkpoint,
            retained_checkpoints=tuple(retained),
            iterations=tuple(metrics),
            usage=usage,
        )

    @staticmethod
    def _validate_static_budget(manifest: LocalRLRunManifest) -> None:
        iterations = manifest.budget.maximum_iterations
        rollouts = (
            iterations
            * len(manifest.training_tasks)
            * manifest.hyperparameters.group_size
        )
        maximum_steps = rollouts * manifest.environment.max_episode_steps
        updates = iterations * manifest.hyperparameters.update_epochs
        if rollouts > manifest.budget.maximum_rollouts:
            raise LocalRLError(
                "Local RL planned rollouts exceed the frozen budget."
            )
        if maximum_steps > manifest.budget.maximum_episode_steps:
            raise LocalRLError(
                "Local RL planned episode steps exceed the frozen budget."
            )
        if updates > manifest.budget.maximum_parameter_updates:
            raise LocalRLError(
                "Local RL planned parameter updates exceed the frozen budget."
            )

    @staticmethod
    def _check_dynamic_budget(
        manifest: LocalRLRunManifest,
        *,
        iterations: int,
        rollouts: int,
        episode_steps: int,
        parameter_updates: int,
    ) -> None:
        if iterations > manifest.budget.maximum_iterations:
            raise LocalRLError("Local RL iteration budget exceeded.")
        if rollouts > manifest.budget.maximum_rollouts:
            raise LocalRLError("Local RL rollout budget exceeded.")
        if episode_steps > manifest.budget.maximum_episode_steps:
            raise LocalRLError("Local RL episode-step budget exceeded.")
        if parameter_updates > manifest.budget.maximum_parameter_updates:
            raise LocalRLError("Local RL parameter-update budget exceeded.")

    @staticmethod
    def _episode_seed(
        seed: int,
        iteration: int,
        task_index: int,
        rollout_index: int,
    ) -> int:
        digest = hashlib.sha256(
            f"{seed}:{iteration}:{task_index}:{rollout_index}".encode("utf-8")
        ).hexdigest()
        return int(digest[:16], 16)

    @staticmethod
    def _action_indices(
        policy: TabularSoftmaxPolicy,
        episode: LocalRLEpisodeTrace,
    ) -> tuple[int, ...]:
        by_action = {action: index for index, action in enumerate(policy.actions)}
        return tuple(by_action[action] for action in episode.actions)

    @staticmethod
    def _rollout(
        environment: LocalSafeDocumentMDP,
        policy: TabularSoftmaxPolicy,
        task,
        *,
        seed: int,
        checkpoint_hash: str,
    ) -> LocalRLEpisodeTrace:
        rng = random.Random(seed)
        state = environment.reset(task)
        actions = []
        state_keys = []
        probabilities = []
        rewards = []
        transition_hashes = []
        success = False
        unsafe_count = 0
        for step_index in range(environment.contract.max_episode_steps):
            action, _, action_probability, _ = policy.sample_action(state.key, rng)
            state_keys.append(state.key)
            actions.append(action)
            probabilities.append(action_probability)
            result = environment.step(
                state,
                action,
                step_index=step_index,
            )
            rewards.append(result.reward)
            transition_hashes.append(result.fingerprint)
            unsafe_count += int(result.unsafe)
            success = result.success
            state = result.state
            if result.done:
                break
        total_reward = sum(rewards)
        payload = {
            "task_hash": task.task_hash,
            "seed": seed,
            "checkpoint_hash": checkpoint_hash,
            "state_keys": tuple(state_keys),
            "actions": tuple(action.value for action in actions),
            "action_probabilities": tuple(probabilities),
            "rewards": tuple(rewards),
            "transition_hashes": tuple(transition_hashes),
            "total_reward": total_reward,
            "success": success,
            "unsafe_action_count": unsafe_count,
        }
        return LocalRLEpisodeTrace(
            task=task,
            actions=tuple(actions),
            state_keys=tuple(state_keys),
            action_probabilities=tuple(probabilities),
            rewards=tuple(rewards),
            total_reward=total_reward,
            success=success,
            unsafe_action_count=unsafe_count,
            episode_hash=canonical_sha256(payload),
        )


__all__ = ["LocalGroupRelativePolicyOptimizer"]
