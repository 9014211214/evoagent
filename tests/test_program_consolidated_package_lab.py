from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import EvolutionProgramPackageManager


def test_consolidated_public_package_manager_reverifies_controlled_lab(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="f" * 40,
    ).run()
    manager = EvolutionProgramPackageManager()
    package = manager.load_file(result.package_path)

    assert manager.verify(package) is True
    assert package.final_head.state.value == "completed"
    assert tuple(item.action.value for item in package.decisions) == (
        "continue",
        "stop_success",
    )
    assert package.generation_campaign.state.value == "completed"
    assert len(package.generation_approvals) == 2
