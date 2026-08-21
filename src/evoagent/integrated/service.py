from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.composite import (
    CompositeEvaluationService,
    CompositeStopAction,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content

from .models import (
    IntegratedCaseRecord,
    IntegratedCaseStatus,
    IntegratedRunStatus,
    IntegratedTrack,
    IntegratedTrackResult,
)
from .repository_hardened import SQLiteIntegratedEvolutionRepository


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class IntegratedDispatchAction(str, Enum):
    CLAIM_SKILL = "claim_skill"
    CLAIM_LOCAL_POLICY = "claim_local_policy"
    RESUME_SKILL = "resume_skill"
    RESUME_LOCAL_POLICY = "resume_local_policy"
    AWAIT_POLICY_EVIDENCE = "await_policy_evidence"
    ESCALATE_REQUIRED = "escalate_required"
    IDLE = "idle"
    TERMINAL = "terminal"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


class IntegratedDispatchPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-integrated-dispatch-v1"] = (
        "evoagent-integrated-dispatch-v1"
    )
    plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy_hash: str = Field(pattern=_SHA256_PATTERN)
    observed_run_revision: int = Field(ge=0)
    expected_claim_revision: int = Field(ge=0)
    action: IntegratedDispatchAction
    track: IntegratedTrack | None = None
    case_ids: tuple[str, ...] = ()
    executor_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    reason: str
    planned_at: datetime
    plan_hash: str = Field(pattern=_SHA256_PATTERN)
    component_mutation_performed: Literal[False] = False
    foundation_model_training_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False

    @field_validator("planned_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Integrated dispatch plan time")

    @field_validator("case_ids")
    @classmethod
    def validate_cases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError(
                "Integrated dispatch case IDs must not contain duplicates."
            )
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_plan(self):
        claims = {
            IntegratedDispatchAction.CLAIM_SKILL,
            IntegratedDispatchAction.CLAIM_LOCAL_POLICY,
            IntegratedDispatchAction.RESUME_SKILL,
            IntegratedDispatchAction.RESUME_LOCAL_POLICY,
        }
        if self.action in claims:
            if (
                self.track not in {
                    IntegratedTrack.SKILL,
                    IntegratedTrack.LOCAL_POLICY,
                }
                or not self.case_ids
                or self.executor_id is None
            ):
                raise ValueError(
                    "Integrated claim plan lacks track, cases or executor."
                )
        elif (
            self.track is not None
            or self.case_ids
            or self.executor_id is not None
        ):
            raise ValueError(
                "Integrated non-claim plan contains execution authority."
            )
        if self.action in {
            IntegratedDispatchAction.RESUME_SKILL,
            IntegratedDispatchAction.RESUME_LOCAL_POLICY,
        } and self.observed_run_revision != self.expected_claim_revision + 1:
            raise ValueError(
                "Integrated resume plan differs from the applied claim revision."
            )
        if self.action in {
            IntegratedDispatchAction.CLAIM_SKILL,
            IntegratedDispatchAction.CLAIM_LOCAL_POLICY,
        } and self.observed_run_revision != self.expected_claim_revision:
            raise ValueError(
                "Integrated new claim plan differs from the observed revision."
            )
        if not self.reason.strip():
            raise ValueError("Integrated dispatch plan requires a reason.")
        payload = self.model_dump(mode="json", exclude={"plan_hash"})
        validate_safe_content(payload)
        if self.plan_hash != canonical_sha256(payload):
            raise ValueError("Integrated dispatch plan hash mismatch.")
        return self


class IntegratedSupervisorService:
    """Deterministically plan and claim the minimum governed intervention."""

    def __init__(
        self,
        repository: SQLiteIntegratedEvolutionRepository,
        *,
        skill_executor_id: str,
        local_policy_executor_id: str,
        evaluation_service: CompositeEvaluationService | None = None,
    ):
        if skill_executor_id == local_policy_executor_id:
            raise ValueError(
                "Integrated Skill and local-policy executors must be distinct."
            )
        self.repository = repository
        self.skill_executor_id = skill_executor_id
        self.local_policy_executor_id = local_policy_executor_id
        self.evaluation_service = evaluation_service

    def plan_next(
        self,
        run_id: str,
        *,
        plan_id: str,
        planned_at: datetime,
    ) -> IntegratedDispatchPlan:
        run = self.repository.get_run(run_id)
        if run.status in {
            IntegratedRunStatus.STOPPED,
            IntegratedRunStatus.ESCALATED,
            IntegratedRunStatus.FAILED,
        }:
            return self._plan(
                plan_id=plan_id,
                run=run,
                action=IntegratedDispatchAction.TERMINAL,
                reason="Integrated run is terminal and immutable.",
                planned_at=planned_at,
            )

        claimed = self.repository.list_cases(
            run_id,
            status=IntegratedCaseStatus.CLAIMED,
        )
        if claimed:
            tracks = {item.case.track for item in claimed}
            actors = {item.claimed_by for item in claimed}
            if len(tracks) != 1 or len(actors) != 1:
                raise RuntimeError(
                    "Integrated claimed batch contains mixed tracks or executors."
                )
            track = next(iter(tracks))
            executor_id = next(iter(actors))
            expected_executor = self._executor(track)
            if executor_id != expected_executor:
                raise RuntimeError(
                    "Integrated claimed batch belongs to another executor."
                )
            action = (
                IntegratedDispatchAction.RESUME_SKILL
                if track == IntegratedTrack.SKILL
                else IntegratedDispatchAction.RESUME_LOCAL_POLICY
            )
            return self._plan(
                plan_id=plan_id,
                run=run,
                action=action,
                track=track,
                case_ids=tuple(item.case.case_id for item in claimed),
                executor_id=executor_id,
                expected_claim_revision=run.revision - 1,
                reason="Resume the exact persisted mixed-track claim batch.",
                planned_at=planned_at,
            )

        if run.status != IntegratedRunStatus.OPEN:
            raise RuntimeError(
                "Integrated non-terminal run is neither OPEN nor claimed RUNNING."
            )
        skill = self.repository.pending_cases(
            run_id,
            IntegratedTrack.SKILL,
        )
        if skill:
            if run.skill_execution_count >= run.policy.max_skill_executions:
                return self._plan(
                    plan_id=plan_id,
                    run=run,
                    action=IntegratedDispatchAction.ESCALATE_REQUIRED,
                    reason="Pending Skill evidence remains after the Skill execution budget.",
                    planned_at=planned_at,
                )
            selected = min(skill, key=lambda item: item.case.case_id)
            return self._plan(
                plan_id=plan_id,
                run=run,
                action=IntegratedDispatchAction.CLAIM_SKILL,
                track=IntegratedTrack.SKILL,
                case_ids=(selected.case.case_id,),
                executor_id=self.skill_executor_id,
                reason="Claim the lexicographically first attributed Skill case.",
                planned_at=planned_at,
            )

        policy_cases = self.repository.pending_cases(
            run_id,
            IntegratedTrack.LOCAL_POLICY,
        )
        if policy_cases:
            if (
                run.policy_execution_count
                >= run.policy.max_policy_executions
            ):
                return self._plan(
                    plan_id=plan_id,
                    run=run,
                    action=IntegratedDispatchAction.ESCALATE_REQUIRED,
                    reason=(
                        "Pending local-policy evidence remains after the policy execution budget."
                    ),
                    planned_at=planned_at,
                )
            if len(policy_cases) < run.policy.min_policy_cases:
                return self._plan(
                    plan_id=plan_id,
                    run=run,
                    action=IntegratedDispatchAction.AWAIT_POLICY_EVIDENCE,
                    reason=(
                        "Local-policy evidence has not reached the frozen distinct-case threshold."
                    ),
                    planned_at=planned_at,
                )
            return self._plan(
                plan_id=plan_id,
                run=run,
                action=IntegratedDispatchAction.CLAIM_LOCAL_POLICY,
                track=IntegratedTrack.LOCAL_POLICY,
                case_ids=tuple(
                    sorted(item.case.case_id for item in policy_cases)
                ),
                executor_id=self.local_policy_executor_id,
                reason="Claim the complete pending local-policy evidence batch.",
                planned_at=planned_at,
            )

        return self._plan(
            plan_id=plan_id,
            run=run,
            action=IntegratedDispatchAction.IDLE,
            reason="No pending automatic mixed-track case is available.",
            planned_at=planned_at,
        )

    def claim_plan(
        self,
        plan: IntegratedDispatchPlan,
        *,
        now: datetime | None = None,
    ) -> tuple[IntegratedCaseRecord, ...]:
        claim_actions = {
            IntegratedDispatchAction.CLAIM_SKILL,
            IntegratedDispatchAction.CLAIM_LOCAL_POLICY,
            IntegratedDispatchAction.RESUME_SKILL,
            IntegratedDispatchAction.RESUME_LOCAL_POLICY,
        }
        if plan.action not in claim_actions:
            return ()
        current = self.repository.get_run(plan.run_id)
        if current.revision != plan.observed_run_revision:
            raise RuntimeError(
                "Integrated dispatch plan is stale relative to the run revision."
            )
        if current.policy.policy_hash != plan.policy_hash:
            raise RuntimeError(
                "Integrated dispatch plan differs from the frozen run policy."
            )
        _, records = self.repository.claim_cases(
            plan.run_id,
            case_ids=plan.case_ids,
            track=plan.track,
            actor_id=plan.executor_id,
            expected_run_revision=plan.expected_claim_revision,
            now=now,
        )
        return records

    def record_result(
        self,
        result: IntegratedTrackResult,
        *,
        expected_run_revision: int,
        now: datetime | None = None,
    ):
        expected_executor = self._executor(result.track)
        if result.executor_id != expected_executor:
            raise ValueError(
                "Integrated track result belongs to another configured executor."
            )
        return self.repository.record_result(
            result,
            actor_id=result.executor_id,
            expected_run_revision=expected_run_revision,
            now=now,
        )

    def complete_from_latest_decision(
        self,
        run_id: str,
        *,
        actor_id: str,
        expected_run_revision: int,
        now: datetime | None = None,
    ):
        if self.evaluation_service is None:
            raise RuntimeError(
                "Integrated completion requires the composite evaluation service."
            )
        run = self.repository.get_run(run_id)
        decision = self.evaluation_service.latest_decision(run.lineage_id)
        active = self.evaluation_service.snapshot_registry.active(
            run.lineage_id
        )
        if decision.snapshot_id != active.snapshot_id:
            raise RuntimeError(
                "Integrated terminal decision does not target the active composite snapshot."
            )
        pending_ids = tuple(
            sorted(
                item.case.case_id
                for item in self.repository.pending_cases(run_id)
            )
        )
        if tuple(decision.actionable_case_ids) != pending_ids:
            raise RuntimeError(
                "Integrated terminal decision differs from pending automatic cases."
            )
        if decision.action == CompositeStopAction.CONTINUE:
            raise RuntimeError(
                "Integrated completion cannot consume a CONTINUE decision."
            )
        return self.repository.complete_run(
            run_id,
            decision,
            actor_id=actor_id,
            expected_run_revision=expected_run_revision,
            now=now,
        )

    def verify_state(self, run_id: str) -> bool:
        run = self.repository.get_run(run_id)
        self.repository.verify_state(run_id)
        if self.evaluation_service is not None:
            self.evaluation_service.verify_state(run.lineage_id)
        return True

    def _executor(self, track: IntegratedTrack) -> str:
        if track == IntegratedTrack.SKILL:
            return self.skill_executor_id
        if track == IntegratedTrack.LOCAL_POLICY:
            return self.local_policy_executor_id
        raise ValueError(
            "Integrated automatic execution requires Skill or local-policy track."
        )

    @staticmethod
    def _plan(
        *,
        plan_id: str,
        run,
        action: IntegratedDispatchAction,
        reason: str,
        planned_at: datetime,
        track: IntegratedTrack | None = None,
        case_ids: tuple[str, ...] = (),
        executor_id: str | None = None,
        expected_claim_revision: int | None = None,
    ) -> IntegratedDispatchPlan:
        payload = {
            "format_version": "evoagent-integrated-dispatch-v1",
            "plan_id": plan_id,
            "run_id": run.run_id,
            "policy_hash": run.policy.policy_hash,
            "observed_run_revision": run.revision,
            "expected_claim_revision": (
                run.revision
                if expected_claim_revision is None
                else expected_claim_revision
            ),
            "action": action,
            "track": track,
            "case_ids": tuple(sorted(case_ids)),
            "executor_id": executor_id,
            "reason": reason,
            "planned_at": planned_at,
            "component_mutation_performed": False,
            "foundation_model_training_performed": False,
            "production_activation_performed": False,
            "production_deployment_performed": False,
        }
        return IntegratedDispatchPlan(
            **payload,
            plan_hash=canonical_sha256(payload),
        )


__all__ = [
    "IntegratedDispatchAction",
    "IntegratedDispatchPlan",
    "IntegratedSupervisorService",
]
