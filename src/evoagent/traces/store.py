from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evoagent.domain.models import ExecutionTrace
from evoagent.traces.models import TraceCheckpoint, TraceEnvelope, TraceTrustLevel


_GENESIS_HASH = "0" * 64
_FORBIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "raw_reasoning",
    "private_reasoning",
    "scratchpad",
}


class TraceIntegrityError(RuntimeError):
    pass


class DuplicateTraceError(ValueError):
    pass


class TracePolicyError(ValueError):
    pass


class JsonlTraceStore:
    """Append-only JSONL trace store with a SHA-256 hash chain.

    The chain is tamper-evident, not tamper-proof. For multi-process production
    use, place the file behind a transactional store or external locking layer.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def append(
        self,
        trace: ExecutionTrace,
        *,
        source: str,
        trust_level: TraceTrustLevel,
        safety_flags: tuple[str, ...] = (),
    ) -> TraceEnvelope:
        self._enforce_observable_only(trace)
        with self._lock:
            records = self._load_verified_unlocked()
            if any(item.trace.trace_id == trace.trace_id for item in records):
                raise DuplicateTraceError(f"Duplicate trace ID: {trace.trace_id}")

            sequence = len(records) + 1
            previous_hash = records[-1].record_hash if records else _GENESIS_HASH
            created_at = datetime.now(timezone.utc)
            record_hash = self._hash_payload(
                sequence=sequence,
                previous_hash=previous_hash,
                created_at=created_at,
                trace=trace,
                source=source,
                trust_level=trust_level,
                safety_flags=safety_flags,
            )
            envelope = TraceEnvelope(
                sequence=sequence,
                previous_hash=previous_hash,
                record_hash=record_hash,
                created_at=created_at,
                trace=trace,
                source=source,
                trust_level=trust_level,
                safety_flags=safety_flags,
            )
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(envelope.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return envelope

    def list(self) -> list[TraceEnvelope]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._load_verified_unlocked()]

    def get(self, trace_id: str) -> TraceEnvelope:
        for item in self.list():
            if item.trace.trace_id == trace_id:
                return item
        raise KeyError(f"Unknown trace ID: {trace_id}")

    def query(
        self,
        *,
        task_type: str | None = None,
        model_id: str | None = None,
        skill_id: str | None = None,
        skill_version: str | None = None,
        verifier_passed: bool | None = None,
        trust_level: TraceTrustLevel | None = None,
    ) -> list[TraceEnvelope]:
        result: list[TraceEnvelope] = []
        for item in self.list():
            trace = item.trace
            if task_type is not None and trace.task.task_type != task_type:
                continue
            if model_id is not None and trace.model_id != model_id:
                continue
            if skill_id is not None and trace.skill_id != skill_id:
                continue
            if skill_version is not None and trace.skill_version != skill_version:
                continue
            if verifier_passed is not None and trace.verifier_passed is not verifier_passed:
                continue
            if trust_level is not None and item.trust_level != trust_level:
                continue
            result.append(item)
        return result

    def checkpoint(self) -> TraceCheckpoint:
        with self._lock:
            records = self._load_verified_unlocked()
            return TraceCheckpoint(
                record_count=len(records),
                head_hash=records[-1].record_hash if records else _GENESIS_HASH,
            )

    def verify(self, checkpoint: TraceCheckpoint | None = None) -> bool:
        with self._lock:
            records = self._load_verified_unlocked()
            if checkpoint is not None:
                current = TraceCheckpoint(
                    record_count=len(records),
                    head_hash=records[-1].record_hash if records else _GENESIS_HASH,
                )
                if current != checkpoint:
                    raise TraceIntegrityError(
                        "Trace store does not match the externally anchored checkpoint."
                    )
        return True

    def _load_verified_unlocked(self) -> list[TraceEnvelope]:
        if not self.path.exists():
            return []

        records: list[TraceEnvelope] = []
        expected_previous = _GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    envelope = TraceEnvelope.model_validate_json(line)
                except (ValidationError, ValueError) as exc:
                    raise TraceIntegrityError(
                        f"Invalid trace envelope at line {line_number}."
                    ) from exc
                expected_sequence = len(records) + 1
                if envelope.sequence != expected_sequence:
                    raise TraceIntegrityError(
                        f"Unexpected trace sequence at line {line_number}: "
                        f"expected {expected_sequence}, got {envelope.sequence}."
                    )
                if envelope.previous_hash != expected_previous:
                    raise TraceIntegrityError(
                        f"Broken trace hash chain at line {line_number}."
                    )
                expected_hash = self._hash_payload(
                    sequence=envelope.sequence,
                    previous_hash=envelope.previous_hash,
                    created_at=envelope.created_at,
                    trace=envelope.trace,
                    source=envelope.source,
                    trust_level=envelope.trust_level,
                    safety_flags=envelope.safety_flags,
                )
                if envelope.record_hash != expected_hash:
                    raise TraceIntegrityError(
                        f"Trace content hash mismatch at line {line_number}."
                    )
                self._enforce_observable_only(envelope.trace)
                records.append(envelope)
                expected_previous = envelope.record_hash
        return records

    @staticmethod
    def _hash_payload(
        *,
        sequence: int,
        previous_hash: str,
        created_at: datetime,
        trace: ExecutionTrace,
        source: str,
        trust_level: TraceTrustLevel,
        safety_flags: tuple[str, ...],
    ) -> str:
        payload = {
            "sequence": sequence,
            "previous_hash": previous_hash,
            "created_at": created_at.isoformat(),
            "trace": trace.model_dump(mode="json"),
            "source": source,
            "trust_level": trust_level.value,
            "safety_flags": list(safety_flags),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _enforce_observable_only(cls, trace: ExecutionTrace) -> None:
        forbidden = cls._find_forbidden_keys(trace.model_dump(mode="json"))
        if forbidden:
            raise TracePolicyError(
                "Trace contains forbidden hidden-reasoning fields: " + ", ".join(sorted(forbidden))
            )

    @classmethod
    def _find_forbidden_keys(cls, value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).strip().lower()
                if normalized in _FORBIDDEN_REASONING_KEYS:
                    found.add(normalized)
                found.update(cls._find_forbidden_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.update(cls._find_forbidden_keys(item))
        return found
