from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.model_registry.models import validate_safe_content
from evoagent.supervisor.models import (
    SupervisorAuditEvent,
    SupervisorCaseRecord,
    SupervisorCaseStatus,
    SupervisorCheckpoint,
    SupervisorEventType,
    SupervisorPolicy,
    SupervisorRunRecord,
    SupervisorRunStatus,
    SupervisorScoreSummary,
    SupervisorTrack,
    canonical_sha256,
)
from evoagent.supervisor.repository import SQLiteSupervisorRepository
from evoagent.supervisor.service import route_case


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENESIS_HASH = "0" * 64


class ClosedLoopPackageError(ValueError):
    pass


class ClosedLoopEvolutionPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-closed-loop-package-v1"] = (
        "evoagent-closed-loop-package-v1"
    )
    run_id: str
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    policy: SupervisorPolicy
    run: SupervisorRunRecord
    cases: tuple[SupervisorCaseRecord, ...]
    events: tuple[SupervisorAuditEvent, ...]
    checkpoint: SupervisorCheckpoint
    score_summary: SupervisorScoreSummary
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    synthetic_fixture: Literal[True] = True
    training_executed_by_evoagent: Literal[False] = False
    external_execution_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Closed-loop package time must include a timezone.")
        return value


class ClosedLoopEvolutionPackageManager:
    def build(
        self,
        *,
        run_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        policy: SupervisorPolicy,
        run: SupervisorRunRecord,
        cases: tuple[SupervisorCaseRecord, ...],
        events: tuple[SupervisorAuditEvent, ...],
        checkpoint: SupervisorCheckpoint,
        score_summary: SupervisorScoreSummary,
    ) -> ClosedLoopEvolutionPackageManifest:
        provisional = ClosedLoopEvolutionPackageManifest(
            run_id=run_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            policy=policy,
            run=run,
            cases=cases,
            events=events,
            checkpoint=checkpoint,
            score_summary=score_summary,
            package_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: ClosedLoopEvolutionPackageManifest) -> bool:
        payload = manifest.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if manifest.package_hash != canonical_sha256(payload):
            raise ClosedLoopPackageError("Closed-loop package hash mismatch.")
        if manifest.run_id != manifest.run.run_id:
            raise ClosedLoopPackageError("Package run ID differs from the Supervisor run.")
        if manifest.policy != manifest.run.policy:
            raise ClosedLoopPackageError("Package policy differs from the Supervisor run policy.")
        if manifest.run.status in {SupervisorRunStatus.OPEN, SupervisorRunStatus.RUNNING}:
            raise ClosedLoopPackageError("Closed-loop package requires a terminal run.")
        if not manifest.cases:
            raise ClosedLoopPackageError("Closed-loop package requires admitted cases.")
        case_ids = [record.case.case_id for record in manifest.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ClosedLoopPackageError("Closed-loop package contains duplicate case IDs.")
        if len(manifest.cases) > manifest.policy.budget.max_cases:
            raise ClosedLoopPackageError("Packaged case count exceeds the Supervisor budget.")
        for record in manifest.cases:
            if record.run_id != manifest.run_id:
                raise ClosedLoopPackageError("Packaged case belongs to another run.")
            if route_case(record.case) != record.track:
                raise ClosedLoopPackageError("Packaged case action-to-track routing changed.")
            if record.status in {SupervisorCaseStatus.PENDING, SupervisorCaseStatus.RUNNING}:
                raise ClosedLoopPackageError("Terminal package contains unfinished cases.")
            if record.outcome is None:
                raise ClosedLoopPackageError("Terminal packaged case lacks an outcome.")
            if record.outcome.training_executed_by_evoagent:
                raise ClosedLoopPackageError("Package cannot claim evoagent executed training.")
            if record.outcome.external_execution_performed:
                raise ClosedLoopPackageError("Package cannot contain external execution.")

        self._verify_case_budgets(manifest)
        self._verify_run_status(manifest)
        self._verify_score_summary(manifest)
        self._verify_events(manifest)
        return True

    @staticmethod
    def _verify_case_budgets(manifest: ClosedLoopEvolutionPackageManifest) -> None:
        skill_count = sum(item.track == SupervisorTrack.SKILL for item in manifest.cases)
        model_count = sum(item.track == SupervisorTrack.MODEL for item in manifest.cases)
        repair_count = sum(
            item.track == SupervisorTrack.EXTERNAL_REPAIR for item in manifest.cases
        )
        budget = manifest.policy.budget
        if skill_count > budget.max_skill_executions:
            raise ClosedLoopPackageError("Packaged Skill cases exceed the Supervisor budget.")
        if model_count > budget.max_model_executions:
            raise ClosedLoopPackageError("Packaged Model cases exceed the Supervisor budget.")
        if repair_count > budget.max_external_repair_tickets:
            raise ClosedLoopPackageError(
                "Packaged external-repair cases exceed the Supervisor budget."
            )

    @staticmethod
    def _verify_run_status(manifest: ClosedLoopEvolutionPackageManifest) -> None:
        statuses = {item.status for item in manifest.cases}
        expected = SupervisorRunStatus.COMPLETED
        if SupervisorCaseStatus.QUARANTINED in statuses:
            expected = SupervisorRunStatus.QUARANTINED
        elif SupervisorCaseStatus.FAILED in statuses:
            expected = SupervisorRunStatus.FAILED
        elif SupervisorCaseStatus.BLOCKED in statuses:
            blocked = [
                item
                for item in manifest.cases
                if item.status == SupervisorCaseStatus.BLOCKED
            ]
            if any(
                item.outcome is not None
                and "budget is exhausted" in item.outcome.reason.lower()
                for item in blocked
            ):
                expected = SupervisorRunStatus.BUDGET_EXHAUSTED
            else:
                expected = SupervisorRunStatus.BLOCKED
        elif SupervisorCaseStatus.ESCALATED in statuses:
            expected = SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS
        if manifest.run.status != expected:
            raise ClosedLoopPackageError(
                "Supervisor run status is not derived from terminal case outcomes."
            )

    @staticmethod
    def _verify_score_summary(manifest: ClosedLoopEvolutionPackageManifest) -> None:
        skill = [
            item
            for item in manifest.cases
            if item.track == SupervisorTrack.SKILL
            and item.status == SupervisorCaseStatus.COMPLETED
        ]
        model = [
            item
            for item in manifest.cases
            if item.track == SupervisorTrack.MODEL
            and item.status == SupervisorCaseStatus.COMPLETED
        ]
        if len(skill) != 1 or len(model) != 1:
            raise ClosedLoopPackageError(
                "Controlled closed-loop package requires one completed Skill and Model track."
            )
        skill_metrics = skill[0].outcome.metrics
        model_metrics = model[0].outcome.metrics
        try:
            expected = SupervisorScoreSummary(
                skill_initial_score=skill_metrics["initial_score"],
                skill_final_score=skill_metrics["final_score"],
                model_initial_score=model_metrics["held_out_base_score"],
                model_final_score=model_metrics["held_out_candidate_score"],
                composite_initial_score=(
                    skill_metrics["initial_score"]
                    + model_metrics["held_out_base_score"]
                )
                / 2.0,
                composite_final_score=(
                    skill_metrics["final_score"]
                    + model_metrics["held_out_candidate_score"]
                )
                / 2.0,
                composite_gain=(
                    (
                        skill_metrics["final_score"]
                        + model_metrics["held_out_candidate_score"]
                    )
                    - (
                        skill_metrics["initial_score"]
                        + model_metrics["held_out_base_score"]
                    )
                )
                / 2.0,
                escalation_count=sum(
                    item.status == SupervisorCaseStatus.ESCALATED
                    for item in manifest.cases
                ),
            )
        except KeyError as exc:
            raise ClosedLoopPackageError(
                "Completed track outcome is missing frozen score metrics."
            ) from exc
        if expected != manifest.score_summary:
            raise ClosedLoopPackageError(
                "Closed-loop score summary differs from child track outcomes."
            )

    @staticmethod
    def _verify_events(manifest: ClosedLoopEvolutionPackageManifest) -> None:
        events = manifest.events
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise ClosedLoopPackageError("Packaged Supervisor audit chain is broken.")
            expected_hash = SQLiteSupervisorRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                run_id=event.run_id,
                case_id=event.case_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                from_status=event.from_status,
                to_status=event.to_status,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise ClosedLoopPackageError(
                    "Packaged Supervisor audit event content was modified."
                )
            if event.run_id != manifest.run_id:
                raise ClosedLoopPackageError("Supervisor audit contains another run.")
            previous_hash = event.event_hash
        checkpoint = SupervisorCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint != manifest.checkpoint:
            raise ClosedLoopPackageError(
                "Packaged Supervisor events do not match the checkpoint."
            )
        if not events or events[0].event_type != SupervisorEventType.RUN_CREATED:
            raise ClosedLoopPackageError("Supervisor audit does not begin with RUN_CREATED.")
        final_events = [
            item
            for item in events
            if item.event_type == SupervisorEventType.RUN_STATUS_CHANGED
        ]
        if not final_events or final_events[-1].to_status != manifest.run.status.value:
            raise ClosedLoopPackageError(
                "Supervisor audit does not end at the packaged run status."
            )
        for record in manifest.cases:
            case_events = [item for item in events if item.case_id == record.case.case_id]
            event_types = [item.event_type for item in case_events]
            if event_types.count(SupervisorEventType.CASE_ADMITTED) != 1:
                raise ClosedLoopPackageError("Supervisor case admission event is missing or duplicated.")
            if event_types.count(SupervisorEventType.CASE_ROUTED) != 1:
                raise ClosedLoopPackageError("Supervisor case routing event is missing or duplicated.")
            if event_types.count(SupervisorEventType.CASE_CLAIMED) != 1:
                raise ClosedLoopPackageError("Supervisor case claim event is missing or duplicated.")
            terminal_types = {
                SupervisorEventType.CASE_COMPLETED,
                SupervisorEventType.CASE_BLOCKED,
                SupervisorEventType.CASE_ESCALATED,
                SupervisorEventType.CASE_QUARANTINED,
                SupervisorEventType.CASE_FAILED,
            }
            terminal = [item for item in case_events if item.event_type in terminal_types]
            if len(terminal) != 1:
                raise ClosedLoopPackageError(
                    "Supervisor case terminal event is missing or duplicated."
                )
            if terminal[0].payload.get("outcome_hash") != record.outcome.outcome_hash:
                raise ClosedLoopPackageError(
                    "Supervisor case audit outcome hash differs from the record."
                )

    def export_file(
        self,
        manifest: ClosedLoopEvolutionPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ClosedLoopPackageError(
                "Closed-loop package output must not be a symlink."
            )
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(manifest.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def load_file(self, path: str | Path) -> ClosedLoopEvolutionPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ClosedLoopPackageError(
                "Closed-loop package must be a regular non-symlink file."
            )
        try:
            manifest = ClosedLoopEvolutionPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ClosedLoopPackageError("Closed-loop package is invalid.") from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "ClosedLoopEvolutionPackageManager",
    "ClosedLoopEvolutionPackageManifest",
    "ClosedLoopPackageError",
]
