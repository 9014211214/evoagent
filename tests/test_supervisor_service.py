from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.supervisor import (
    PersistentEvolutionSupervisor,
    SQLiteSupervisorRepository,
    SupervisorBudget,
    SupervisorCaseStatus,
    SupervisorConflictError,
    SupervisorOutcome,
    SupervisorPolicy,
    SupervisorRunStatus,
    SupervisorTrack,
    build_supervisor_case,
    canonical_sha256,
    route_case,
)


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)


def _case(
    case_id: str,
    *,
    layer: FailureLayer,
    action: EvolutionAction,
    trust_level: str = "verified",
    safety_flags: tuple[str, ...] = (),
):
    return build_supervisor_case(
        case_id=case_id,
        trace_id=f"trace:{case_id}",
        task_id=f"task:{case_id}",
        failure_layer=layer,
        action=action,
        attribution_hash=canonical_sha256(
            {"case": case_id, "kind": "attribution"}
        ),
        evidence_hash=canonical_sha256(
            {"case": case_id, "kind": "evidence"}
        ),
        source="test",
        trust_level=trust_level,
        safety_flags=safety_flags,
        created_at=NOW,
    )


class _Executor:
    idempotent = True

    def __init__(self, track: SupervisorTrack):
        self.track = track
        self.executor_id = f"executor:{track.value}"
        self.calls = 0

    def execute(self, case):
        self.calls += 1
        payload = {
            "case_id": case.case_id,
            "track": self.track,
            "status": SupervisorCaseStatus.COMPLETED,
            "reason": "governed executor completed",
            "executor_id": self.executor_id,
            "child_run_id": f"child:{case.case_id}",
            "artifact_refs": (f"artifact:{case.case_id}",),
            "artifact_hashes": {
                "result": canonical_sha256(case.case_id)
            },
            "metrics": (
                {"initial_score": 0.5, "final_score": 1.0}
                if self.track == SupervisorTrack.SKILL
                else {
                    "held_out_base_score": 0.0,
                    "held_out_candidate_score": 1.0,
                }
            ),
            "completed_at": NOW,
            "skill_promoted": self.track == SupervisorTrack.SKILL,
            "model_candidate_evaluated": (
                self.track == SupervisorTrack.MODEL
            ),
            "model_candidate_activated": (
                self.track == SupervisorTrack.MODEL
            ),
            "model_rollback_verified": (
                self.track == SupervisorTrack.MODEL
            ),
            "training_executed_by_evoagent": False,
            "external_execution_performed": False,
        }
        return SupervisorOutcome(
            **payload,
            outcome_hash=canonical_sha256(payload),
        )


def _policy(**budget_updates):
    budget = {
        "max_cases": 5,
        "max_rounds": 5,
        "max_skill_executions": 2,
        "max_model_executions": 2,
        "max_external_repair_tickets": 1,
    }
    budget.update(budget_updates)
    return SupervisorPolicy(budget=SupervisorBudget(**budget))


def test_supervisor_routes_skill_model_and_escalation_then_resumes_read_only(
    tmp_path,
):
    skill_executor = _Executor(SupervisorTrack.SKILL)
    model_executor = _Executor(SupervisorTrack.MODEL)
    repository = SQLiteSupervisorRepository(tmp_path / "supervisor.db")
    supervisor = PersistentEvolutionSupervisor(
        repository=repository,
        run_id="run:mixed",
        policy=_policy(),
        executors={
            SupervisorTrack.SKILL: skill_executor,
            SupervisorTrack.MODEL: model_executor,
        },
    )
    cases = (
        _case(
            "case:skill",
            layer=FailureLayer.SKILL,
            action=EvolutionAction.UPDATE_SKILL,
        ),
        _case(
            "case:model",
            layer=FailureLayer.MODEL,
            action=EvolutionAction.TRAIN_MODEL,
        ),
        _case(
            "case:environment",
            layer=FailureLayer.ENVIRONMENT,
            action=EvolutionAction.ESCALATE,
        ),
    )
    first = supervisor.process(cases)
    assert first.status == SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS
    assert skill_executor.calls == 1
    assert model_executor.calls == 1
    event_count = repository.checkpoint().event_count

    second = supervisor.process(cases)
    assert second == first
    assert skill_executor.calls == 1
    assert model_executor.calls == 1
    assert repository.checkpoint().event_count == event_count
    assert sorted(
        item.status.value for item in repository.list_cases(first.run_id)
    ) == ["completed", "completed", "escalated"]


def test_track_budget_is_checked_before_second_executor_call(tmp_path):
    skill_executor = _Executor(SupervisorTrack.SKILL)
    repository = SQLiteSupervisorRepository(tmp_path / "supervisor.db")
    supervisor = PersistentEvolutionSupervisor(
        repository=repository,
        run_id="run:budget",
        policy=_policy(max_skill_executions=1),
        executors={SupervisorTrack.SKILL: skill_executor},
    )
    cases = (
        _case(
            "case:skill-a",
            layer=FailureLayer.SKILL,
            action=EvolutionAction.UPDATE_SKILL,
        ),
        _case(
            "case:skill-b",
            layer=FailureLayer.SKILL,
            action=EvolutionAction.UPDATE_SKILL,
        ),
    )
    run = supervisor.process(cases)
    assert run.status == SupervisorRunStatus.BUDGET_EXHAUSTED
    assert skill_executor.calls == 1
    records = repository.list_cases(run.run_id)
    assert [item.status for item in records].count(
        SupervisorCaseStatus.BLOCKED
    ) == 1
    blocked = next(
        item for item in records
        if item.status == SupervisorCaseStatus.BLOCKED
    )
    assert "budget is exhausted" in blocked.outcome.reason


def test_quarantine_stops_automatic_execution(tmp_path):
    skill_executor = _Executor(SupervisorTrack.SKILL)
    repository = SQLiteSupervisorRepository(tmp_path / "supervisor.db")
    supervisor = PersistentEvolutionSupervisor(
        repository=repository,
        run_id="run:quarantine",
        policy=_policy(),
        executors={SupervisorTrack.SKILL: skill_executor},
    )
    run = supervisor.process(
        (
            _case(
                "case:unsafe",
                layer=FailureLayer.SAFETY,
                action=EvolutionAction.QUARANTINE,
                safety_flags=("prompt_injection",),
            ),
            _case(
                "case:skill",
                layer=FailureLayer.SKILL,
                action=EvolutionAction.UPDATE_SKILL,
            ),
        )
    )
    assert run.status == SupervisorRunStatus.QUARANTINED
    assert skill_executor.calls == 0
    records = repository.list_cases(run.run_id)
    assert len(records) == 1
    assert records[0].status == SupervisorCaseStatus.QUARANTINED


def test_external_repair_is_blocked_without_authorized_executor(tmp_path):
    repository = SQLiteSupervisorRepository(tmp_path / "supervisor.db")
    supervisor = PersistentEvolutionSupervisor(
        repository=repository,
        run_id="run:repair",
        policy=_policy(),
    )
    run = supervisor.process(
        (
            _case(
                "case:tool",
                layer=FailureLayer.TOOL,
                action=EvolutionAction.REPAIR_TOOL,
            ),
        )
    )
    assert run.status == SupervisorRunStatus.BLOCKED
    record = repository.list_cases(run.run_id)[0]
    assert record.track == SupervisorTrack.EXTERNAL_REPAIR
    assert record.status == SupervisorCaseStatus.BLOCKED
    assert record.outcome.artifact_refs == (
        "evolution-ticket:case:tool",
    )


def test_route_case_rejects_layer_action_mismatch():
    case = _case(
        "case:mismatch",
        layer=FailureLayer.MODEL,
        action=EvolutionAction.UPDATE_SKILL,
    )
    with pytest.raises(
        SupervisorConflictError,
        match="does not match failure layer",
    ):
        route_case(case)
