from __future__ import annotations

from abc import ABC, abstractmethod

from evoagent.acquisition.models import (
    AcquisitionPromotionResult,
    FindingSeverity,
    SandboxAcquisitionResult,
    SkillAcquisitionCandidate,
)
from evoagent.skills.registry import SkillRegistry


class AcquisitionSandbox(ABC):
    @abstractmethod
    def evaluate(self, candidate: SkillAcquisitionCandidate) -> SandboxAcquisitionResult:
        raise NotImplementedError


class SyntheticAcquisitionSandbox(AcquisitionSandbox):
    def __init__(self, *, sandbox_id: str = "synthetic-acquisition-v1", outcomes=None):
        self.sandbox_id = sandbox_id
        self.outcomes = outcomes

    def evaluate(self, candidate: SkillAcquisitionCandidate) -> SandboxAcquisitionResult:
        expected_ids = [case.case_id for case in candidate.acceptance_cases]
        outcomes = (
            self.outcomes
            if self.outcomes is not None
            else {case_id: True for case_id in expected_ids}
        )
        per_case = {case_id: bool(outcomes.get(case_id, False)) for case_id in expected_ids}
        return SandboxAcquisitionResult(
            candidate_id=candidate.candidate_id,
            sandbox_id=self.sandbox_id,
            passed=bool(per_case) and all(per_case.values()),
            per_case=per_case,
            evidence=("Synthetic sandbox executed all generated acceptance cases.",),
        )


class InitialSkillAcquisitionGate:
    def evaluate_and_register(
        self,
        candidate: SkillAcquisitionCandidate,
        *,
        sandbox: AcquisitionSandbox,
        registry: SkillRegistry,
        allow_warnings: bool = False,
    ) -> AcquisitionPromotionResult:
        if any(item.severity == FindingSeverity.ERROR for item in candidate.findings):
            raise ValueError("Candidate contains blocking acquisition findings.")
        if (
            not allow_warnings
            and any(item.severity == FindingSeverity.WARNING for item in candidate.findings)
        ):
            raise ValueError("Candidate warnings require explicit review approval.")

        result = sandbox.evaluate(candidate)
        expected_ids = {case.case_id for case in candidate.acceptance_cases}
        if result.candidate_id != candidate.candidate_id:
            raise ValueError("Sandbox result does not belong to the candidate.")
        if set(result.per_case) != expected_ids:
            raise ValueError("Sandbox result must cover the exact generated acceptance cases.")
        if not result.passed or not all(result.per_case.values()):
            raise ValueError("Candidate failed sandbox acquisition evaluation.")

        registry.register_initial(
            candidate.skill,
            reason=(
                f"Initial Skill registered after static validation and sandbox "
                f"evaluation in {result.sandbox_id}."
            ),
        )
        return AcquisitionPromotionResult(
            candidate_id=candidate.candidate_id,
            skill_id=candidate.skill.skill_id,
            version=candidate.skill.version,
            registered=True,
            reason="Static validation and all sandbox acceptance cases passed.",
        )
