from datetime import datetime, timezone

from evoagent.lab import ShadowCanaryReleaseLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.release import (
    ReleaseAssessmentStatus,
    ReleaseDecisionAction,
    ReleaseEvidenceBatch,
    ReleaseEvidencePackageManager,
    ReleaseStageGate,
)


def _packages(tmp_path):
    result = ShadowCanaryReleaseLab(
        tmp_path / "release-lab", source_commit="b" * 40
    ).run()
    manager = ReleaseEvidencePackageManager()
    return (
        manager.load_file(result.drift.package_path),
        manager.load_file(result.passing.package_path),
    )


def test_hard_segment_and_safety_gates_override_aggregate_score(tmp_path):
    drift, passing = _packages(tmp_path)
    drift_final = next(
        item for item in drift.assessments if item.stage_id == "canary-25"
    )
    passing_final = next(
        item for item in passing.assessments if item.stage_id == "canary-25"
    )

    assert drift_final.quality_delta == 0.0
    assert drift_final.status == ReleaseAssessmentStatus.ROLLBACK
    assert drift_final.regressed_segments == 1
    assert drift_final.protected_segment_regressions == 1
    assert drift_final.challenger_safety_violations == 1
    assert "protected_segment_regression:protected" in drift_final.reasons
    assert "maximum_safety_violations_exceeded" in drift_final.reasons

    assert passing_final.status == ReleaseAssessmentStatus.PASS
    assert passing_final.regressed_segments == 0
    assert passing_final.challenger_safety_violations == 0
    final_decision = next(
        item for item in passing.decisions if item.stage_id == "canary-25"
    )
    assert final_decision.action == ReleaseDecisionAction.READY


def test_release_bootstrap_and_decisions_are_deterministic(tmp_path):
    drift, _ = _packages(tmp_path)
    gate = ReleaseStageGate()
    for batch, stored_assessment, stored_decision in zip(
        sorted(drift.batches, key=lambda item: item.stage_id),
        sorted(drift.assessments, key=lambda item: item.stage_id),
        sorted(drift.decisions, key=lambda item: item.stage_id),
        strict=True,
    ):
        assessment = gate.assess(
            drift.plan,
            batch,
            assessment_id=stored_assessment.assessment_id,
        )
        decision = gate.decide(
            drift.plan,
            assessment,
            decision_id=stored_decision.decision_id,
            decision_actor_id=stored_decision.decision_actor_id,
            decided_at=stored_decision.decided_at,
        )
        assert assessment == stored_assessment
        assert decision == stored_decision


def test_insufficient_release_evidence_holds_without_advancing(tmp_path):
    _, passing = _packages(tmp_path)
    base = next(item for item in passing.batches if item.stage_id == "shadow")
    events = base.events[:2]
    payload = base.model_dump(mode="python", exclude={"evidence_hash"})
    payload.update(
        {
            "batch_id": "release-batch:partial:shadow",
            "events": events,
            "pair_count": 1,
            "segment_pair_counts": {"general": 1},
            "source_file_sha256": "f" * 64,
        }
    )
    partial = ReleaseEvidenceBatch(
        **payload,
        evidence_hash=canonical_sha256(payload),
    )
    gate = ReleaseStageGate()
    assessment = gate.assess(
        passing.plan,
        partial,
        assessment_id="release-assessment:partial:shadow",
    )
    decision = gate.decide(
        passing.plan,
        assessment,
        decision_id="release-decision:partial:shadow",
        decision_actor_id="partial-evaluator",
        decided_at=datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc),
    )
    assert assessment.status == ReleaseAssessmentStatus.HOLD
    assert "minimum_stage_pairs_not_met" in assessment.reasons
    assert "minimum_segment_pairs_not_met:protected" in assessment.reasons
    assert decision.action == ReleaseDecisionAction.HOLD
    assert decision.next_stage_id is None