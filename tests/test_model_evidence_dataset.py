from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evoagent.diagnosis import CounterfactualAttributionEngine
from evoagent.domain.models import Task
from evoagent.lab import GovernedModelEvolutionLab
from evoagent.training import (
    ModelEvidenceDatasetError,
    ModelEvidenceDatasetManager,
    ModelEvidenceExample,
)


def build_examples(tmp_path, count=4):
    lab = GovernedModelEvolutionLab(tmp_path / "lab")
    engine = CounterfactualAttributionEngine()
    examples = []
    reports = []
    runners = []
    for index in range(1, count + 1):
        runner = lab._runner(index=index, task=lab._evidence_task(index))
        report = engine.diagnose(runner)
        examples.append(
            ModelEvidenceExample.build(
                report=report,
                failed_trace=runner.baseline_trace,
                reference_trace=runner.traces()["exp:model"],
                problem_cluster=lab.PROBLEM_CLUSTER,
            )
        )
        reports.append(report)
        runners.append(runner)
    return lab, tuple(examples), tuple(reports), tuple(runners)


def held_out_tasks():
    return tuple(
        Task(
            task_id=f"held-out:{index}",
            task_type="model-held-out",
            input={
                "initial_documents": {},
                "target_path": f"held-{index}.txt",
                "content": f"held out {index}",
                "expected_status": "completed",
                "require_verification": True,
            },
            expected_outcome={"status": "completed"},
        )
        for index in (1, 2)
    )


def test_executable_model_evidence_builds_sft_preferences_and_replay_seeds(tmp_path):
    lab, examples, reports, runners = build_examples(tmp_path)
    tasks = held_out_tasks()
    manager = ModelEvidenceDatasetManager()
    manifest = manager.build(
        examples=examples,
        held_out_task_ids=tuple(task.task_id for task in tasks),
        environment_id=lab.ENVIRONMENT_ID,
        verifier_id=lab.VERIFIER_ID,
        created_at=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
        replay_seed=lab.REPLAY_SEED,
    )

    assert manager.verify(manifest) is True
    assert len(manifest.examples) == 4
    assert len(manifest.supervised_examples) == 4
    assert len(manifest.preference_pairs) == 4
    assert len(manifest.replay_seeds) == 4
    assert len(set(manifest.evidence_task_ids)) == 4
    assert set(manifest.evidence_task_ids).isdisjoint(manifest.held_out_task_ids)
    assert all(example.failed.verifier_passed is False for example in manifest.examples)
    assert all(example.reference.verifier_passed is True for example in manifest.examples)
    assert all(
        pair.chosen_actions and pair.rejected_actions
        for pair in manifest.preference_pairs
    )
    assert all(
        report.root_cause_layer.value == "model"
        and [
            item.experiment_type.value
            for item in report.experiments
            if item.supports_hypothesis
        ]
        == ["reference_model"]
        for report in reports
    )
    assert all(
        runner.baseline_trace.task == runner.traces()["exp:model"].task
        for runner in runners
    )

    signals = manager.signals(manifest)
    assert signals.gold_trajectories == 4
    assert signals.preference_pairs == 4
    assert signals.replayable_environment is True
    assert signals.resettable_environment is True
    assert signals.machine_verifier is True

    path = tmp_path / "dataset.json"
    manager.export_file(manifest, path)
    assert manager.load_file(path) == manifest


def test_dataset_rejects_task_mismatch_hidden_reasoning_and_non_model_attribution(tmp_path):
    lab, examples, reports, runners = build_examples(tmp_path, count=1)
    runner = runners[0]
    report = reports[0]
    reference = runner.traces()["exp:model"]

    changed_task = reference.task.model_copy(
        deep=True,
        update={"task_id": "different-task"},
    )
    with pytest.raises(ModelEvidenceDatasetError, match="exact same frozen Task"):
        ModelEvidenceExample.build(
            report=report,
            failed_trace=runner.baseline_trace,
            reference_trace=reference.model_copy(deep=True, update={"task": changed_task}),
            problem_cluster=lab.PROBLEM_CLUSTER,
        )

    poisoned = runner.baseline_trace.model_copy(
        deep=True,
        update={
            "observable_events": [
                *runner.baseline_trace.observable_events,
                {"chain_of_thought": "private reasoning"},
            ]
        },
    )
    with pytest.raises(ModelEvidenceDatasetError, match="hidden-reasoning"):
        ModelEvidenceExample.build(
            report=report,
            failed_trace=poisoned,
            reference_trace=reference,
            problem_cluster=lab.PROBLEM_CLUSTER,
        )

    non_model = report.model_copy(
        deep=True,
        update={"actionable": False},
    )
    with pytest.raises(ModelEvidenceDatasetError, match="Only actionable"):
        ModelEvidenceExample.build(
            report=non_model,
            failed_trace=runner.baseline_trace,
            reference_trace=reference,
            problem_cluster=lab.PROBLEM_CLUSTER,
        )


def test_dataset_rejects_duplicates_overlap_and_manifest_tampering(tmp_path):
    lab, examples, _, _ = build_examples(tmp_path, count=2)
    manager = ModelEvidenceDatasetManager()

    with pytest.raises(ModelEvidenceDatasetError, match="distinct"):
        manager.build(
            examples=(examples[0], examples[0]),
            held_out_task_ids=("held-out:1",),
            environment_id=lab.ENVIRONMENT_ID,
            verifier_id=lab.VERIFIER_ID,
            created_at=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
            replay_seed=lab.REPLAY_SEED,
        )

    with pytest.raises(ModelEvidenceDatasetError, match="disjoint"):
        manager.build(
            examples=examples,
            held_out_task_ids=(examples[0].task.task_id,),
            environment_id=lab.ENVIRONMENT_ID,
            verifier_id=lab.VERIFIER_ID,
            created_at=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
            replay_seed=lab.REPLAY_SEED,
        )

    manifest = manager.build(
        examples=examples,
        held_out_task_ids=("held-out:1",),
        environment_id=lab.ENVIRONMENT_ID,
        verifier_id=lab.VERIFIER_ID,
        created_at=datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
        replay_seed=lab.REPLAY_SEED,
    )
    with pytest.raises(ModelEvidenceDatasetError, match="hash mismatch"):
        manager.verify(manifest.model_copy(update={"manifest_hash": "0" * 64}))
