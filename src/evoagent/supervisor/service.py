from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Protocol

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.supervisor.models import (
    SupervisorCase,
    SupervisorCaseRecord,
    SupervisorCaseStatus,
    SupervisorOutcome,
    SupervisorPolicy,
    SupervisorRunRecord,
    SupervisorRunStatus,
    SupervisorTrack,
    canonical_sha256,
)
from evoagent.supervisor.repository import (
    SQLiteSupervisorRepository,
    SupervisorConflictError,
)


class EvolutionTrackExecutor(Protocol):
    executor_id: str
    track: SupervisorTrack
    idempotent: bool

    def execute(self, case: SupervisorCase) -> SupervisorOutcome:
        ...


def route_case(case: SupervisorCase) -> SupervisorTrack:
    """Route an attributed case without inventing a new root cause."""

    if case.trust_level != "verified" or case.safety_flags:
        return SupervisorTrack.QUARANTINE
    mapping = {
        EvolutionAction.NO_ACTION: SupervisorTrack.NONE,
        EvolutionAction.CREATE_SKILL: SupervisorTrack.SKILL,
        EvolutionAction.UPDATE_SKILL: SupervisorTrack.SKILL,
        EvolutionAction.TRAIN_MODEL: SupervisorTrack.MODEL,
        EvolutionAction.UPDATE_ROUTER: SupervisorTrack.EXTERNAL_REPAIR,
        EvolutionAction.REPAIR_TOOL: SupervisorTrack.EXTERNAL_REPAIR,
        EvolutionAction.UPDATE_CONTEXT: SupervisorTrack.EXTERNAL_REPAIR,
        EvolutionAction.REPAIR_VERIFIER: SupervisorTrack.EXTERNAL_REPAIR,
        EvolutionAction.QUARANTINE: SupervisorTrack.QUARANTINE,
        EvolutionAction.ESCALATE: SupervisorTrack.ESCALATION,
    }
    try:
        track = mapping[case.action]
    except KeyError as exc:  # pragma: no cover - future enum values fail closed
        raise SupervisorConflictError(
            f"Unsupported evolution action: {case.action.value}"
        ) from exc
    _validate_layer_action(case, track)
    return track


def build_supervisor_case(
    *,
    case_id: str,
    trace_id: str,
    task_id: str,
    failure_layer: FailureLayer,
    action: EvolutionAction,
    attribution_hash: str,
    evidence_hash: str,
    source: str,
    trust_level: str = "verified",
    safety_flags: tuple[str, ...] = (),
    created_at: datetime | None = None,
) -> SupervisorCase:
    created_at = created_at or datetime.now(timezone.utc)
    payload = {
        "case_id": case_id,
        "trace_id": trace_id,
        "task_id": task_id,
        "failure_layer": failure_layer,
        "action": action,
        "attribution_hash": attribution_hash,
        "evidence_hash": evidence_hash,
        "source": source,
        "trust_level": trust_level,
        "safety_flags": safety_flags,
        "created_at": created_at,
    }
    return SupervisorCase(
        **payload,
        case_hash=canonical_sha256(payload),
    )


class PersistentEvolutionSupervisor:
    """Durably route cases while leaving mutation to governed track executors."""

    def __init__(
        self,
        *,
        repository: SQLiteSupervisorRepository,
        run_id: str,
        policy: SupervisorPolicy | None = None,
        executors: Mapping[SupervisorTrack, EvolutionTrackExecutor] | None = None,
        actor_id: str = "evoagent-supervisor",
    ):
        self.repository = repository
        self.run_id = run_id
        self.policy = policy or SupervisorPolicy()
        self.executors = dict(executors or {})
        self.actor_id = actor_id

    def process(self, cases: tuple[SupervisorCase, ...]) -> SupervisorRunRecord:
        if len({case.case_id for case in cases}) != len(cases):
            raise SupervisorConflictError("A Supervisor batch contains duplicate case IDs.")
        run, _ = self.repository.create_or_get_run(
            self.run_id,
            self.policy,
            actor_id=self.actor_id,
        )
        if run.status not in {SupervisorRunStatus.OPEN, SupervisorRunStatus.RUNNING}:
            self._validate_terminal_resume(cases)
            self.repository.verify_state(self.run_id)
            return run
        if run.status == SupervisorRunStatus.OPEN:
            run = self.repository.transition_run(
                self.run_id,
                to_status=SupervisorRunStatus.RUNNING,
                expected_revision=run.revision,
                actor_id=self.actor_id,
                reason="Closed-loop case processing started.",
            )

        for round_index, case in enumerate(cases, start=1):
            current_run = self.repository.get_run(self.run_id)
            if current_run.status not in {
                SupervisorRunStatus.OPEN,
                SupervisorRunStatus.RUNNING,
            }:
                break
            if round_index > self.policy.budget.max_rounds:
                self._finish_run(
                    SupervisorRunStatus.BUDGET_EXHAUSTED,
                    "Supervisor maximum round budget was exhausted.",
                )
                break
            record = self._process_case(case)
            if (
                record.status == SupervisorCaseStatus.QUARANTINED
                and self.policy.stop_on_quarantine
            ):
                self._finish_run(
                    SupervisorRunStatus.QUARANTINED,
                    "A quarantined case stopped automatic evolution.",
                )
                break

        current = self.repository.get_run(self.run_id)
        if current.status in {SupervisorRunStatus.OPEN, SupervisorRunStatus.RUNNING}:
            self._finish_run(*self._derive_final_status())
        self.repository.verify_state(self.run_id)
        return self.repository.get_run(self.run_id)

    def _process_case(self, case: SupervisorCase) -> SupervisorCaseRecord:
        track = route_case(case)
        record, _ = self.repository.admit_case(
            self.run_id,
            case,
            track,
            actor_id=self.actor_id,
        )
        if record.status not in {
            SupervisorCaseStatus.PENDING,
            SupervisorCaseStatus.RUNNING,
        }:
            return record
        if record.status == SupervisorCaseStatus.RUNNING:
            raise SupervisorConflictError(
                "Interrupted RUNNING case requires explicit operator recovery."
            )
        claimed = self.repository.claim_case(
            self.run_id,
            case.case_id,
            expected_revision=record.revision,
            actor_id=self.actor_id,
        )
        outcome = self._execute_or_route(case, track)
        return self.repository.finalize_case(
            self.run_id,
            case.case_id,
            outcome,
            expected_revision=claimed.revision,
            actor_id=self.actor_id,
        )

    def _execute_or_route(
        self,
        case: SupervisorCase,
        track: SupervisorTrack,
    ) -> SupervisorOutcome:
        now = datetime.now(timezone.utc)
        if track == SupervisorTrack.NONE:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.COMPLETED,
                "Verified execution requires no evolution action.",
                completed_at=now,
            )
        if track == SupervisorTrack.ESCALATION:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.ESCALATED,
                "Attribution requires human investigation; no artifact was mutated.",
                completed_at=now,
            )
        if track == SupervisorTrack.QUARANTINE:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.QUARANTINED,
                "Untrusted or safety-flagged evidence was quarantined.",
                completed_at=now,
            )
        if track == SupervisorTrack.EXTERNAL_REPAIR:
            if not self.policy.automatic_external_repair:
                return self._outcome(
                    case,
                    track,
                    SupervisorCaseStatus.BLOCKED,
                    "External repair requires a separately authorized implementation.",
                    completed_at=now,
                    artifact_refs=(f"evolution-ticket:{case.case_id}",),
                )
        budget_error = self._track_budget_error(track)
        if budget_error is not None:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.BLOCKED,
                budget_error,
                completed_at=now,
            )
        if track == SupervisorTrack.SKILL and not self.policy.automatic_skill:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.BLOCKED,
                "Automatic Skill evolution is disabled by Supervisor policy.",
                completed_at=now,
            )
        if track == SupervisorTrack.MODEL and not self.policy.automatic_model:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.BLOCKED,
                "Automatic Model lifecycle execution is disabled by Supervisor policy.",
                completed_at=now,
            )
        executor = self.executors.get(track)
        if executor is None:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.BLOCKED,
                f"No governed {track.value} executor was supplied.",
                completed_at=now,
            )
        if executor.track != track:
            raise SupervisorConflictError("Track executor advertises the wrong track.")
        if self.policy.require_idempotent_executors and not executor.idempotent:
            raise SupervisorConflictError("Supervisor policy requires idempotent executors.")
        try:
            outcome = executor.execute(case)
        except Exception as exc:
            return self._outcome(
                case,
                track,
                SupervisorCaseStatus.FAILED,
                f"Track executor failed closed: {type(exc).__name__}.",
                completed_at=datetime.now(timezone.utc),
                executor_id=executor.executor_id,
            )
        if outcome.case_id != case.case_id:
            raise SupervisorConflictError("Track executor returned another case ID.")
        if outcome.track != track:
            raise SupervisorConflictError("Track executor returned another track.")
        if outcome.executor_id != executor.executor_id:
            raise SupervisorConflictError("Track executor identity binding changed.")
        if outcome.training_executed_by_evoagent or outcome.external_execution_performed:
            raise SupervisorConflictError(
                "Closed-loop research Supervisor cannot accept executed training or external work."
            )
        return outcome

    def _track_budget_error(self, track: SupervisorTrack) -> str | None:
        cases = self.repository.list_cases(self.run_id)
        count = sum(item.track == track for item in cases)
        if track == SupervisorTrack.SKILL and count > self.policy.budget.max_skill_executions:
            return "Supervisor Skill-execution budget is exhausted."
        if track == SupervisorTrack.MODEL and count > self.policy.budget.max_model_executions:
            return "Supervisor Model-execution budget is exhausted."
        if (
            track == SupervisorTrack.EXTERNAL_REPAIR
            and count > self.policy.budget.max_external_repair_tickets
        ):
            return "Supervisor external-repair ticket budget is exhausted."
        return None

    def _derive_final_status(self) -> tuple[SupervisorRunStatus, str]:
        cases = self.repository.list_cases(self.run_id)
        statuses = {item.status for item in cases}
        if SupervisorCaseStatus.QUARANTINED in statuses:
            return (
                SupervisorRunStatus.QUARANTINED,
                "Closed-loop run contains quarantined evidence.",
            )
        if SupervisorCaseStatus.FAILED in statuses:
            return (
                SupervisorRunStatus.FAILED,
                "At least one governed track executor failed closed.",
            )
        blocked = [item for item in cases if item.status == SupervisorCaseStatus.BLOCKED]
        if blocked:
            if any(
                item.outcome is not None and "budget is exhausted" in item.outcome.reason.lower()
                for item in blocked
            ):
                return (
                    SupervisorRunStatus.BUDGET_EXHAUSTED,
                    "At least one track exceeded the immutable Supervisor budget.",
                )
            return (
                SupervisorRunStatus.BLOCKED,
                "At least one case awaits separately authorized work.",
            )
        if SupervisorCaseStatus.ESCALATED in statuses:
            return (
                SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS,
                "Automatic tracks completed and unresolved cases were escalated.",
            )
        return (
            SupervisorRunStatus.COMPLETED,
            "All admitted cases reached governed terminal outcomes.",
        )

    def _finish_run(
        self,
        status: SupervisorRunStatus,
        reason: str,
    ) -> SupervisorRunRecord:
        run = self.repository.get_run(self.run_id)
        if run.status == status:
            return run
        if run.status not in {SupervisorRunStatus.OPEN, SupervisorRunStatus.RUNNING}:
            return run
        return self.repository.transition_run(
            self.run_id,
            to_status=status,
            expected_revision=run.revision,
            actor_id=self.actor_id,
            reason=reason,
        )

    def _validate_terminal_resume(self, cases: tuple[SupervisorCase, ...]) -> None:
        stored = {item.case.case_id: item for item in self.repository.list_cases(self.run_id)}
        supplied = {item.case_id: item for item in cases}
        if set(stored) != set(supplied):
            raise SupervisorConflictError(
                "Terminal Supervisor resume case set differs from persisted state."
            )
        for case_id, case in supplied.items():
            if stored[case_id].case != case:
                raise SupervisorConflictError(
                    "Terminal Supervisor resume contains conflicting case evidence."
                )

    @staticmethod
    def _outcome(
        case: SupervisorCase,
        track: SupervisorTrack,
        status: SupervisorCaseStatus,
        reason: str,
        *,
        completed_at: datetime,
        executor_id: str | None = None,
        child_run_id: str | None = None,
        artifact_refs: tuple[str, ...] = (),
        artifact_hashes: dict[str, str] | None = None,
        metrics: dict[str, float] | None = None,
        skill_promoted: bool = False,
        model_candidate_evaluated: bool = False,
        model_candidate_activated: bool = False,
        model_rollback_verified: bool = False,
    ) -> SupervisorOutcome:
        payload = {
            "case_id": case.case_id,
            "track": track,
            "status": status,
            "reason": reason,
            "executor_id": executor_id,
            "child_run_id": child_run_id,
            "artifact_refs": artifact_refs,
            "artifact_hashes": artifact_hashes or {},
            "metrics": metrics or {},
            "completed_at": completed_at,
            "skill_promoted": skill_promoted,
            "model_candidate_evaluated": model_candidate_evaluated,
            "model_candidate_activated": model_candidate_activated,
            "model_rollback_verified": model_rollback_verified,
            "training_executed_by_evoagent": False,
            "external_execution_performed": False,
        }
        return SupervisorOutcome(
            **payload,
            outcome_hash=canonical_sha256(payload),
        )



def _validate_layer_action(case: SupervisorCase, track: SupervisorTrack) -> None:
    expected_layers = {
        SupervisorTrack.NONE: {FailureLayer.NONE},
        SupervisorTrack.SKILL: {FailureLayer.SKILL},
        SupervisorTrack.MODEL: {FailureLayer.MODEL},
        SupervisorTrack.EXTERNAL_REPAIR: {
            FailureLayer.ROUTER,
            FailureLayer.TOOL,
            FailureLayer.CONTEXT,
            FailureLayer.VERIFIER,
        },
        SupervisorTrack.ESCALATION: {
            FailureLayer.ENVIRONMENT,
            FailureLayer.UNKNOWN,
            FailureLayer.SAFETY,
        },
        SupervisorTrack.QUARANTINE: set(FailureLayer),
    }
    if case.failure_layer not in expected_layers[track]:
        raise SupervisorConflictError(
            f"Action {case.action.value} does not match failure layer {case.failure_layer.value}."
        )


__all__ = [
    "EvolutionTrackExecutor",
    "PersistentEvolutionSupervisor",
    "build_supervisor_case",
    "route_case",
]
