from evoagent.domain.models import EvaluationResult
from evoagent.skills.models import SkillEvaluationDecision, SkillSpec


class SkillPromotionPolicy:
    def __init__(self, *, max_regressions: int = 0, min_score_delta: float = 0.0):
        self.max_regressions = max_regressions
        self.min_score_delta = min_score_delta

    def evaluate(
        self,
        base: SkillSpec,
        candidate: SkillSpec,
        base_result: EvaluationResult,
        candidate_result: EvaluationResult,
    ) -> SkillEvaluationDecision:
        if base.skill_id != candidate.skill_id:
            raise ValueError("Base and candidate must belong to the same skill.")
        if set(base_result.per_task) != set(candidate_result.per_task):
            raise ValueError("Frozen evaluations must contain identical task IDs.")

        regressions = sum(
            1
            for task_id, base_passed in base_result.per_task.items()
            if base_passed and not candidate_result.per_task[task_id]
        )
        score_delta = candidate_result.score - base_result.score
        promote = regressions <= self.max_regressions and score_delta > self.min_score_delta
        reason = (
            f"Promote: score delta={score_delta:.4f}, regressions={regressions}."
            if promote
            else f"Reject: score delta={score_delta:.4f}, regressions={regressions}."
        )
        return SkillEvaluationDecision(
            skill_id=base.skill_id,
            base_version=base.version,
            candidate_version=candidate.version,
            promote=promote,
            base_score=base_result.score,
            candidate_score=candidate_result.score,
            regression_count=regressions,
            reason=reason,
        )
