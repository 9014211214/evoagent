from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from evoagent.skills.models import (
    SkillEvaluationDecision,
    SkillEventType,
    SkillLifecycleEvent,
    SkillSpec,
    SkillVersionRecord,
    SkillVersionStatus,
)


def _content_hash(spec: SkillSpec) -> str:
    payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SkillRegistry:
    """In-memory immutable Skill version registry with an auditable active pointer.

    This implementation is deterministic and intentionally not thread-safe. A
    persistent backend should use optimistic locking on the active version.
    """

    def __init__(self):
        self._records: dict[str, dict[str, SkillVersionRecord]] = defaultdict(dict)
        self._active: dict[str, str] = {}
        self._events: list[SkillLifecycleEvent] = []

    def register_initial(self, spec: SkillSpec, *, reason: str = "initial registration") -> None:
        if self._records[spec.skill_id]:
            raise ValueError(f"Skill already registered: {spec.skill_id}")
        record = SkillVersionRecord(
            spec=spec,
            parent_version=None,
            status=SkillVersionStatus.ACTIVE,
            content_hash=_content_hash(spec),
        )
        self._records[spec.skill_id][spec.version] = record
        self._active[spec.skill_id] = spec.version
        self._emit(SkillEventType.REGISTERED, spec, reason=reason)

    def add_candidate(self, spec: SkillSpec, *, parent_version: str, reason: str) -> None:
        versions = self._records[spec.skill_id]
        if spec.version in versions:
            raise ValueError(f"Duplicate Skill version: {spec.skill_id}@{spec.version}")
        if parent_version not in versions:
            raise ValueError(f"Unknown parent version: {spec.skill_id}@{parent_version}")
        record = SkillVersionRecord(
            spec=spec,
            parent_version=parent_version,
            status=SkillVersionStatus.CANDIDATE,
            content_hash=_content_hash(spec),
        )
        versions[spec.version] = record
        self._emit(
            SkillEventType.CANDIDATE_CREATED,
            spec,
            from_version=parent_version,
            to_version=spec.version,
            reason=reason,
        )

    def promote(self, skill_id: str, version: str, decision: SkillEvaluationDecision) -> None:
        if not decision.promote:
            raise ValueError("A rejected evaluation decision cannot promote a candidate.")
        candidate = self._require(skill_id, version)
        if candidate.status != SkillVersionStatus.CANDIDATE:
            raise ValueError("Only candidate versions can be promoted.")
        if decision.skill_id != skill_id or decision.candidate_version != version:
            raise ValueError("Evaluation decision does not match candidate.")

        previous_version = self._active[skill_id]
        previous = self._require(skill_id, previous_version)
        self._records[skill_id][previous_version] = previous.model_copy(
            update={"status": SkillVersionStatus.SUPERSEDED}
        )
        self._records[skill_id][version] = candidate.model_copy(
            update={"status": SkillVersionStatus.ACTIVE, "evaluation": decision}
        )
        self._active[skill_id] = version
        self._emit(
            SkillEventType.PROMOTED,
            candidate.spec,
            from_version=previous_version,
            to_version=version,
            reason=decision.reason,
            metadata={"regression_count": decision.regression_count},
        )

    def reject(self, skill_id: str, version: str, decision: SkillEvaluationDecision) -> None:
        if decision.promote:
            raise ValueError("A passing evaluation decision cannot reject a candidate.")
        candidate = self._require(skill_id, version)
        if candidate.status != SkillVersionStatus.CANDIDATE:
            raise ValueError("Only candidate versions can be rejected.")
        self._records[skill_id][version] = candidate.model_copy(
            update={"status": SkillVersionStatus.REJECTED, "evaluation": decision}
        )
        self._emit(
            SkillEventType.REJECTED,
            candidate.spec,
            reason=decision.reason,
            metadata={"regression_count": decision.regression_count},
        )

    def rollback(self, skill_id: str, to_version: str, *, reason: str) -> None:
        target = self._require(skill_id, to_version)
        if target.status not in {SkillVersionStatus.ACTIVE, SkillVersionStatus.SUPERSEDED}:
            raise ValueError("Rollback target must be a previously active stable version.")
        current_version = self._active[skill_id]
        if current_version == to_version:
            raise ValueError("Requested rollback target is already active.")
        current = self._require(skill_id, current_version)
        self._records[skill_id][current_version] = current.model_copy(
            update={"status": SkillVersionStatus.SUPERSEDED}
        )
        self._records[skill_id][to_version] = target.model_copy(
            update={"status": SkillVersionStatus.ACTIVE}
        )
        self._active[skill_id] = to_version
        self._emit(
            SkillEventType.ROLLED_BACK,
            target.spec,
            from_version=current_version,
            to_version=to_version,
            reason=reason,
        )

    def active(self, skill_id: str) -> SkillVersionRecord:
        return self._require(skill_id, self._active[skill_id]).model_copy(deep=True)

    def get(self, skill_id: str, version: str) -> SkillVersionRecord:
        return self._require(skill_id, version).model_copy(deep=True)

    def list_versions(self, skill_id: str) -> list[SkillVersionRecord]:
        return [record.model_copy(deep=True) for record in self._records[skill_id].values()]

    def events(self, skill_id: str | None = None) -> list[SkillLifecycleEvent]:
        events = self._events if skill_id is None else [e for e in self._events if e.skill_id == skill_id]
        return [event.model_copy(deep=True) for event in events]

    def _require(self, skill_id: str, version: str) -> SkillVersionRecord:
        try:
            return self._records[skill_id][version]
        except KeyError as exc:
            raise KeyError(f"Unknown Skill version: {skill_id}@{version}") from exc

    def _emit(
        self,
        event_type: SkillEventType,
        spec: SkillSpec,
        *,
        reason: str,
        from_version: str | None = None,
        to_version: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        self._events.append(
            SkillLifecycleEvent(
                sequence=len(self._events) + 1,
                event_type=event_type,
                skill_id=spec.skill_id,
                version=spec.version,
                from_version=from_version,
                to_version=to_version,
                reason=reason,
                metadata=metadata or {},
            )
        )
