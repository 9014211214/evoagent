from evoagent.domain.models import AgentSnapshot, ExecutionTrace, Task
from evoagent.runtime.base import AgentRuntime

class SyntheticRuntime(AgentRuntime):
    def run(self, task: Task, snapshot: AgentSnapshot) -> ExecutionTrace:
        skill = snapshot.skills["decision_skill"]
        kind = task.input["kind"]
        if kind == "safe":
            passed = "accept_safe" in skill.rules
            feedback = "" if passed else "missing_skill_rule: accept_safe"
        elif kind == "unsafe":
            passed = "reject_unsafe" in skill.rules
            feedback = "" if passed else "missing_skill_rule: reject_unsafe"
        else:
            passed = False
            feedback = "unknown_case"

        return ExecutionTrace(
            trace_id=f"trace:{snapshot.snapshot_id}:{task.task_id}",
            task=task, model_id=snapshot.model_id,
            skill_id=skill.skill_id, skill_version=skill.version,
            observable_events=[
                {"event":"skill_selected","skill_id":skill.skill_id,"version":skill.version},
                {"event":"input_classified","kind":kind},
            ],
            final_output={"decision":"accepted" if passed else "unresolved"},
            verifier_passed=passed, verifier_feedback=feedback,
            cost={"tool_calls":0.0,"llm_tokens":0.0},
        )
