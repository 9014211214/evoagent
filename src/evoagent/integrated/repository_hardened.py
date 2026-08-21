from __future__ import annotations

from datetime import datetime
from typing import Any

from evoagent.composite import CompositeStopAction

from .models import (
    IntegratedCaseStatus,
    IntegratedEventType,
    IntegratedRunStatus,
    IntegratedTrack,
)
from .repository import (
    IntegratedAuditIntegrityError,
    IntegratedRepositoryConflictError,
    SQLiteIntegratedEvolutionRepository as _BaseRepository,
    StaleIntegratedRevision,
)


class SQLiteIntegratedEvolutionRepository(_BaseRepository):
    """Final mixed-track queue with canonical, retry-safe audit semantics."""

    def claim_cases(
        self,
        run_id: str,
        *,
        case_ids: tuple[str, ...],
        track: IntegratedTrack,
        actor_id: str,
        expected_run_revision: int,
        now=None,
    ):
        normalized = self._normalize_case_ids(case_ids)
        claimed = tuple(
            sorted(
                self.list_cases(
                    run_id,
                    status=IntegratedCaseStatus.CLAIMED,
                ),
                key=lambda item: item.case.case_id,
            )
        )
        if claimed:
            run = self.get_run(run_id)
            claimed_ids = tuple(item.case.case_id for item in claimed)
            if run.revision != expected_run_revision + 1:
                raise StaleIntegratedRevision(
                    "Applied integrated claim differs from the retried revision."
                )
            if (
                run.status != IntegratedRunStatus.RUNNING
                or claimed_ids != normalized
                or any(
                    item.case.track != track
                    or item.claimed_by != actor_id
                    or item.revision != 1
                    for item in claimed
                )
            ):
                raise IntegratedRepositoryConflictError(
                    "Integrated claim retry differs from the persisted batch."
                )
            return run, claimed
        return super().claim_cases(
            run_id,
            case_ids=normalized,
            track=track,
            actor_id=actor_id,
            expected_run_revision=expected_run_revision,
            now=now,
        )

    def _append_event(
        self,
        connection,
        *,
        run_id: str,
        event_type: IntegratedEventType,
        actor_id: str,
        reason: str,
        metadata: dict[str, Any],
        created_at: datetime,
        case_ids: tuple[str, ...] = (),
    ) -> None:
        last = connection.execute(
            "SELECT created_at FROM integrated_audit_events "
            "WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if last is not None and created_at < datetime.fromisoformat(
            last["created_at"]
        ):
            raise ValueError(
                "Integrated audit write time precedes the prior lifecycle event."
            )
        super()._append_event(
            connection,
            run_id=run_id,
            event_type=event_type,
            actor_id=actor_id,
            reason=reason,
            metadata=metadata,
            created_at=created_at,
            case_ids=case_ids,
        )

    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise IntegratedAuditIntegrityError(message)

    @staticmethod
    def _initial_status(track: IntegratedTrack) -> IntegratedCaseStatus:
        return {
            IntegratedTrack.SKILL: IntegratedCaseStatus.PENDING,
            IntegratedTrack.LOCAL_POLICY: IntegratedCaseStatus.PENDING,
            IntegratedTrack.ESCALATION: IntegratedCaseStatus.ESCALATED,
            IntegratedTrack.QUARANTINE: IntegratedCaseStatus.QUARANTINED,
        }[track]

    def _verify_semantic_events(self, run_id: str) -> None:
        events = self.events(run_id)
        run = self.get_run(run_id)
        cases = self.list_cases(run_id)
        results = self.list_results(run_id)
        with self._connection() as connection:
            row = self._require_run_row(connection, run_id)
            created_by = row["created_by"]
            completed_by = row["completed_by"]

        self._require(
            run.status != IntegratedRunStatus.FAILED,
            "Integrated FAILED state lacks a governed failure lifecycle.",
        )
        self._require(
            bool(events)
            and events[0].event_type == IntegratedEventType.RUN_CREATED,
            "Integrated audit lacks the run creation event.",
        )
        self._require(
            all(
                current.created_at >= previous.created_at
                for previous, current in zip(events, events[1:])
            ),
            "Integrated audit chronology is not monotonic.",
        )

        created = events[0]
        self._require(
            not created.case_ids
            and created.reason
            == "Integrated multi-track evolution run created."
            and created.metadata
            == {
                "lineage_id": run.lineage_id,
                "policy_hash": run.policy.policy_hash,
            }
            and created.actor_id == created_by
            and created.created_at == run.created_at,
            "Integrated run creation audit semantics differ.",
        )

        case_by_id = {item.case.case_id: item for item in cases}
        admissions = tuple(
            item
            for item in events
            if item.event_type == IntegratedEventType.CASE_ADMITTED
        )
        admission_by_case_id = {}
        for event in admissions:
            self._require(
                len(event.case_ids) == 1,
                "Integrated case admission contains another case set.",
            )
            case_id = event.case_ids[0]
            case = case_by_id.get(case_id)
            self._require(
                case_id not in admission_by_case_id,
                "Integrated audit omits, duplicates, or misbinds case admission.",
            )
            self._require(
                case is not None
                and event.reason == "Attributed mixed-track case admitted."
                and event.metadata
                == {
                    "case_hash": case.case.case_hash,
                    "track": case.case.track.value,
                    "status": self._initial_status(case.case.track).value,
                }
                and event.created_at == case.created_at
                and event.created_at >= case.case.created_at,
                "Integrated case admission audit semantics differ.",
            )
            admission_by_case_id[case_id] = event
        self._require(
            set(admission_by_case_id) == set(case_by_id),
            "Integrated audit omits, duplicates, or misbinds case admission.",
        )

        claims = tuple(
            item
            for item in events
            if item.event_type == IntegratedEventType.CASES_CLAIMED
        )
        result_events = tuple(
            item
            for item in events
            if item.event_type
            == IntegratedEventType.TRACK_RESULT_RECORDED
        )
        completions = tuple(
            item
            for item in events
            if item.event_type == IntegratedEventType.RUN_COMPLETED
        )
        claimed_records = tuple(
            item
            for item in cases
            if item.status == IntegratedCaseStatus.CLAIMED
        )
        terminal = run.status in {
            IntegratedRunStatus.STOPPED,
            IntegratedRunStatus.ESCALATED,
        }
        expected_claim_count = len(results) + bool(claimed_records)
        expected_event_count = (
            1
            + len(cases)
            + expected_claim_count
            + len(results)
            + terminal
        )
        self._require(
            len(claims) == expected_claim_count
            and len(result_events) == len(results)
            and len(completions) == terminal
            and len(events) == expected_event_count,
            "Integrated audit differs from the exact persisted lifecycle.",
        )
        self._require(
            terminal or completed_by is None,
            "Integrated non-terminal run contains a completion actor.",
        )
        expected_updated_at = run.updated_at
        if run.status == IntegratedRunStatus.RUNNING:
            expected_updated_at = claims[-1].created_at
        elif run.status == IntegratedRunStatus.OPEN:
            expected_updated_at = (
                result_events[-1].created_at
                if result_events
                else run.created_at
            )
        self._require(
            run.updated_at == expected_updated_at,
            "Integrated run update time differs from its lifecycle audit.",
        )

        result_by_id = {item.result_id: item for item in results}
        observed_result_ids = set()
        for index, (claim, result_event) in enumerate(
            zip(claims, result_events, strict=False)
        ):
            result_id = result_event.metadata.get("result_id")
            result = result_by_id.get(result_id)
            claim_revision = 2 * index
            result_revision = claim_revision + 1
            self._require(
                result is not None and result_id not in observed_result_ids,
                "Integrated result audit omits or duplicates evidence.",
            )
            observed_result_ids.add(result_id)
            self._require(
                claim.case_ids == result.case_ids
                and claim.actor_id == result.executor_id
                and claim.reason
                == "Governed mixed-track execution batch claimed."
                and claim.metadata
                == {
                    "track": result.track.value,
                    "active_revision_before": claim_revision,
                }
                and result_event.case_ids == result.case_ids
                and result_event.actor_id == result.executor_id
                and result_event.reason
                == "Governed mixed-track execution result recorded."
                and result_event.metadata
                == {
                    "result_id": result.result_id,
                    "result_hash": result.result_hash,
                    "track": result.track.value,
                    "component_ref": result.component_ref,
                    "component_hash": result.component_hash,
                    "active_revision_before": result_revision,
                }
                and claim.sequence < result_event.sequence
                and claim.created_at <= result.started_at
                and result.completed_at <= result_event.created_at,
                "Integrated claim/result audit semantics differ.",
            )
            if index + 1 < len(claims):
                self._require(
                    result_event.sequence < claims[index + 1].sequence,
                    "Integrated execution audits overlap or are reordered.",
                )
            for case_id in result.case_ids:
                case = case_by_id.get(case_id)
                admission = admission_by_case_id.get(case_id)
                self._require(
                    case is not None
                    and admission is not None
                    and admission.sequence < claim.sequence
                    and case.status == IntegratedCaseStatus.COMPLETED
                    and case.claimed_by == result.executor_id
                    and case.result_id == result.result_id
                    and case.revision == 2
                    and case.created_at <= claim.created_at
                    and case.updated_at == result_event.created_at,
                    "Completed integrated case differs from its lifecycle audit.",
                )
        self._require(
            observed_result_ids == set(result_by_id),
            "Integrated result audit set differs from persisted results.",
        )

        if claimed_records:
            claim = claims[-1]
            tracks = {item.case.track for item in claimed_records}
            actors = {item.claimed_by for item in claimed_records}
            claimed_ids = tuple(
                sorted(item.case.case_id for item in claimed_records)
            )
            self._require(
                len(tracks) == 1 and len(actors) == 1,
                "In-flight integrated claim contains mixed evidence.",
            )
            track = next(iter(tracks))
            actor = next(iter(actors))
            self._require(
                run.status == IntegratedRunStatus.RUNNING
                and claim.case_ids == claimed_ids
                and claim.actor_id == actor
                and claim.reason
                == "Governed mixed-track execution batch claimed."
                and claim.metadata
                == {
                    "track": track.value,
                    "active_revision_before": 2 * len(results),
                }
                and all(
                    item.revision == 1
                    and item.updated_at == claim.created_at
                    and item.created_at <= claim.created_at
                    and admission_by_case_id[
                        item.case.case_id
                    ].sequence
                    < claim.sequence
                    for item in claimed_records
                ),
                "In-flight integrated claim differs from crash-recovery evidence.",
            )

        completed_case_ids = {
            case_id for result in results for case_id in result.case_ids
        }
        claimed_case_ids = {item.case.case_id for item in claimed_records}
        for case in cases:
            case_id = case.case.case_id
            if case_id in completed_case_ids or case_id in claimed_case_ids:
                continue
            self._require(
                case.status == self._initial_status(case.case.track)
                and case.claimed_by is None
                and case.result_id is None
                and case.revision == 0
                and case.updated_at == case.created_at,
                "Unexecuted integrated case differs from admission state.",
            )

        if terminal:
            completed = completions[0]
            action = (
                CompositeStopAction.STOP.value
                if run.status == IntegratedRunStatus.STOPPED
                else CompositeStopAction.ESCALATE.value
            )
            pending_count = sum(
                item.status == IntegratedCaseStatus.PENDING
                for item in cases
            )
            self._require(
                not completed.case_ids
                and set(completed.metadata)
                == {
                    "decision_hash",
                    "decision_action",
                    "snapshot_id",
                    "pending_case_count",
                    "active_revision_before",
                }
                and completed.reason
                == "Integrated multi-track evolution run completed."
                and completed.metadata["decision_hash"]
                == run.terminal_decision_hash
                and completed.metadata["decision_action"] == action
                and isinstance(completed.metadata["snapshot_id"], str)
                and bool(completed.metadata["snapshot_id"].strip())
                and completed.metadata["pending_case_count"]
                == pending_count
                and completed.metadata["active_revision_before"]
                == 2 * len(results)
                and completed.actor_id == completed_by
                and completed.created_at == run.completed_at
                and completed.created_at == run.updated_at
                and completed.sequence == events[-1].sequence,
                "Integrated run completion audit semantics differ.",
            )

    def _pending_records(
        self,
        connection,
        run_id: str,
        track: IntegratedTrack | None = None,
    ):
        query = (
            "SELECT * FROM integrated_cases WHERE run_id = ? AND status = ?"
        )
        values = [run_id, IntegratedCaseStatus.PENDING.value]
        if track is not None:
            query += " AND track = ?"
            values.append(track.value)
        query += " ORDER BY case_id"
        rows = connection.execute(query, tuple(values)).fetchall()
        return tuple(self._row_to_case(row) for row in rows)

    @staticmethod
    def _claimed_rows(connection, run_id: str):
        return connection.execute(
            "SELECT * FROM integrated_cases WHERE run_id = ? AND status = ? "
            "ORDER BY case_id",
            (run_id, IntegratedCaseStatus.CLAIMED.value),
        ).fetchall()


__all__ = [
    "IntegratedAuditIntegrityError",
    "IntegratedRepositoryConflictError",
    "SQLiteIntegratedEvolutionRepository",
    "StaleIntegratedRevision",
]
