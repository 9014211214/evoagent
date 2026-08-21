from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_UPSTREAM_COMMIT = "9e3daa339987c3cfa624121e1be442593a53d43c"


class SkillEvolBenchMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    learning_sr: float = Field(ge=0.0, le=1.0)
    evaluation_sr: float = Field(ge=0.0, le=1.0)
    overall_sr: float = Field(ge=0.0, le=1.0)
    context_shift_sr: float = Field(ge=0.0, le=1.0)
    adversarial_sr: float = Field(ge=0.0, le=1.0)
    composition_sr: float = Field(ge=0.0, le=1.0)
    final_retention_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    forgetting_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    negative_transfer_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    revision_hurt_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    active_skill_count: int = Field(default=0, ge=0)


class SkillEvolBenchEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: str = "evoagent-skillevolbench-evidence-v1"
    upstream_repository: str = "AIoT-MLSys-Lab/SkillEvolBench"
    upstream_commit: str = _UPSTREAM_COMMIT
    source_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_id: str
    baseline_name: str
    strategy_name: str
    order_seed: str
    n_tasks_attempted: int = Field(gt=0)
    metrics: SkillEvolBenchMetrics
    imported_at: datetime
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    official_submission_performed: bool = False
    official_leaderboard_claimed: bool = False

    @field_validator("imported_at")
    @classmethod
    def _timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SkillEvolBench evidence time must include a timezone.")
        return value

    @model_validator(mode="after")
    def _verify_hash(self):
        if self.official_submission_performed or self.official_leaderboard_claimed:
            raise ValueError("Local import cannot claim official SkillEvolBench submission.")
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != _canonical_sha256(payload):
            raise ValueError("SkillEvolBench evidence hash mismatch.")
        return self


class SkillEvolBenchImportError(ValueError):
    pass


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _metric(mapping: dict[str, Any], key: str) -> float:
    value = mapping.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SkillEvolBenchImportError(f"Missing numeric SkillEvolBench metric: {key}")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise SkillEvolBenchImportError(f"SkillEvolBench metric out of range: {key}")
    return value


def _optional_metric(mapping: dict[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SkillEvolBenchImportError(f"Invalid SkillEvolBench metric: {key}")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise SkillEvolBenchImportError(f"SkillEvolBench metric out of range: {key}")
    return value


def import_skillevolbench_report(
    report_path: str | Path,
    *,
    expected_sha256: str,
    imported_at: datetime | None = None,
) -> SkillEvolBenchEvidence:
    path = Path(report_path)
    if path.is_symlink() or not path.is_file():
        raise SkillEvolBenchImportError("SkillEvolBench report must be a regular file.")
    if path.name != "full_report.json":
        raise SkillEvolBenchImportError("Expected SkillEvolBench reports/full_report.json.")
    raw = path.read_bytes()
    actual_sha256 = _sha256_bytes(raw)
    if actual_sha256 != expected_sha256:
        raise SkillEvolBenchImportError("SkillEvolBench report SHA-256 mismatch.")
    if len(raw) > 2_000_000:
        raise SkillEvolBenchImportError("SkillEvolBench report exceeds the import size limit.")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillEvolBenchImportError("SkillEvolBench report is not valid UTF-8 JSON.") from exc
    if not isinstance(document, dict):
        raise SkillEvolBenchImportError("SkillEvolBench report root must be an object.")

    task_success = document.get("task_success")
    revision_safety = document.get("revision_safety") or {}
    transfer = document.get("transfer") or {}
    library_health = document.get("library_health") or {}
    if not isinstance(task_success, dict):
        raise SkillEvolBenchImportError("SkillEvolBench report lacks task_success metrics.")
    for mapping, label in (
        (revision_safety, "revision_safety"),
        (transfer, "transfer"),
        (library_health, "library_health"),
    ):
        if not isinstance(mapping, dict):
            raise SkillEvolBenchImportError(f"SkillEvolBench {label} must be an object.")

    run_id = document.get("run_id")
    baseline_name = document.get("baseline_name")
    strategy_name = document.get("strategy_name")
    order_seed = document.get("order_seed")
    n_tasks_attempted = document.get("n_tasks_attempted")
    if not all(isinstance(value, str) and value.strip() for value in (run_id, baseline_name, strategy_name, order_seed)):
        raise SkillEvolBenchImportError("SkillEvolBench report lacks required run identity fields.")
    if not isinstance(n_tasks_attempted, int) or isinstance(n_tasks_attempted, bool) or n_tasks_attempted <= 0:
        raise SkillEvolBenchImportError("SkillEvolBench n_tasks_attempted must be positive.")

    active_skill_count = library_health.get("active_skill_count", 0)
    if not isinstance(active_skill_count, int) or isinstance(active_skill_count, bool) or active_skill_count < 0:
        raise SkillEvolBenchImportError("SkillEvolBench active_skill_count must be non-negative.")

    metrics = SkillEvolBenchMetrics(
        learning_sr=_metric(task_success, "learning_sr"),
        evaluation_sr=_metric(task_success, "evaluation_sr"),
        overall_sr=_metric(task_success, "overall_sr"),
        context_shift_sr=_metric(task_success, "t4_transfer"),
        adversarial_sr=_metric(task_success, "t5_pass_rate"),
        composition_sr=_metric(task_success, "t6_composition_rate"),
        final_retention_rate=_optional_metric(transfer, "final_retention_rate"),
        forgetting_rate=_optional_metric(transfer, "forgetting_rate"),
        negative_transfer_rate=_optional_metric(transfer, "negative_transfer_rate"),
        revision_hurt_rate=_optional_metric(revision_safety, "revision_hurt_rate"),
        active_skill_count=active_skill_count,
    )
    effective_time = imported_at or datetime.now(timezone.utc)
    canonical_imported_at = (
        effective_time.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    payload = {
        "format_version": "evoagent-skillevolbench-evidence-v1",
        "upstream_repository": "AIoT-MLSys-Lab/SkillEvolBench",
        "upstream_commit": _UPSTREAM_COMMIT,
        "source_report_sha256": actual_sha256,
        "run_id": run_id,
        "baseline_name": baseline_name,
        "strategy_name": strategy_name,
        "order_seed": order_seed,
        "n_tasks_attempted": n_tasks_attempted,
        "metrics": metrics.model_dump(mode="json"),
        "imported_at": canonical_imported_at,
        "official_submission_performed": False,
        "official_leaderboard_claimed": False,
    }
    return SkillEvolBenchEvidence(**payload, evidence_hash=_canonical_sha256(payload))


def compare_skillevolbench_runs(
    baseline: SkillEvolBenchEvidence,
    evolved: SkillEvolBenchEvidence,
) -> dict[str, float]:
    if baseline.order_seed != evolved.order_seed:
        raise SkillEvolBenchImportError("SkillEvolBench comparison requires the same order seed.")
    return {
        "learning_sr_delta": evolved.metrics.learning_sr - baseline.metrics.learning_sr,
        "evaluation_sr_delta": evolved.metrics.evaluation_sr - baseline.metrics.evaluation_sr,
        "overall_sr_delta": evolved.metrics.overall_sr - baseline.metrics.overall_sr,
        "context_shift_delta": evolved.metrics.context_shift_sr - baseline.metrics.context_shift_sr,
        "adversarial_delta": evolved.metrics.adversarial_sr - baseline.metrics.adversarial_sr,
        "composition_delta": evolved.metrics.composition_sr - baseline.metrics.composition_sr,
    }


__all__ = [
    "SkillEvolBenchEvidence",
    "SkillEvolBenchImportError",
    "SkillEvolBenchMetrics",
    "compare_skillevolbench_runs",
    "import_skillevolbench_report",
]
