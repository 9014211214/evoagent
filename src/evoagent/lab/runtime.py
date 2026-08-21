from __future__ import annotations

from evoagent.domain.models import ExecutionTrace, Task
from evoagent.lab.models import ReferenceCaseResult, ReferenceEvaluationResult
from evoagent.skills.models import SkillSpec


class ReferenceDecisionRuntime:
    """Deterministic public/synthetic runtime for lifecycle verification.

    The base imported plan can complete the stable `safe` case. The injected
    `unsafe` case requires the evolved `reject_unsafe` rule.
    """

    SAFE_RULE = "follow_approved_skill_recorder_plan_in_order"
    EVOLVED_RULE = "reject_unsafe"

    def task(self, kind: str) -> Task:
        if kind not in {"safe", "unsafe"}:
            raise ValueError(f"Unsupported reference task kind: {kind}")
        return Task(
            task_id=f"reference:{kind}",
            task_type="reference-decision",
            input={"kind": kind},
            expected_outcome={"status": "accepted" if kind == "safe" else "rejected"},
            tags=("public", "synthetic", "frozen-evaluation"),
        )

    def execute(
        self,
        skill: SkillSpec,
        *,
        kind: str,
        model_id: str = "synthetic/reference-model-v0",
        trace_id: str | None = None,
    ) -> ExecutionTrace:
        task = self.task(kind)
        if kind == "safe":
            passed = self.SAFE_RULE in skill.rules
            expected = "accepted"
            observed = "accepted" if passed else "unresolved"
            feedback = "" if passed else f"missing_skill_rule: {self.SAFE_RULE}"
        else:
            passed = self.EVOLVED_RULE in skill.rules
            expected = "rejected"
            observed = "rejected" if passed else "unresolved"
            feedback = "" if passed else f"missing_skill_rule: {self.EVOLVED_RULE}"

        return ExecutionTrace(
            trace_id=trace_id or f"trace:{skill.skill_id}:{skill.version}:{kind}",
            task=task,
            model_id=model_id,
            skill_id=skill.skill_id,
            skill_version=skill.version,
            observable_events=[
                {
                    "event": "reference_skill_selected",
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                },
                {"event": "reference_case_classified", "kind": kind},
                {"event": "reference_outcome_observed", "status": observed},
            ],
            final_output={"status": observed, "expected": expected},
            verifier_passed=passed,
            verifier_feedback=feedback,
            cost={"llm_tokens": 0.0, "tool_calls": 0.0, "cost_usd": 0.0},
        )

    def evaluate(self, skill: SkillSpec) -> ReferenceEvaluationResult:
        cases = []
        for kind in ("safe", "unsafe"):
            trace = self.execute(skill, kind=kind)
            cases.append(
                ReferenceCaseResult(
                    task_id=trace.task.task_id,
                    passed=trace.verifier_passed,
                    expected=str(trace.final_output["expected"]),
                    observed=str(trace.final_output["status"]),
                )
            )
        passed = sum(item.passed for item in cases)
        return ReferenceEvaluationResult(
            skill_id=skill.skill_id,
            version=skill.version,
            cases=tuple(cases),
            score=passed / len(cases),
        )


__all__ = ["ReferenceDecisionRuntime"]
