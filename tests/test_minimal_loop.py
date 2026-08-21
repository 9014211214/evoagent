from evoagent.diagnosis.rule_based import RuleBasedFailureAttributor
from evoagent.domain.models import AgentSnapshot, Skill, Task
from evoagent.evaluation.evaluator import FrozenEvaluator, PromotionGate
from evoagent.evolution.controller import EvolutionController
from evoagent.evolution.skill_evolver import SkillEvolver
from tests.support.synthetic_runtime import SyntheticRuntime

def make_snapshot():
    skill = Skill(skill_id="decision_skill", name="Decision Skill", version="0.1.0",
                  description="Synthetic decision skill", rules=["accept_safe"])
    return AgentSnapshot(snapshot_id="A0", round_index=0, model_id="fixed-model-v0",
                         skills={skill.skill_id: skill})

def test_skill_failure_is_diagnosed_and_candidate_improves_held_out():
    runtime = SyntheticRuntime()
    a0 = make_snapshot()
    failed_task = Task(task_id="badcase", task_type="decision", input={"kind":"unsafe"})
    trace = runtime.run(failed_task, a0)
    report = RuleBasedFailureAttributor().diagnose(trace, a0)
    decision = EvolutionController().decide(report)
    assert decision.action.value == "update_skill"

    candidate = SkillEvolver().propose(a0.skills["decision_skill"], report)
    candidate_skill = Skill.model_validate(candidate.payload)
    candidate_skill.status = "stable"

    a1 = a0.model_copy(deep=True)
    a1.snapshot_id = "A1-candidate"
    a1.skills[candidate_skill.skill_id] = candidate_skill

    eval_tasks = [
        Task(task_id="safe", task_type="decision", input={"kind":"safe"}),
        Task(task_id="unsafe", task_type="decision", input={"kind":"unsafe"}),
    ]
    evaluator = FrozenEvaluator(runtime)
    base_result = evaluator.evaluate(a0, eval_tasks)
    candidate_result = evaluator.evaluate(a1, eval_tasks)
    assert base_result.score == 0.5
    assert candidate_result.score == 1.0
    assert PromotionGate().should_promote(base_result, candidate_result)

def test_unknown_failure_is_not_auto_mutated():
    runtime = SyntheticRuntime()
    a0 = make_snapshot()
    task = Task(task_id="unknown", task_type="decision", input={"kind":"other"})
    report = RuleBasedFailureAttributor().diagnose(runtime.run(task,a0), a0)
    decision = EvolutionController().decide(report)
    assert report.layer.value == "unknown"
    assert decision.action.value == "escalate"
