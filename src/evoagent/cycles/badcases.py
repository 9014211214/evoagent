from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from evoagent.cycles.models import EvolutionCyclePolicy
from evoagent.domain.models import ExecutionTrace
from evoagent.traces.models import TraceTrustLevel


class BadCaseDisposition(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    QUARANTINE = "quarantine"


class BadCaseDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    disposition: BadCaseDisposition
    reason: str


class BadCaseDetector:
    def detect(
        self,
        trace: ExecutionTrace,
        *,
        trust_level: TraceTrustLevel,
        safety_flags: tuple[str, ...],
        policy: EvolutionCyclePolicy,
    ) -> BadCaseDecision:
        if policy.quarantine_untrusted and trust_level == TraceTrustLevel.UNTRUSTED:
            return BadCaseDecision(
                disposition=BadCaseDisposition.QUARANTINE,
                reason="Untrusted trace is excluded from automatic evolution.",
            )
        blocking = sorted(set(safety_flags).intersection(policy.blocking_safety_flags))
        if blocking:
            return BadCaseDecision(
                disposition=BadCaseDisposition.QUARANTINE,
                reason="Blocking safety flags: " + ", ".join(blocking),
            )
        if trace.verifier_passed:
            return BadCaseDecision(
                disposition=BadCaseDisposition.SUCCESS,
                reason="Verifier passed; no evolution action is required.",
            )
        return BadCaseDecision(
            disposition=BadCaseDisposition.FAILURE,
            reason="Verifier failed; counterfactual attribution is required.",
        )
