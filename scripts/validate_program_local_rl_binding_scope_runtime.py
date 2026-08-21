from types import SimpleNamespace

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.local_rl import (
    ProgramLocalRLBindingError,
    ProgramLocalRLBindingManager,
)


manager = ProgramLocalRLBindingManager()

for layer, action in (
    (FailureLayer.ROUTER, EvolutionAction.UPDATE_ROUTER),
    (FailureLayer.CONTEXT, EvolutionAction.UPDATE_CONTEXT),
    (FailureLayer.VERIFIER, EvolutionAction.REPAIR_VERIFIER),
):
    ticket = SimpleNamespace(
        generation_plan=SimpleNamespace(
            intervention_layer=layer,
            intervention_action=action,
        )
    )
    manager._verify_intervention_scope(ticket)

for layer, action in (
    (FailureLayer.SKILL, EvolutionAction.UPDATE_SKILL),
    (FailureLayer.TOOL, EvolutionAction.REPAIR_TOOL),
    (FailureLayer.MODEL, EvolutionAction.TRAIN_MODEL),
    (FailureLayer.UNKNOWN, EvolutionAction.ESCALATE),
):
    ticket = SimpleNamespace(
        generation_plan=SimpleNamespace(
            intervention_layer=layer,
            intervention_action=action,
        )
    )
    try:
        manager._verify_intervention_scope(ticket)
    except ProgramLocalRLBindingError:
        pass
    else:
        raise SystemExit(
            f"Program-bound Local-RL accepted unsupported layer: {layer.value}"
        )

mismatch = SimpleNamespace(
    generation_plan=SimpleNamespace(
        intervention_layer=FailureLayer.CONTEXT,
        intervention_action=EvolutionAction.UPDATE_ROUTER,
    )
)
try:
    manager._verify_intervention_scope(mismatch)
except ProgramLocalRLBindingError:
    pass
else:
    raise SystemExit("Program-bound Local-RL accepted a layer/action mismatch")

print("Program-bound Local-RL intervention scope verified")
