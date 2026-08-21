from __future__ import annotations

from evoagent.lab import ClosedLoopEvolutionSupervisorLab
from evoagent.supervisor import (
    ClosedLoopEvolutionPackageManager,
    SQLiteSupervisorRepository,
    SupervisorRunStatus,
)


def test_complete_closed_loop_supervisor_and_read_only_resume(tmp_path):
    lab = ClosedLoopEvolutionSupervisorLab(
        tmp_path / "closed-loop",
        source_commit="6" * 40,
    )
    first = lab.run()
    repository = SQLiteSupervisorRepository(lab.supervisor_database)
    event_count = repository.checkpoint().event_count
    second = lab.run()

    assert first.resumed is False
    assert second.resumed is True
    assert first.package_hash == second.package_hash
    assert second.run_status == SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS.value
    assert set(second.tracks) == {"skill", "model", "escalation"}
    assert sorted(second.case_statuses) == ["completed", "completed", "escalated"]
    assert second.skill_initial_score == 0.5
    assert second.skill_final_score == 1.0
    assert second.model_initial_score == 0.0
    assert second.model_final_score == 1.0
    assert second.composite_initial_score == 0.25
    assert second.composite_final_score == 1.0
    assert second.composite_gain == 0.75
    assert second.escalation_count == 1
    assert second.supervisor_case_count == 3
    assert second.supervisor_event_count == 15
    assert repository.checkpoint().event_count == event_count
    assert second.training_executed_by_evoagent is False
    assert second.external_execution_performed is False
    assert second.production_deployment_performed is False

    package = ClosedLoopEvolutionPackageManager().load_file(lab.package_path)
    assert package.package_hash == second.package_hash
    assert package.run.status == SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS
    assert package.checkpoint.model_dump(mode="json") == second.supervisor_checkpoint
    assert package.training_executed_by_evoagent is False
    assert package.external_execution_performed is False
    assert package.production_deployment_performed is False


def test_closed_loop_package_contains_no_hidden_reasoning_or_secrets(tmp_path):
    result = ClosedLoopEvolutionSupervisorLab(
        tmp_path / "closed-loop",
        source_commit="7" * 40,
    ).run()
    text = open(result.package_path, encoding="utf-8").read().lower()
    for forbidden in (
        "chain_of_thought",
        "hidden_reasoning",
        "reasoning_content",
        "scratchpad",
        "traceback",
        "private key",
        "api_key=",
        "password=",
    ):
        assert forbidden not in text
