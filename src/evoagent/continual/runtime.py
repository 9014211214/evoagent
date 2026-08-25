from __future__ import annotations

from pathlib import Path
from typing import Literal

from evoagent.domain.models import ExecutionTrace, Task
from evoagent.runtime import (
    AgentAction,
    AgentContext,
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    RuntimeLimits,
    ToolAgentPolicy,
    ToolAgentRuntime,
    VerificationContext,
    VerificationResult,
)

from .builders import to_runtime_snapshot
from .models import UnifiedAgentSnapshot


_CAPABILITY_PREFIX = "capability:"
_POLICY_PREFIX = "policy:"


class UnifiedDocumentPolicy(ToolAgentPolicy):
    """Route Skills, retrieve bounded Memory and apply one numeric policy.

    All three components influence actions inside the same Tool-Agent episode.
    The reference policy is deterministic during evaluation. Training may
    override only the first observable action for a bounded rollout.
    """

    INSPECT_RULE = "inspect_before_write"
    VERIFY_RULE = "verify_after_write"
    INSPECT_SKILL_BONUS = 3.0

    def __init__(
        self,
        snapshot: UnifiedAgentSnapshot,
        *,
        initial_action_override: Literal["inspect", "write"] | None = None,
    ):
        self.snapshot = snapshot
        self.initial_action_override = initial_action_override
        self._last_metadata: dict[tuple[str, int], dict[str, object]] = {}

    def next_action(self, context: AgentContext) -> AgentAction:
        self._validate_runtime_binding(context)
        try:
            target = context.task.input["target_path"]
            content = context.task.input["content"]
            selected, source, memory_ids = self._route(context.task)
            rules = self._rules(selected)
        except (KeyError, TypeError, ValueError) as exc:
            return AgentAction.finish(
                status="configuration_error",
                error_type=type(exc).__name__,
            )
        if not isinstance(target, str) or not isinstance(content, str):
            return AgentAction.finish(
                status="configuration_error",
                error_type="invalid_task_input",
            )

        history = context.tool_results
        selected_action: str | None = None
        policy_state = self._policy_state(context.task)
        if not history:
            selected_action = self.initial_action_override or self._initial_action(
                policy_state,
                rules,
            )
            if selected_action == "inspect":
                action = AgentAction.call(
                    self._call_id(context, "inspect"),
                    "read_document",
                    path=target,
                )
            else:
                action = AgentAction.call(
                    self._call_id(context, "write"),
                    "write_document",
                    path=target,
                    content=content,
                )
        else:
            last = history[-1]
            if last.tool_name == "read_document":
                verifying = len(history) >= 2 and history[-2].tool_name == "write_document"
                if verifying:
                    if (
                        last.ok
                        and last.output.get("exists") is True
                        and last.output.get("content") == content
                    ):
                        action = AgentAction.finish(
                            status="completed",
                            path=target,
                            verified=True,
                        )
                    else:
                        action = AgentAction.finish(
                            status="failed",
                            path=target,
                            error_code="verification_mismatch",
                        )
                elif not last.ok:
                    action = AgentAction.finish(
                        status="failed",
                        path=target,
                        error_code=last.error_code,
                    )
                elif last.output.get("exists") and last.output.get("protected"):
                    action = AgentAction.finish(
                        status="blocked",
                        path=target,
                        reason="protected_document",
                    )
                else:
                    action = AgentAction.call(
                        self._call_id(context, "write"),
                        "write_document",
                        path=target,
                        content=content,
                    )
            elif last.tool_name == "write_document":
                if not last.ok:
                    action = AgentAction.finish(
                        status="failed",
                        path=target,
                        error_code=last.error_code,
                    )
                elif self.VERIFY_RULE in rules:
                    action = AgentAction.call(
                        self._call_id(context, "verify"),
                        "read_document",
                        path=target,
                    )
                else:
                    action = AgentAction.finish(
                        status="completed",
                        path=target,
                        verified=False,
                    )
            else:
                action = AgentAction.finish(
                    status="failed",
                    path=target,
                    error_code="unsupported_observation",
                )

        self._last_metadata[(context.task.task_id, context.step_index)] = {
            "selected_skill_ids": selected,
            "router_source": source,
            "memory_record_ids": memory_ids,
            "policy_state": policy_state,
            "initial_policy_action": selected_action,
            "snapshot_hash": self.snapshot.snapshot_hash,
        }
        return action

    def observable_metadata(self, context: AgentContext) -> dict[str, object]:
        return dict(self._last_metadata.get((context.task.task_id, context.step_index), {}))

    def _route(self, task: Task) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
        tags = set(task.tags)
        matches = tuple(
            rule
            for rule in self.snapshot.router.rules
            if rule.task_type == task.task_type and set(rule.required_tags) <= tags
        )
        if matches:
            ranked = sorted(
                matches,
                key=lambda item: (-item.priority, -len(item.required_tags), item.rule_id),
            )
            best = ranked[0]
            tied = tuple(
                item
                for item in ranked
                if (item.priority, len(item.required_tags))
                == (best.priority, len(best.required_tags))
            )
            if len({item.skill_ids for item in tied}) > 1:
                raise ValueError("Ambiguous Router rules have equal precedence.")
            return best.skill_ids, f"rule:{best.rule_id}", ()

        capability = self._tag_value(task.tags, _CAPABILITY_PREFIX)
        if capability:
            for record in reversed(self.snapshot.memory.records):
                if record.task_type == task.task_type and record.capability_key == capability:
                    return (
                        record.selected_skill_ids,
                        "verified_memory",
                        (record.record_id,),
                    )
        return self.snapshot.router.default_skill_ids, "default", ()

    def _rules(self, selected_skill_ids: tuple[str, ...]) -> set[str]:
        by_id = {item.skill_id: item for item in self.snapshot.skills}
        if any(skill_id not in by_id for skill_id in selected_skill_ids):
            raise ValueError("Router selected a missing Skill.")
        rules: set[str] = set()
        for skill_id in selected_skill_ids:
            rules.update(by_id[skill_id].rules)
        return rules

    def _initial_action(self, state_key: str, rules: set[str]) -> str:
        try:
            row_index = self.snapshot.action_policy.state_keys.index(state_key)
        except ValueError as exc:
            raise ValueError(f"Action policy lacks state {state_key!r}.") from exc
        logits = list(self.snapshot.action_policy.logits[row_index])
        if self.INSPECT_RULE in rules:
            logits[0] += self.INSPECT_SKILL_BONUS
        best = max(range(len(logits)), key=lambda index: (logits[index], -index))
        return self.snapshot.action_policy.actions[best]

    @staticmethod
    def _tag_value(tags: list[str], prefix: str) -> str | None:
        values = tuple(item[len(prefix) :] for item in tags if item.startswith(prefix))
        if len(values) > 1:
            raise ValueError(f"Task has multiple {prefix!r} tags.")
        return values[0] if values else None

    def _policy_state(self, task: Task) -> str:
        return self._tag_value(task.tags, _POLICY_PREFIX) or task.task_type

    def _validate_runtime_binding(self, context: AgentContext) -> None:
        if context.snapshot.metadata.get("unified_snapshot_hash") != self.snapshot.snapshot_hash:
            raise ValueError("Runtime snapshot differs from the unified Agent snapshot.")

    @staticmethod
    def _call_id(context: AgentContext, suffix: str) -> str:
        return f"{context.task.task_id}:{context.step_index}:{suffix}"


class ContinualDocumentVerifier(DocumentTaskVerifier):
    """Base document verification plus explicit composition observations."""

    def verify(self, task: Task, context: VerificationContext) -> VerificationResult:
        base = super().verify(task, context)
        if not base.passed:
            return base
        required = task.input.get("required_observations", ())
        if not isinstance(required, (list, tuple)):
            return VerificationResult(
                passed=False,
                score=0.0,
                feedback="invalid_verifier_spec",
                evidence=("required_observations must be a list",),
            )
        names = tuple(item.tool_name for item in context.tool_results)
        missing: list[str] = []
        if "inspect_before_write" in required:
            if "read_document" not in names:
                missing.append("inspect_before_write")
            elif (
                "write_document" in names
                and names.index("read_document") > names.index("write_document")
            ):
                missing.append("inspect_before_write")
        if "verify_after_write" in required and not self._read_after_write(context):
            missing.append("verify_after_write")
        if missing:
            return VerificationResult(
                passed=False,
                score=0.0,
                feedback="required_observation_missing",
                evidence=tuple(f"missing={item}" for item in missing),
            )
        return base


class UnifiedDocumentAgentRuntime:
    """Execute one full frozen Agent snapshot in the local reference Environment."""

    def __init__(
        self,
        root: str | Path,
        *,
        limits: RuntimeLimits | None = None,
        seed: int = 0,
    ):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or RuntimeLimits(max_steps=8, max_tool_calls=5, max_wall_seconds=5.0)
        self.seed = seed

    def run(
        self,
        task: Task,
        snapshot: UnifiedAgentSnapshot,
        *,
        initial_action_override: Literal["inspect", "write"] | None = None,
    ) -> ExecutionTrace:
        policy = UnifiedDocumentPolicy(
            snapshot,
            initial_action_override=initial_action_override,
        )
        runtime = ToolAgentRuntime(
            environment_factory=lambda: LocalDocumentEnvironment(self.root / "episodes"),
            policy=policy,
            verifier=ContinualDocumentVerifier(),
            limits=self.limits,
            seed=self.seed,
        )
        return runtime.run(task, to_runtime_snapshot(snapshot))


def trace_policy_observation(trace: ExecutionTrace) -> dict[str, object]:
    observations = tuple(
        event.get("metadata", {})
        for event in trace.observable_events
        if event.get("event") == "policy_observation" and event.get("step_index") == 0
    )
    if len(observations) != 1:
        raise ValueError("Trace requires one initial policy observation.")
    return dict(observations[0])


__all__ = [
    "ContinualDocumentVerifier",
    "UnifiedDocumentAgentRuntime",
    "UnifiedDocumentPolicy",
    "trace_policy_observation",
]
