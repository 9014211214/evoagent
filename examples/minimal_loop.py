from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evoagent.diagnosis.rule_based import RuleBasedFailureAttributor
from evoagent.domain.models import AgentSnapshot, Skill, Task
from evoagent.evaluation.evaluator import FrozenEvaluator, PromotionGate
from evoagent.evolution.controller import EvolutionController
from evoagent.evolution.skill_evolver import SkillEvolver
from evoagent.registry.snapshot_registry import SnapshotRegistry
from tests.support.synthetic_runtime import SyntheticRuntime

base_skill = Skill(skill_id="decision_skill", name="Decision Skill", version="0.1.0",
                   description="Process safe and unsafe requests.", rules=["accept_safe"])
a0 = AgentSnapshot(snapshot_id="A0", round_index=0, model_id="fixed-model-v0",
                   skills={base_skill.skill_id:base_skill})

runtime = SyntheticRuntime()
train_case = Task(task_id="evolution-case-unsafe", task_type="decision", input={"kind":"unsafe"})
held_out = [
    Task(task_id="safe-1", task_type="decision", input={"kind":"safe"}),
    Task(task_id="unsafe-1", task_type="decision", input={"kind":"unsafe"}),
    Task(task_id="unsafe-2", task_type="decision", input={"kind":"unsafe"}),
]

trace = runtime.run(train_case, a0)
report = RuleBasedFailureAttributor().diagnose(trace, a0)
decision = EvolutionController().decide(report)
print("Diagnosis:", report.layer.value, "Decision:", decision.action.value)

candidate = SkillEvolver().propose(base_skill, report)
candidate_skill = Skill.model_validate(candidate.payload)
candidate_skill.status = "stable"
temp = a0.model_copy(deep=True)
temp.snapshot_id = "A1-candidate"
temp.skills[candidate_skill.skill_id] = candidate_skill

evaluator = FrozenEvaluator(runtime)
before = evaluator.evaluate(a0, held_out)
after = evaluator.evaluate(temp, held_out)
print("A0:", before.score, before.per_task)
print("A1 candidate:", after.score, after.per_task)

registry = SnapshotRegistry()
registry.add(a0)
if PromotionGate().should_promote(before, after):
    a1 = registry.promote_skill_candidate(a0, candidate, new_snapshot_id="A1")
    print("PROMOTED:", a1.snapshot_id, a1.skills["decision_skill"].version)
else:
    print("REJECTED")
