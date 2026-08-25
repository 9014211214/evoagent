from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evoagent._io import atomic_temporary_path
from evoagent.skills.models import SkillEventType, SkillVersionStatus
from evoagent.skills.persistent_models import SkillRegistryBundle
from evoagent.skills.sqlite_registry import (
    SQLiteSkillRegistry,
    SkillAuditIntegrityError,
    skill_content_hash,
)


_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_GENESIS_HASH = "0" * 64


class SkillStateBundleError(ValueError):
    pass


def _bundle_payload(bundle: SkillRegistryBundle | dict[str, Any]) -> dict[str, Any]:
    payload = (
        bundle.model_dump(mode="json")
        if isinstance(bundle, SkillRegistryBundle)
        else dict(bundle)
    )
    payload.pop("manifest_hash", None)
    return payload


def _manifest_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SECRET_PATTERNS)
    return False


class SkillStateBundleManager:
    def build(self, registry: SQLiteSkillRegistry) -> SkillRegistryBundle:
        registry.verify_audit()
        records = tuple(
            record
            for skill_id in registry.list_skill_ids()
            for record in registry.list_versions(skill_id)
        )
        provisional = SkillRegistryBundle(
            exported_at=datetime.now(timezone.utc),
            records=records,
            active_versions=registry.active_versions(),
            active_revisions=registry.active_revisions(),
            events=tuple(registry.events()),
            manifest_hash="0" * 64,
        )
        payload = _bundle_payload(provisional)
        if _contains_secret(payload):
            raise SkillStateBundleError("Skill state contains a potential secret and cannot be exported.")
        bundle = provisional.model_copy(update={"manifest_hash": _manifest_hash(payload)})
        self.verify(bundle)
        return bundle

    def export_file(self, registry: SQLiteSkillRegistry, path: str | Path) -> SkillRegistryBundle:
        bundle = self.build(registry)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = atomic_temporary_path(destination)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(bundle.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return bundle

    def load_file(self, path: str | Path) -> SkillRegistryBundle:
        try:
            bundle = SkillRegistryBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SkillStateBundleError("Invalid Skill state bundle.") from exc
        self.verify(bundle)
        return bundle

    def verify(self, bundle: SkillRegistryBundle) -> bool:
        payload = _bundle_payload(bundle)
        if _contains_secret(payload):
            raise SkillStateBundleError("Skill state bundle contains a potential secret.")
        if bundle.manifest_hash != _manifest_hash(payload):
            raise SkillStateBundleError("Skill state bundle manifest hash mismatch.")

        records = {(record.spec.skill_id, record.spec.version): record for record in bundle.records}
        if len(records) != len(bundle.records):
            raise SkillStateBundleError("Skill state bundle contains duplicate versions.")

        self._verify_records(records)
        self._verify_active_maps(bundle, records)
        reconstructed_active, pointer_revisions = self._verify_events(bundle, records)
        if reconstructed_active != bundle.active_versions:
            raise SkillStateBundleError("Lifecycle events do not reconstruct the active Skill pointers.")
        if pointer_revisions != bundle.active_revisions:
            raise SkillStateBundleError("Active revisions do not match promotion and rollback events.")
        return True

    def import_into(self, registry: SQLiteSkillRegistry, bundle: SkillRegistryBundle) -> None:
        self.verify(bundle)
        with registry._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                counts = connection.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM skill_versions) AS versions, "
                    "(SELECT COUNT(*) FROM skill_heads) AS heads, "
                    "(SELECT COUNT(*) FROM skill_audit_events) AS events"
                ).fetchone()
                if any(counts[key] for key in ("versions", "heads", "events")):
                    raise SkillStateBundleError(
                        "Skill state can only be imported into a completely empty registry."
                    )

                for record in bundle.records:
                    registry._insert_version(connection, record, created_at=bundle.exported_at)
                for skill_id, active_version in bundle.active_versions.items():
                    connection.execute(
                        "INSERT INTO skill_heads (skill_id, active_version, revision, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            skill_id,
                            active_version,
                            bundle.active_revisions[skill_id],
                            bundle.exported_at.isoformat(),
                        ),
                    )
                for event in bundle.events:
                    connection.execute(
                        "INSERT INTO skill_audit_events (sequence, event_id, event_type, skill_id, "
                        "version, from_version, to_version, reason, metadata_json, actor_id, "
                        "created_at, previous_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            event.sequence,
                            event.event_id,
                            event.event_type.value,
                            event.skill_id,
                            event.version,
                            event.from_version,
                            event.to_version,
                            event.reason,
                            registry._json(event.metadata),
                            event.actor_id,
                            event.created_at.isoformat(),
                            event.previous_hash,
                            event.event_hash,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        try:
            registry.verify_audit()
        except SkillAuditIntegrityError as exc:
            raise SkillStateBundleError("Imported Skill audit verification failed.") from exc

    @staticmethod
    def _verify_records(records) -> None:
        roots: Counter[str] = Counter()
        for key, record in records.items():
            if record.content_hash != skill_content_hash(record.spec):
                raise SkillStateBundleError(f"Skill content hash mismatch: {key[0]}@{key[1]}")
            if record.parent_version is None:
                roots[record.spec.skill_id] += 1
            elif (record.spec.skill_id, record.parent_version) not in records:
                raise SkillStateBundleError(
                    f"Missing parent version: {record.spec.skill_id}@{record.parent_version}"
                )

            if record.status == SkillVersionStatus.CANDIDATE and record.evaluation is not None:
                raise SkillStateBundleError("Unevaluated candidate unexpectedly contains a decision.")
            if record.status == SkillVersionStatus.REJECTED:
                if record.evaluation is None or record.evaluation.promote:
                    raise SkillStateBundleError("Rejected Skill version lacks a rejecting decision.")
            if record.evaluation is not None:
                decision = record.evaluation
                if (
                    decision.skill_id != record.spec.skill_id
                    or decision.candidate_version != record.spec.version
                ):
                    raise SkillStateBundleError("Skill evaluation decision references another version.")

        skill_ids = {skill_id for skill_id, _ in records}
        if any(roots[skill_id] != 1 for skill_id in skill_ids):
            raise SkillStateBundleError("Each Skill must contain exactly one root version.")

        for skill_id, version in records:
            visited: set[str] = set()
            current = version
            while current is not None:
                if current in visited:
                    raise SkillStateBundleError(f"Skill parent graph contains a cycle: {skill_id}")
                visited.add(current)
                current = records[(skill_id, current)].parent_version

    @staticmethod
    def _verify_active_maps(bundle: SkillRegistryBundle, records) -> None:
        skill_ids = {skill_id for skill_id, _ in records}
        if set(bundle.active_versions) != skill_ids or set(bundle.active_revisions) != skill_ids:
            raise SkillStateBundleError("Active Skill maps do not match bundled Skill IDs.")
        for skill_id, active_version in bundle.active_versions.items():
            active_key = (skill_id, active_version)
            if active_key not in records:
                raise SkillStateBundleError(f"Unknown active Skill version: {skill_id}@{active_version}")
            active_records = [
                record
                for (candidate_skill_id, _), record in records.items()
                if candidate_skill_id == skill_id and record.status == SkillVersionStatus.ACTIVE
            ]
            if len(active_records) != 1 or active_records[0].spec.version != active_version:
                raise SkillStateBundleError(f"Invalid active status set for Skill: {skill_id}")
            if bundle.active_revisions[skill_id] < 0:
                raise SkillStateBundleError("Active Skill revision cannot be negative.")

    @staticmethod
    def _verify_events(bundle: SkillRegistryBundle, records):
        previous = _GENESIS_HASH
        registered: set[str] = set()
        created_versions: set[tuple[str, str]] = set()
        reconstructed_active: dict[str, str] = {}
        pointer_revisions: defaultdict[str, int] = defaultdict(int)

        for expected_sequence, event in enumerate(bundle.events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous:
                raise SkillStateBundleError("Skill event sequence or hash chain is broken.")
            key = (event.skill_id, event.version)
            if key not in records:
                raise SkillStateBundleError(
                    f"Skill event references an unknown version: {event.skill_id}@{event.version}"
                )
            expected_hash = SQLiteSkillRegistry._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type.value,
                skill_id=event.skill_id,
                version=event.version,
                from_version=event.from_version,
                to_version=event.to_version,
                reason=event.reason,
                metadata=event.metadata,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise SkillStateBundleError("Skill event content hash mismatch.")

            if event.event_type == SkillEventType.REGISTERED:
                if event.skill_id in registered or records[key].parent_version is not None:
                    raise SkillStateBundleError("Invalid or duplicate Skill registration event.")
                if event.from_version is not None or event.to_version is not None:
                    raise SkillStateBundleError("Registration event must not contain from/to versions.")
                registered.add(event.skill_id)
                created_versions.add(key)
                reconstructed_active[event.skill_id] = event.version
                pointer_revisions[event.skill_id] = 0
            elif event.event_type == SkillEventType.CANDIDATE_CREATED:
                if event.from_version != records[key].parent_version or event.to_version != event.version:
                    raise SkillStateBundleError("Candidate event does not match its parent/version.")
                created_versions.add(key)
            elif event.event_type == SkillEventType.PROMOTED:
                if event.to_version != event.version:
                    raise SkillStateBundleError("Promotion event target does not match its version.")
                if (
                    event.from_version is None
                    or (event.skill_id, event.from_version) not in records
                    or reconstructed_active.get(event.skill_id) != event.from_version
                ):
                    raise SkillStateBundleError("Promotion event does not start from the active version.")
                reconstructed_active[event.skill_id] = event.to_version
                pointer_revisions[event.skill_id] += 1
            elif event.event_type == SkillEventType.REJECTED:
                if records[key].status != SkillVersionStatus.REJECTED:
                    raise SkillStateBundleError("Rejected event does not reference a rejected version.")
            elif event.event_type == SkillEventType.ROLLED_BACK:
                if event.to_version != event.version:
                    raise SkillStateBundleError("Rollback event target does not match its version.")
                if (
                    event.from_version is None
                    or (event.skill_id, event.from_version) not in records
                    or reconstructed_active.get(event.skill_id) != event.from_version
                ):
                    raise SkillStateBundleError("Rollback event does not start from the active version.")
                reconstructed_active[event.skill_id] = event.to_version
                pointer_revisions[event.skill_id] += 1
            else:  # pragma: no cover - enum validation should make this unreachable
                raise SkillStateBundleError(f"Unsupported Skill event type: {event.event_type}")
            previous = event.event_hash

        if registered != {skill_id for skill_id, _ in records}:
            raise SkillStateBundleError("Every Skill must have exactly one registration event.")
        if created_versions != set(records):
            raise SkillStateBundleError("Every Skill version must have a creation event.")
        return reconstructed_active, dict(pointer_revisions)
