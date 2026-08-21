from __future__ import annotations

import json

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.lab import ExecutableCrossLayerAttributionLab


EXPECTED_ACTIONS = {
    FailureLayer.SKILL: EvolutionAction.UPDATE_SKILL,
    FailureLayer.ROUTER: EvolutionAction.UPDATE_ROUTER,
    FailureLayer.TOOL: EvolutionAction.REPAIR_TOOL,
    FailureLayer.CONTEXT: EvolutionAction.UPDATE_CONTEXT,
    FailureLayer.VERIFIER: EvolutionAction.REPAIR_VERIFIER,
    FailureLayer.ENVIRONMENT: EvolutionAction.ESCALATE,
    FailureLayer.MODEL: EvolutionAction.TRAIN_MODEL,
}


def test_cross_layer_lab_dispatches_all_layers_and_repeats(tmp_path):
    result = ExecutableCrossLayerAttributionLab(tmp_path / "lab").run()

    assert result.repeatable is True
    assert result.external_execution_performed is False
    assert len(result.results) == 7
    assert [item.injected_layers[0] for item in result.results] == list(
        EXPECTED_ACTIONS
    )

    for item in result.results:
        layer = item.injected_layers[0]
        expected_action = EXPECTED_ACTIONS[layer]
        assert item.attribution.root_cause_layer == layer
        assert item.attribution.recommended_action == expected_action
        assert item.decision.action == expected_action
        assert item.supported_experiments == (
            {
                FailureLayer.SKILL: "replace_skill",
                FailureLayer.ROUTER: "force_router",
                FailureLayer.TOOL: "replay_tool",
                FailureLayer.CONTEXT: "complete_context",
                FailureLayer.VERIFIER: "oracle_verifier",
                FailureLayer.ENVIRONMENT: "reset_environment",
                FailureLayer.MODEL: "reference_model",
            }[layer],
        )
        assert len(item.counterfactual_trace_ids) == 7
        if layer == FailureLayer.ENVIRONMENT:
            assert item.attribution.actionable is False
            assert item.evolution_ticket is None
        else:
            assert item.attribution.actionable is True
            assert item.evolution_ticket is not None
            assert item.evolution_ticket.target_layer == layer
            assert item.evolution_ticket.proposed_action == expected_action
            assert item.evolution_ticket.evidence_trace_ids == [item.baseline_trace_id]
            assert item.evolution_ticket.required_evaluations == [
                "held_out",
                "regression",
                "safety",
            ]

    conflict = result.conflict
    assert conflict.injected_layers == (FailureLayer.SKILL, FailureLayer.ROUTER)
    assert set(conflict.supported_experiments) == {"replace_skill", "force_router"}
    assert conflict.attribution.root_cause_layer == FailureLayer.UNKNOWN
    assert conflict.attribution.actionable is False
    assert conflict.decision.action == EvolutionAction.ESCALATE
    assert conflict.evolution_ticket is None


def test_dispatch_targets_are_bounded_and_model_is_only_a_request(tmp_path):
    result = ExecutableCrossLayerAttributionLab(tmp_path / "lab").run()
    by_layer = {item.injected_layers[0]: item for item in result.results}

    assert by_layer[FailureLayer.SKILL].evolution_ticket.target_id == (
        "skill:unsafe_document_writer@1.0.0"
    )
    assert by_layer[FailureLayer.ROUTER].evolution_ticket.target_id == (
        "router:fault-router"
    )
    assert by_layer[FailureLayer.TOOL].evolution_ticket.target_id == (
        "tool:local-document-write"
    )
    assert by_layer[FailureLayer.CONTEXT].evolution_ticket.target_id.startswith(
        "context:matrix:fault-context:"
    )
    assert by_layer[FailureLayer.VERIFIER].evolution_ticket.target_id == (
        "verifier:local-document-v1"
    )
    model_ticket = by_layer[FailureLayer.MODEL].evolution_ticket
    assert model_ticket.target_layer == FailureLayer.MODEL
    assert model_ticket.target_id == (
        "model:synthetic/incapable-local-document-policy-v1"
    )
    assert model_ticket.proposed_action == EvolutionAction.TRAIN_MODEL
    assert "verified model failure" in model_ticket.expected_benefit.lower()
    assert model_ticket.required_evaluations == ["held_out", "regression", "safety"]

    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True).lower()
    assert "chain_of_thought" not in serialized
    assert "scratchpad" not in serialized
    assert "api_key" not in serialized
    assert "external_execution_performed\": true" not in serialized
