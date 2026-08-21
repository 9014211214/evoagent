from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from evoagent.integrated import (
    IntegratedCaseRecord,
    IntegratedCaseStatus,
    IntegratedRunRecord,
    IntegratedRunStatus,
    IntegratedTrack,
    build_integrated_run_policy,
    build_integrated_track_result,
)
from tests.test_integrated_repository import _case


NOW = datetime.now(timezone.utc) - timedelta(minutes=1)
POLICY = build_integrated_run_policy()


def _run_payload(**updates):
    payload = {
        "run_id": "integrated-run:model-invariants",
        "lineage_id": "composite-lineage:model-invariants",
        "policy": POLICY,
        "status": IntegratedRunStatus.OPEN,
        "revision": 0,
        "round_index": 0,
        "skill_execution_count": 0,
        "policy_execution_count": 0,
        "terminal_decision_hash": None,
        "created_at": NOW,
        "updated_at": NOW,
        "completed_at": None,
    }
    payload.update(updates)
    return payload


def test_nonterminal_run_rejects_partial_or_complete_terminal_evidence():
    with pytest.raises(ValidationError, match="non-terminal"):
        IntegratedRunRecord(
            **_run_payload(terminal_decision_hash="a" * 64)
        )
    with pytest.raises(ValidationError, match="non-terminal"):
        IntegratedRunRecord(
            **_run_payload(completed_at=NOW)
        )
    with pytest.raises(ValidationError, match="non-terminal"):
        IntegratedRunRecord(
            **_run_payload(
                terminal_decision_hash="a" * 64,
                completed_at=NOW,
            )
        )


def test_terminal_run_requires_both_completion_fields():
    with pytest.raises(ValidationError, match="terminal run"):
        IntegratedRunRecord(
            **_run_payload(
                status=IntegratedRunStatus.STOPPED,
                completed_at=NOW,
            )
        )
    with pytest.raises(ValidationError, match="terminal run"):
        IntegratedRunRecord(
            **_run_payload(
                status=IntegratedRunStatus.ESCALATED,
                terminal_decision_hash="b" * 64,
            )
        )

    valid = IntegratedRunRecord(
        **_run_payload(
            status=IntegratedRunStatus.STOPPED,
            terminal_decision_hash="c" * 64,
            completed_at=NOW,
        )
    )
    assert valid.status == IntegratedRunStatus.STOPPED


def test_negative_track_metrics_are_rejected():
    with pytest.raises(ValidationError, match="non-negative"):
        build_integrated_track_result(
            result_id="integrated-result:negative-metric",
            run_id="integrated-run:model-invariants",
            track=IntegratedTrack.SKILL,
            case_ids=("case:skill",),
            source_decision_hashes=("d" * 64,),
            source_package_hashes=("e" * 64,),
            component_ref="skill:document_guard:0.2.0",
            component_hash="f" * 64,
            executor_id="integrated-skill-executor",
            started_at=NOW,
            completed_at=NOW,
            metrics={"deterministic_cost": -0.01},
            skill_promoted=True,
        )


def test_case_record_requires_timezone_aware_persistence_times():
    case = _case(
        POLICY,
        case_id="case:model-invariants-skill",
        track=IntegratedTrack.SKILL,
        created_at=NOW,
    )
    with pytest.raises(ValidationError, match="timezone"):
        IntegratedCaseRecord(
            run_id="integrated-run:model-invariants",
            case=case,
            status=IntegratedCaseStatus.PENDING,
            claimed_by=None,
            result_id=None,
            revision=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
