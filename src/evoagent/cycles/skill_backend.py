from __future__ import annotations

import re
from abc import ABC, abstractmethod

from evoagent.diagnosis.counterfactual import AttributionReport
from evoagent.domain.models import ExecutionTrace
from evoagent.skills.models import SkillPatch, SkillSpec


_RULE_PATTERN = re.compile(r"(?:^|\s)missing_skill_rule:\s*([A-Za-z0-9_.-]{1,80})(?:\s|$)")


class SkillPatchUnavailable(ValueError):
    pass


class SkillEvolutionBackend(ABC):
    @abstractmethod
    def propose(
        self,
        report: AttributionReport,
        trace: ExecutionTrace,
        base: SkillSpec,
    ) -> SkillPatch:
        raise NotImplementedError


class StructuredVerifierSkillBackend(SkillEvolutionBackend):
    """Minimal safe backend for machine-readable missing-rule evidence.

    A future LLM/SkillRL backend can implement the same interface, but it must
    still return an immutable patch and cannot promote it.
    """

    def propose(
        self,
        report: AttributionReport,
        trace: ExecutionTrace,
        base: SkillSpec,
    ) -> SkillPatch:
        match = _RULE_PATTERN.search(trace.verifier_feedback)
        if not match:
            raise SkillPatchUnavailable(
                "Verifier feedback does not contain a supported structured Skill patch."
            )
        rule = match.group(1)
        if rule in base.rules:
            raise SkillPatchUnavailable("Structured rule already exists in the active Skill.")
        return SkillPatch(
            add_rules=(rule,),
            evidence_trace_ids=(trace.trace_id,),
            generated_by="structured-verifier-skill-backend:v0.7",
        )
