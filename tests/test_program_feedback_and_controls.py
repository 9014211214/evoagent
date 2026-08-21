from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import EvolutionProgramPackageManager


def test_release_feedback_is_noncausal_until_independent_attribution(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="b" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)

    assert package.signal.causal_attribution_claimed is False
    assert package.signal.safety_violation_count == 1
    assert package.signal.protected_segments == ("protected",)
    assert "protected_segment_regression:protected" in package.signal.reasons
    assert package.attribution.signal_hash == package.signal.signal_hash
    assert package.attribution.attributor_id != package.signal.evidence_producer_id
    assert package.attribution.failure_layer.value == "context"
    assert package.attribution.action.value == "update_context"
    assert len(package.attribution.supported_experiment_hashes) == 1


def test_budget_and_ambiguous_attribution_controls_open_no_campaign(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="c" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)

    budget = package.budget_control
    assert budget.decisions[-1].action.value == "stop_budget"
    assert budget.final_head.state.value == "budget_exhausted"
    assert budget.generation_campaign_count == 0
    assert budget.attributions == ()

    ambiguous = package.ambiguous_control
    assert ambiguous.decisions[-1].action.value == "escalate"
    assert ambiguous.final_head.state.value == "escalated"
    assert ambiguous.generation_campaign_count == 0
    assert len(ambiguous.attributions) == 1
    assert len(ambiguous.attributions[0].supported_experiment_hashes) == 2
