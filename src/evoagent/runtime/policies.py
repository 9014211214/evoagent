from __future__ import annotations

from evoagent.runtime.interfaces import ToolAgentPolicy
from evoagent.runtime.models import AgentAction, AgentContext


class DocumentSkillPolicy(ToolAgentPolicy):
    """A fixed policy whose observable behavior is controlled by Skill rules."""

    INSPECT_RULE = "inspect_before_write"
    VERIFY_RULE = "verify_after_write"

    def next_action(self, context: AgentContext) -> AgentAction:
        try:
            rules = self._rules(context)
            target = context.task.input["target_path"]
            content = context.task.input["content"]
        except (KeyError, ValueError, TypeError) as exc:
            return AgentAction.finish(status="configuration_error", error=str(exc))
        if not isinstance(target, str) or not isinstance(content, str):
            return AgentAction.finish(
                status="configuration_error",
                error="target_path and content must be strings",
            )

        history = context.tool_results
        if not history:
            if self.INSPECT_RULE in rules:
                return AgentAction.call(
                    self._call_id(context, "inspect"),
                    "read_document",
                    path=target,
                )
            return AgentAction.call(
                self._call_id(context, "write"),
                "write_document",
                path=target,
                content=content,
            )

        last = history[-1]
        if last.tool_name == "read_document":
            is_verification_read = len(history) >= 2 and history[-2].tool_name == "write_document"
            if is_verification_read:
                if (
                    last.ok
                    and last.output.get("exists") is True
                    and last.output.get("content") == content
                ):
                    return AgentAction.finish(
                        status="completed",
                        path=target,
                        verified=True,
                    )
                return AgentAction.finish(
                    status="failed",
                    path=target,
                    error_code="verification_mismatch",
                )

            if not last.ok:
                return AgentAction.finish(
                    status="failed",
                    path=target,
                    error_code=last.error_code,
                )
            if last.output.get("exists") and last.output.get("protected"):
                return AgentAction.finish(
                    status="blocked",
                    path=target,
                    reason="protected_document",
                )
            return AgentAction.call(
                self._call_id(context, "write"),
                "write_document",
                path=target,
                content=content,
            )

        if last.tool_name == "write_document":
            if not last.ok:
                return AgentAction.finish(
                    status="failed",
                    path=target,
                    error_code=last.error_code,
                )
            if self.VERIFY_RULE in rules:
                return AgentAction.call(
                    self._call_id(context, "verify"),
                    "read_document",
                    path=target,
                )
            return AgentAction.finish(
                status="completed",
                path=target,
                verified=False,
            )

        return AgentAction.finish(
            status="failed",
            path=target,
            error_code="unsupported_observation",
        )

    @staticmethod
    def _rules(context: AgentContext) -> set[str]:
        active_id = context.snapshot.metadata.get("active_skill_id")
        if active_id is not None:
            if active_id not in context.snapshot.skills:
                raise ValueError("active_skill_id is not present in the snapshot")
            return set(context.snapshot.skills[active_id].rules)
        if len(context.snapshot.skills) != 1:
            raise ValueError("DocumentSkillPolicy requires exactly one active Skill")
        return set(next(iter(context.snapshot.skills.values())).rules)

    @staticmethod
    def _call_id(context: AgentContext, suffix: str) -> str:
        return f"{context.task.task_id}:{context.step_index}:{suffix}"


__all__ = ["DocumentSkillPolicy"]
