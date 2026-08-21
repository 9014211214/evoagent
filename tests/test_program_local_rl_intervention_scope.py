import pytest

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.program_rl import ProgramLocalRLAdapter
from tests.test_program_local_rl_adapter import _running_program


def _intent_arguments(package, generation, head, checkpoint, governed):
    return {
        "generation": generation,
        "head": head,
        "checkpoint": checkpoint,
        "signal": package.signal,
        "attribution": package.attribution,
        "governed_actor_ids": governed,
        "local_rl_run_id": "local-rl-run:scope-control",
        "optimizer_config_hash": "1" * 64,
        "training_task_set_hash": "2" * 64,
        "heldout_task_set_hash": "3" * 64,
        "created_by": "local-rl-intent-builder",
        "created_at": head.updated_at,
    }


def test_context_policy_generation_is_eligible_for_local_rl(tmp_path):
    package, generation, head, checkpoint, governed = _running_program(tmp_path)
    intent = ProgramLocalRLAdapter().build_intent(
        **_intent_arguments(package, generation, head, checkpoint, governed)
    )

    assert intent.intervention_layer == FailureLayer.CONTEXT
    assert intent.intervention_action == EvolutionAction.UPDATE_CONTEXT


@pytest.mark.parametrize(
    ("layer", "action"),
    (
        (FailureLayer.SKILL, EvolutionAction.UPDATE_SKILL),
        (FailureLayer.TOOL, EvolutionAction.REPAIR_TOOL),
        (FailureLayer.MODEL, EvolutionAction.TRAIN_MODEL),
        (FailureLayer.ENVIRONMENT, EvolutionAction.ESCALATE),
    ),
)
def test_non_policy_intervention_layers_cannot_use_local_rl(
    tmp_path,
    layer,
    action,
):
    package, generation, head, checkpoint, governed = _running_program(tmp_path)
    forged_plan = generation.plan.model_copy(
        update={
            "intervention_layer": layer,
            "intervention_action": action,
        }
    )
    forged_generation = generation.model_copy(update={"plan": forged_plan})

    with pytest.raises(ValueError, match="not eligible"):
        ProgramLocalRLAdapter().build_intent(
            **_intent_arguments(
                package,
                forged_generation,
                head,
                checkpoint,
                governed,
            )
        )


def test_policy_layer_action_mismatch_is_rejected(tmp_path):
    package, generation, head, checkpoint, governed = _running_program(tmp_path)
    forged_plan = generation.plan.model_copy(
        update={"intervention_action": EvolutionAction.UPDATE_ROUTER}
    )
    forged_generation = generation.model_copy(update={"plan": forged_plan})

    with pytest.raises(ValueError, match="action differs"):
        ProgramLocalRLAdapter().build_intent(
            **_intent_arguments(
                package,
                forged_generation,
                head,
                checkpoint,
                governed,
            )
        )
