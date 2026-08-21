from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.release.builders import build_release_observation
from evoagent.release.models import ReleaseEvidenceBatch, ReleaseEvidenceError, ReleasePlan


_MAX_FILE_BYTES = 2_000_000
_MAX_JSON_DEPTH = 20
_MAX_JSON_NODES = 100_000
_MAX_ARRAY_ITEMS = 50_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROOT_KEYS = {
    "batch_id", "plan_id", "plan_hash", "stage_id",
    "incumbent_snapshot_id", "challenger_snapshot_id",
    "candidate_traffic_percent", "window_start", "window_end",
    "producer_id", "declared_event_count", "declared_pair_count", "events",
}
_EVENT_KEYS = {
    "event_id", "pair_id", "stage_id", "segment_id", "snapshot_id",
    "success", "error", "safety_violation", "latency_ms",
    "input_tokens", "output_tokens", "cost_usd", "observed_at",
}


class ReleaseEvidenceImporter:
    """Import bounded observable paired evidence from a caller-hashed file."""

    def __init__(self, controlled_root: str | Path):
        raw_root = Path(controlled_root).expanduser()
        if raw_root.is_symlink():
            raise ReleaseEvidenceError("Release evidence root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def import_file(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        plan: ReleasePlan,
    ) -> ReleaseEvidenceBatch:
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise ReleaseEvidenceError("Release evidence requires lowercase SHA-256.")
        target = self._resolve_target(relative_path)
        if target.name != "release-evidence.json":
            raise ReleaseEvidenceError("Release evidence file name is not supported.")
        if not target.is_file() or target.is_symlink():
            raise ReleaseEvidenceError("Release evidence must be a regular file.")
        size = target.stat().st_size
        if size <= 0 or size > _MAX_FILE_BYTES:
            raise ReleaseEvidenceError("Release evidence file size is outside limits.")
        raw = target.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ReleaseEvidenceError("Release evidence file SHA-256 mismatch.")
        try:
            text = raw.decode("utf-8")
            payload = json.loads(text, parse_constant=_reject_non_finite)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ReleaseEvidenceError("Release evidence is not valid UTF-8 JSON.") from exc
        self._inspect_json(payload)
        self._validate_shape(payload)
        validate_safe_content(payload)
        return self._build_batch(payload, source_file_sha256=actual_sha256, plan=plan)

    def _resolve_target(self, relative_path: str) -> Path:
        pure = PurePosixPath(relative_path.replace("\\", "/"))
        if pure.is_absolute() or not pure.parts:
            raise ReleaseEvidenceError("Release evidence path must be relative.")
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise ReleaseEvidenceError("Release evidence path contains unsafe segments.")
        candidate = self.root.joinpath(*pure.parts)
        current = self.root
        for part in pure.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ReleaseEvidenceError("Release evidence path traverses a symlink.")
        resolved = candidate.resolve()
        try:
            common = Path(os.path.commonpath((self.root, resolved)))
        except ValueError as exc:
            raise ReleaseEvidenceError("Release evidence escaped its root.") from exc
        if common != self.root:
            raise ReleaseEvidenceError("Release evidence escaped its controlled root.")
        return resolved

    def _inspect_json(self, payload: Any) -> None:
        nodes = 0

        def visit(value: Any, *, depth: int) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > _MAX_JSON_NODES:
                raise ReleaseEvidenceError("Release evidence JSON has too many nodes.")
            if depth > _MAX_JSON_DEPTH:
                raise ReleaseEvidenceError("Release evidence JSON is too deeply nested.")
            if isinstance(value, dict):
                for item in value.values():
                    visit(item, depth=depth + 1)
            elif isinstance(value, list):
                if len(value) > _MAX_ARRAY_ITEMS:
                    raise ReleaseEvidenceError("Release evidence array is too large.")
                for item in value:
                    visit(item, depth=depth + 1)

        visit(payload, depth=0)

    @staticmethod
    def _validate_shape(payload: Any) -> None:
        if not isinstance(payload, dict) or set(payload) != _ROOT_KEYS:
            raise ReleaseEvidenceError("Release evidence root schema differs.")
        events = payload.get("events")
        if not isinstance(events, list) or not events:
            raise ReleaseEvidenceError("Release evidence requires events.")
        if payload["declared_event_count"] != len(events):
            raise ReleaseEvidenceError("Declared release event count differs.")
        for event in events:
            if not isinstance(event, dict) or set(event) != _EVENT_KEYS:
                raise ReleaseEvidenceError("Release observation schema differs.")

    @staticmethod
    def _build_batch(
        payload: dict[str, Any],
        *,
        source_file_sha256: str,
        plan: ReleasePlan,
    ) -> ReleaseEvidenceBatch:
        try:
            stage = next(item for item in plan.stages if item.stage_id == payload["stage_id"])
        except StopIteration as exc:
            raise ReleaseEvidenceError("Release evidence references an unknown stage.") from exc
        if (
            payload["plan_id"] != plan.plan_id
            or payload["plan_hash"] != plan.plan_hash
            or payload["incumbent_snapshot_id"] != plan.incumbent_snapshot_id
            or payload["challenger_snapshot_id"] != plan.challenger_snapshot_id
            or abs(float(payload["candidate_traffic_percent"]) - stage.candidate_traffic_percent) > 1e-12
        ):
            raise ReleaseEvidenceError("Release evidence differs from the frozen plan.")
        window_start = _parse_datetime(payload["window_start"], "window_start")
        window_end = _parse_datetime(payload["window_end"], "window_end")
        if window_end <= window_start:
            raise ReleaseEvidenceError("Release evidence window is not positive.")
        if (window_end - window_start).total_seconds() > stage.observation_window_seconds:
            raise ReleaseEvidenceError("Release evidence window exceeds its stage.")
        segment_ids = {item.segment_id for item in plan.segments}
        events = []
        for raw in payload["events"]:
            if raw["segment_id"] not in segment_ids:
                raise ReleaseEvidenceError("Release evidence segment is not frozen.")
            observed_at = _parse_datetime(raw["observed_at"], "observed_at")
            try:
                events.append(build_release_observation(
                    event_id=raw["event_id"], pair_id=raw["pair_id"],
                    stage_id=raw["stage_id"], segment_id=raw["segment_id"],
                    snapshot_id=raw["snapshot_id"], success=raw["success"],
                    error=raw["error"], safety_violation=raw["safety_violation"],
                    latency_ms=raw["latency_ms"], input_tokens=raw["input_tokens"],
                    output_tokens=raw["output_tokens"], cost_usd=raw["cost_usd"],
                    observed_at=observed_at,
                ))
            except (TypeError, ValueError) as exc:
                raise ReleaseEvidenceError("Release observation failed validation.") from exc
        pairs = {item.pair_id for item in events}
        if payload["declared_pair_count"] != len(pairs):
            raise ReleaseEvidenceError("Declared release pair count differs.")
        segment_pair_counts: dict[str, int] = {}
        seen_pairs: dict[str, str] = {}
        for event in events:
            if event.pair_id not in seen_pairs:
                seen_pairs[event.pair_id] = event.segment_id
                segment_pair_counts[event.segment_id] = segment_pair_counts.get(event.segment_id, 0) + 1
        batch_payload = {
            "batch_id": payload["batch_id"],
            "source_file_name": "release-evidence.json",
            "source_file_sha256": source_file_sha256,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "stage_id": stage.stage_id,
            "incumbent_snapshot_id": plan.incumbent_snapshot_id,
            "challenger_snapshot_id": plan.challenger_snapshot_id,
            "candidate_traffic_percent": stage.candidate_traffic_percent,
            "window_start": window_start,
            "window_end": window_end,
            "producer_id": payload["producer_id"],
            "events": tuple(events),
            "pair_count": len(pairs),
            "segment_pair_counts": dict(sorted(segment_pair_counts.items())),
            "external_execution_performed_by_evoagent": False,
            "production_traffic_observed_by_evoagent": False,
        }
        try:
            return ReleaseEvidenceBatch(**batch_payload, evidence_hash=canonical_sha256(batch_payload))
        except ValueError as exc:
            raise ReleaseEvidenceError("Release evidence batch integrity failed.") from exc


def _parse_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseEvidenceError(f"Release {label} must be a timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseEvidenceError(f"Release {label} is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseEvidenceError(f"Release {label} requires a timezone.")
    return parsed


def _reject_non_finite(value: str):
    raise ValueError(value)


__all__ = ["ReleaseEvidenceImporter"]