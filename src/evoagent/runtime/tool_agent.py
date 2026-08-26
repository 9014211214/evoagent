from __future__ import annotations

import math
from time import monotonic
from typing import Callable

from evoagent.domain.models import AgentSnapshot, ExecutionTrace, Task
from evoagent.runtime.base import AgentRuntime
from evoagent.runtime.interfaces import ResettableToolEnvironment, TaskVerifier, ToolAgentPolicy
from evoagent.runtime.models import (
    AgentActionKind,
    AgentContext,
    EnvironmentState,
    RuntimeLimits,
    ToolResult,
    VerificationContext,
    VerificationResult,
)


class ToolAgentRuntime(AgentRuntime):
    """Bounded observable tool loop compatible with the original AgentRuntime API."""

    def __init__(
        self,
        *,
        environment_factory: Callable[[], ResettableToolEnvironment],
        policy: ToolAgentPolicy,
        verifier: TaskVerifier,
        limits: RuntimeLimits | None = None,
        seed: int = 0,
    ):
        self.environment_factory = environment_factory
        self.policy = policy
        self.verifier = verifier
        self.limits = limits or RuntimeLimits()
        self.seed = seed

    def run(self, task: Task, snapshot: AgentSnapshot) -> ExecutionTrace:
        started = monotonic()
        policy_usage_before = self._policy_usage()
        environment: ResettableToolEnvironment | None = None
        events: list[dict] = []
        tool_results: list[ToolResult] = []
        final_output: dict | None = None
        limit_exceeded: str | None = None
        steps_used = 0
        initial_state = self._empty_state()
        final_state = initial_state
        verification = VerificationResult(
            passed=False,
            score=0.0,
            feedback="runtime_not_started",
            evidence=("runtime did not start",),
        )

        try:
            environment = self.environment_factory()
            observation = environment.reset(task, seed=self.seed)
            initial_state = environment.inspect_state()
            events.append(
                {
                    "event": "environment_reset",
                    "episode_id": observation.episode_id,
                    "seed": self.seed,
                    "state_fingerprint": initial_state.state_fingerprint,
                    "available_tools": list(observation.available_tools),
                }
            )

            for step_index in range(self.limits.max_steps):
                if monotonic() - started > self.limits.max_wall_seconds:
                    limit_exceeded = "wall_time"
                    break

                action = self.policy.next_action(
                    AgentContext(
                        task=task,
                        snapshot=snapshot,
                        observation=observation,
                        tool_results=tuple(tool_results),
                        step_index=step_index,
                    )
                )
                steps_used += 1
                events.append(
                    {
                        "event": "agent_action",
                        "step_index": step_index,
                        "action": action.model_dump(mode="json"),
                    }
                )
                policy_metadata = self.policy.observable_metadata(
                    AgentContext(
                        task=task,
                        snapshot=snapshot,
                        observation=observation,
                        tool_results=tuple(tool_results),
                        step_index=step_index,
                    )
                )
                if policy_metadata:
                    events.append(
                        {
                            "event": "policy_observation",
                            "step_index": step_index,
                            "metadata": policy_metadata,
                        }
                    )

                # Policy execution is part of the wall budget. Do not execute or
                # accept another action after the budget has expired.
                if monotonic() - started > self.limits.max_wall_seconds:
                    limit_exceeded = "wall_time"
                    break

                if action.kind == AgentActionKind.FINISH:
                    final_output = dict(action.final_output or {})
                    break

                if len(tool_results) >= self.limits.max_tool_calls:
                    limit_exceeded = "tool_calls"
                    break
                if action.tool_call is None:  # guarded by the Pydantic model
                    raise RuntimeError("Tool action is missing its tool call.")

                observation = environment.execute(action.tool_call)
                result = observation.last_tool_result
                if result is None:
                    raise RuntimeError("Environment returned no ToolResult for a tool call.")
                tool_results.append(result)
                events.append(
                    {
                        "event": "tool_result",
                        "step_index": step_index,
                        "result": result.model_dump(mode="json"),
                    }
                )

                if monotonic() - started > self.limits.max_wall_seconds:
                    limit_exceeded = "wall_time"
                    break
            else:
                limit_exceeded = "steps"

            if final_output is None:
                final_output = {
                    "status": "limit_exceeded",
                    "limit": limit_exceeded or "unknown",
                }
            final_state = environment.inspect_state()
            verification = self.verifier.verify(
                task,
                VerificationContext(
                    final_output=final_output,
                    tool_results=tuple(tool_results),
                    initial_state=initial_state,
                    final_state=final_state,
                    steps_used=steps_used,
                    tool_calls_used=len(tool_results),
                    limit_exceeded=limit_exceeded,
                ),
            )
        except Exception as exc:  # fail closed without persisting stack traces
            final_output = {
                "status": "runtime_error",
                "error_type": type(exc).__name__,
            }
            events.append(
                {
                    "event": "runtime_error",
                    "error_type": type(exc).__name__,
                }
            )
            if environment is not None:
                try:
                    final_state = environment.inspect_state()
                except Exception:
                    final_state = initial_state
            verification = VerificationResult(
                passed=False,
                score=0.0,
                feedback="runtime_error",
                evidence=(type(exc).__name__,),
            )
        finally:
            if environment is not None:
                environment.close()

        events.append(
            {
                "event": "verification",
                "passed": verification.passed,
                "score": verification.score,
                "feedback": verification.feedback,
                "evidence": list(verification.evidence),
                "safety_violations": list(verification.safety_violations),
                "initial_state_fingerprint": initial_state.state_fingerprint,
                "final_state_fingerprint": final_state.state_fingerprint,
            }
        )
        skill_id, skill_version = self._skill_binding(snapshot)
        elapsed = max(0.0, monotonic() - started)
        policy_usage_after = self._policy_usage()
        usage_delta = {
            key: policy_usage_after.get(key, 0.0) - policy_usage_before.get(key, 0.0)
            for key in set(policy_usage_before) | set(policy_usage_after)
        }
        if any(value < 0.0 for value in usage_delta.values()):
            raise RuntimeError("Policy observable usage counters moved backwards.")
        return ExecutionTrace(
            trace_id=f"trace:tool:{snapshot.snapshot_id}:{task.task_id}:{self.seed}",
            task=task,
            model_id=snapshot.model_id,
            skill_id=skill_id,
            skill_version=skill_version,
            observable_events=events,
            final_output=final_output or {"status": "runtime_error"},
            verifier_passed=verification.passed,
            verifier_feedback=verification.feedback,
            cost={
                "steps": float(steps_used),
                "tool_calls": float(len(tool_results)),
                "llm_tokens": usage_delta.get("llm_tokens", 0.0),
                "llm_requests": usage_delta.get("llm_requests", 0.0),
                "llm_prompt_tokens": usage_delta.get("llm_prompt_tokens", 0.0),
                "llm_completion_tokens": usage_delta.get(
                    "llm_completion_tokens", 0.0
                ),
                "wall_seconds": elapsed,
                "cost_usd": usage_delta.get("cost_usd", 0.0),
            },
        )

    def _policy_usage(self) -> dict[str, float]:
        usage = self.policy.observable_usage()
        if not isinstance(usage, dict):
            raise RuntimeError("Policy observable usage must be a mapping.")
        normalized: dict[str, float] = {}
        for key, value in usage.items():
            if not isinstance(key, str) or not isinstance(value, (int, float)):
                raise RuntimeError("Policy observable usage contains an invalid entry.")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0.0:
                raise RuntimeError("Policy observable usage must be non-negative.")
            normalized[key] = numeric
        return normalized

    @staticmethod
    def _skill_binding(snapshot: AgentSnapshot) -> tuple[str | None, str | None]:
        active_id = snapshot.metadata.get("active_skill_id")
        if isinstance(active_id, str) and active_id in snapshot.skills:
            skill = snapshot.skills[active_id]
            return skill.skill_id, skill.version
        if len(snapshot.skills) == 1:
            skill = next(iter(snapshot.skills.values()))
            return skill.skill_id, skill.version
        return None, None

    @staticmethod
    def _empty_state() -> EnvironmentState:
        return EnvironmentState(
            state_fingerprint="0" * 64,
            public_state={},
        )


__all__ = ["ToolAgentRuntime"]
