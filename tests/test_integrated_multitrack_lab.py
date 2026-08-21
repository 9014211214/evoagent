from __future__ import annotations

import json
from pathlib import Path

import pytest

from evoagent.composite import (
    SQLiteCompositeEvaluationRepository,
    SQLiteCompositeSnapshotRegistry,
)
from evoagent.integrated import (
    IntegratedEvolutionPackageError,
    IntegratedEvolutionPackageManager,
    IntegratedRunStatus,
    IntegratedTrack,
    SQLiteIntegratedEvolutionRepository,
)
from evoagent.lab import IntegratedMultiTrackEvolutionLab
from evoagent.model_registry.models import canonical_sha256


def _run(tmp_path):
    lab = IntegratedMultiTrackEvolutionLab(
        tmp_path / "integrated-v2.3",
        source_commit="c" * 40,
    )
    result = lab.run()
    package = IntegratedEvolutionPackageManager().load_file(
        result.package_path
    )
    return lab, result, package


def _rehash(payload: dict) -> dict:
    payload["package_hash"] = canonical_sha256(
        {
            key: value
            for key, value in payload.items()
            if key != "package_hash"
        }
    )
    return payload


def test_real_integrated_lab_reaches_a0_a1_a2_and_stops(tmp_path):
    _, result, package = _run(tmp_path)

    assert result.resumed is False
    assert result.optimizer_invoked is True
    assert result.active_snapshot_id == (
        "composite-snapshot:integrated:a2"
    )
    assert result.active_snapshot_revision == 2
    assert result.active_skill_version == "1.1.0"
    assert result.active_policy_id == "local-policy:accepted:p1"
    assert result.active_policy_revision == 1
    assert result.composite_scores == (0.25, 0.5, 1.0)
    assert result.stop_actions == ("continue", "continue", "stop")
    assert result.case_count == 3
    assert result.track_result_count == 2
    assert result.local_skill_evolution_performed is True
    assert result.local_policy_optimization_performed is True
    assert result.foundation_model_training_performed is False
    assert result.production_activation_performed is False
    assert result.production_deployment_performed is False
    assert result.external_rollout_performed is False

    assert package.run.status == IntegratedRunStatus.STOPPED
    assert package.run.round_index == 2
    assert package.run.skill_execution_count == 1
    assert package.run.policy_execution_count == 1
    assert tuple(
        item.manifest.snapshot_id
        for item in package.composite_snapshots
    ) == (
        "composite-snapshot:integrated:a0",
        "composite-snapshot:integrated:a1",
        "composite-snapshot:integrated:a2",
    )
    assert tuple(
        item.evaluation.composite_score
        for item in package.evaluations
    ) == (0.25, 0.5, 1.0)
    assert tuple(
        item.evaluation.safety_violation_count
        for item in package.evaluations
    )[-1] == 0
    assert all(
        item.evaluation.regression_count == 0
        for item in package.evaluations
    )
    assert {
        item.track for item in package.track_results
    } == {IntegratedTrack.SKILL, IntegratedTrack.LOCAL_POLICY}
    assert all(
        item.status.value == "completed" for item in package.cases
    )
    assert package.foundation_model_training_performed is False
    assert package.production_activation_performed is False
    assert package.production_deployment_performed is False
    assert package.external_rollout_performed is False
    assert package.official_benchmark_claimed is False
    assert IntegratedEvolutionPackageManager.verify(package) is True


def test_second_integrated_invocation_is_fully_read_only(tmp_path):
    lab, first, first_package = _run(tmp_path)
    package_before = Path(first.package_path).read_bytes()
    integrated = SQLiteIntegratedEvolutionRepository(
        lab.integrated_database
    )
    composite = SQLiteCompositeSnapshotRegistry(lab.composite_database)
    evaluations = SQLiteCompositeEvaluationRepository(
        lab.evaluation_database
    )
    integrated_events_before = integrated.events(lab.RUN_ID)
    integrated_checkpoint_before = integrated.checkpoint(lab.RUN_ID)
    composite_events_before = composite.events(lab.LINEAGE_ID)
    composite_checkpoint_before = composite.checkpoint(lab.LINEAGE_ID)
    evaluation_events_before = evaluations.events(lab.LINEAGE_ID)
    evaluation_checkpoint_before = evaluations.checkpoint(lab.LINEAGE_ID)

    second = lab.run()
    second_package = IntegratedEvolutionPackageManager().load_file(
        second.package_path
    )

    assert second.resumed is True
    assert second.optimizer_invoked is False
    assert second.package_hash == first.package_hash
    assert second.composite_scores == first.composite_scores
    assert second.stop_actions == first.stop_actions
    assert second_package == first_package
    assert Path(second.package_path).read_bytes() == package_before
    assert integrated.events(lab.RUN_ID) == integrated_events_before
    assert integrated.checkpoint(lab.RUN_ID) == integrated_checkpoint_before
    assert composite.events(lab.LINEAGE_ID) == composite_events_before
    assert composite.checkpoint(lab.LINEAGE_ID) == composite_checkpoint_before
    assert evaluations.events(lab.LINEAGE_ID) == evaluation_events_before
    assert evaluations.checkpoint(lab.LINEAGE_ID) == (
        evaluation_checkpoint_before
    )


def test_rehashed_composite_pointer_substitution_is_rejected(tmp_path):
    _, result, _ = _run(tmp_path)
    source = Path(result.package_path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["composite_head"]["active_snapshot_id"] = (
        "composite-snapshot:integrated:a1"
    )
    _rehash(payload)
    forged = tmp_path / "forged-composite-pointer.json"
    forged.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        IntegratedEvolutionPackageError,
        match="pointer|lineage|snapshot",
    ):
        IntegratedEvolutionPackageManager().load_file(forged)


def test_rehashed_integrated_audit_tail_truncation_is_rejected(tmp_path):
    _, result, _ = _run(tmp_path)
    payload = json.loads(
        Path(result.package_path).read_text(encoding="utf-8")
    )
    payload["integrated_events"].pop()
    payload["integrated_checkpoint"]["event_count"] = len(
        payload["integrated_events"]
    )
    payload["integrated_checkpoint"]["head_hash"] = payload[
        "integrated_events"
    ][-1]["event_hash"]
    _rehash(payload)
    forged = tmp_path / "forged-integrated-tail.json"
    forged.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        IntegratedEvolutionPackageError,
        match="lifecycle|completion|audit",
    ):
        IntegratedEvolutionPackageManager().load_file(forged)


def test_conflicting_integrated_package_is_not_overwritten(tmp_path):
    _, _, package = _run(tmp_path)
    target = tmp_path / "foreign-integrated-package.json"
    original = b'{"foreign":"integrated-evidence"}\n'
    target.write_bytes(original)

    with pytest.raises(
        IntegratedEvolutionPackageError,
        match="differs from immutable evidence",
    ):
        IntegratedEvolutionPackageManager().export_file(package, target)

    assert target.read_bytes() == original
