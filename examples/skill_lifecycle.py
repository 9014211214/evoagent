from evoagent.domain.models import EvaluationResult
from evoagent.skills import (
    SkillCandidateBuilder,
    SkillPatch,
    SkillPromotionPolicy,
    SkillRegistry,
    SkillSpec,
)


def result(snapshot_id: str, outcomes: dict[str, bool]) -> EvaluationResult:
    passed = sum(outcomes.values())
    return EvaluationResult(
        snapshot_id=snapshot_id,
        total=len(outcomes),
        passed=passed,
        score=passed / len(outcomes),
        failed_task_ids=[task for task, ok in outcomes.items() if not ok],
        per_task=outcomes,
    )


base = SkillSpec(
    skill_id="decision_skill",
    name="Decision Skill",
    version="1.0.0",
    description="Handle safe cases.",
    rules=("accept_safe",),
)
candidate = SkillCandidateBuilder().propose(
    base,
    SkillPatch(add_rules=("reject_unsafe",), evidence_trace_ids=("trace:badcase:1",)),
    new_version="1.1.0",
)

registry = SkillRegistry()
registry.register_initial(base)
registry.add_candidate(candidate, parent_version=base.version, reason="verified skill failure")
decision = SkillPromotionPolicy().evaluate(
    base,
    candidate,
    result("A0", {"safe": True, "unsafe": False}),
    result("A1", {"safe": True, "unsafe": True}),
)
registry.promote(base.skill_id, candidate.version, decision)

print("active:", registry.active(base.skill_id).spec.version)
print("events:", [event.event_type.value for event in registry.events(base.skill_id)])
