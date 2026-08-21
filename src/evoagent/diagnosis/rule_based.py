from evoagent.diagnosis.base import FailureAttributor
from evoagent.domain.models import AgentSnapshot, EvolutionAction, ExecutionTrace, FailureLayer, FailureReport

class RuleBasedFailureAttributor(FailureAttributor):
    def diagnose(self, trace: ExecutionTrace, snapshot: AgentSnapshot) -> FailureReport:
        if trace.verifier_passed:
            return FailureReport(trace_id=trace.trace_id, layer=FailureLayer.NONE, confidence=1.0,
                evidence=["Verifier passed."], recommended_action=EvolutionAction.NO_ACTION,
                summary="No failure detected.")
        feedback = trace.verifier_feedback.lower()
        if "missing_skill_rule" in feedback:
            return FailureReport(trace_id=trace.trace_id, layer=FailureLayer.SKILL, confidence=0.99,
                evidence=[trace.verifier_feedback], recommended_action=EvolutionAction.UPDATE_SKILL,
                summary="Verifier identified a missing rule in the selected skill.")
        return FailureReport(trace_id=trace.trace_id, layer=FailureLayer.UNKNOWN, confidence=0.3,
            evidence=[trace.verifier_feedback or "No structured failure evidence."],
            recommended_action=EvolutionAction.ESCALATE,
            summary="Root cause is not verified by the v0.1 diagnostic baseline.")
