from __future__ import annotations

from evoagent.benchmarks import LocalToolEvolutionLab


def test_local_tool_evolution_lab_is_frozen_repeatable_and_improves(tmp_path):
    lab = LocalToolEvolutionLab(tmp_path / "lab")
    snapshots_before = tuple(item.model_dump_json() for item in lab.snapshots)

    result = lab.run()

    assert result.repeatable is True
    assert result.external_execution_performed is False
    assert result.summary.initial_score == 0.5
    assert result.summary.final_score == 1.0
    assert result.summary.evolution_gain == 0.5
    assert result.summary.best_round == 1
    assert tuple(item.model_dump_json() for item in lab.snapshots) == snapshots_before

    first = result.first_run.evaluations
    second = result.second_run.evaluations
    assert first[0].per_task == {
        "local:create-note": 1.0,
        "local:protected-policy": 0.0,
    }
    assert first[1].per_task == {
        "local:create-note": 1.0,
        "local:protected-policy": 1.0,
    }
    assert [item.per_task for item in first] == [item.per_task for item in second]
    assert [item.usage.tool_calls for item in first] == [3, 4]
    assert [item.usage.tool_calls for item in second] == [3, 4]
    assert all(
        item.manifest_fingerprint == first[0].manifest_fingerprint
        for item in (*first, *second)
    )
    assert all(item.model_id == first[0].model_id for item in (*first, *second))


def test_local_tool_lab_exposes_per_task_observable_traces(tmp_path):
    result = LocalToolEvolutionLab(tmp_path / "lab").run()

    base = result.first_traces["A0-local-tool"]["local:protected-policy"]
    evolved = result.first_traces["A1-local-tool"]["local:protected-policy"]

    assert base.verifier_passed is False
    assert base.verifier_feedback == "missing_skill_rule: inspect_before_write"
    assert evolved.verifier_passed is True
    assert evolved.final_output["status"] == "blocked"

    for trace in (base, evolved):
        verification = trace.observable_events[-1]
        assert verification["event"] == "verification"
        assert len(verification["initial_state_fingerprint"]) == 64
        assert len(verification["final_state_fingerprint"]) == 64
        assert trace.cost["cost_usd"] == 0.0
        assert trace.cost["llm_tokens"] == 0.0
