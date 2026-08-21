from __future__ import annotations

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.program_rl.adapter import ProgramLocalRLAdapter as _BaseAdapter


_LOCAL_RL_INTERVENTIONS = {
    FailureLayer.ROUTER: EvolutionAction.UPDATE_ROUTER,
    FailureLayer.CONTEXT: EvolutionAction.UPDATE_CONTEXT,
    FailureLayer.VERIFIER: EvolutionAction.REPAIR_VERIFIER,
}


class ProgramLocalRLAdapter(_BaseAdapter):
    """Final adapter for local Agent-policy optimization, not arbitrary repair."""

    def build_intent(self, *, generation, **kwargs):
        plan = generation.plan
        if plan is None:
            raise ValueError("Local-RL intent requires an immutable GenerationPlan.")
        expected_action = _LOCAL_RL_INTERVENTIONS.get(plan.intervention_layer)
        if expected_action is None:
            raise ValueError(
                "Program intervention layer is not eligible for local Agent-policy RL."
            )
        if plan.intervention_action != expected_action:
            raise ValueError(
                "Program local-RL intervention action differs from its policy layer."
            )
        return super().build_intent(generation=generation, **kwargs)


__all__ = ["ProgramLocalRLAdapter"]
