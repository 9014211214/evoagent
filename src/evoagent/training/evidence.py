from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.diagnosis import AttributionReport, ExperimentType
from evoagent.domain.models import EvolutionAction, ExecutionTrace, FailureLayer, Task
from evoagent.training.models import DatasetSignals


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_EXTERNAL_LAYERS = (
    FailureLayer.SKILL,
    FailureLayer.ROUTER,
    FailureLayer.TOOL,
    FailureLayer.CONTEXT,
    FailureLayer.VERIFIER,
    FailureLayer.ENVIRONMENT,
)
_EXTERNAL_EXPERIMENTS = {
    ExperimentType.REPLACE_SKILL: FailureLayer.SKILL,
    ExperimentType.FORCE_ROUTER: FailureLayer.ROUTER,
    ExperimentType.REPLAY_TOOL: FailureLayer.TOOL,
    ExperimentType.COMPLETE_CONTEXT: FailureLayer.CONTEXT,
    ExperimentType.ORACLE_VERIFIER: FailureLayer.VERIFIER,
    ExperimentType.RESET_ENVIRONMENT: FailureLayer.ENVIRONMENT,
}
_FORBIDDEN_KEYS = {
    "chain_of_thought",
    "scratchpad",
    "hidden_reasoning",
    "reasoning_content",
    "traceback",
    "stack_trace",
}
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|api[_-]?key|access[_-]?token|auth[_-]?token|secret|private[_-]?key)\s*[:=]"
)


class ModelEvidenceDatasetError(ValueError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _validate_safe(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise ModelEvidenceDatasetError(
                    f"Forbidden hidden-reasoning field in model evidence: {path}.{key}"
                )
            _validate_safe(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if _SECRET_ASSIGNMENT.search(value) or any(
            pattern.search(value) for pattern in _SECRET_PATTERNS
        ):
            raise ModelEvidenceDatasetError(
                f"Potential secret in model evidence at {path}."
            )


def _normalized_cost(trace: ExecutionTrace) -> dict[str, float]:
    allowed = ("steps", "tool_calls", "llm_tokens", "cost_usd")
    return {key: float(trace.cost.get(key, 0.0)) for key in allowed}


def _event_subset(trace: ExecutionTrace, event_name: str, payload_key: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(event[payload_key])
        for event in trace.observable_events
        if event.get("event") == event_name and isinstance(event.get(payload_key), dict)
    )


class ObservableTrajectoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id: str
    model_id: str
    skill_id: str | None = None
    skill_version: str | None = None
    verifier_passed: bool
    verifier_feedback: str
    observable_events: tuple[dict[str, Any], ...]
    final_output: dict[str, Any]
    cost: dict[str, float]
    trace_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash_and_content(self):
        payload = self.model_dump(mode="json", exclude={"trace_hash"})
        _validate_safe(payload)
        if self.trace_hash != canonical_sha256(payload):
            raise ValueError("Observable trajectory hash mismatch.")
        if "wall_seconds" in self.cost:
            raise ValueError("Nondeterministic wall time must not enter model evidence.")
        return self

    @classmethod
    def from_trace(cls, trace: ExecutionTrace) -> "ObservableTrajectoryRecord":
        payload = {
            "trace_id": trace.trace_id,
            "model_id": trace.model_id,
            "skill_id": trace.skill_id,
            "skill_version": trace.skill_version,
            "verifier_passed": trace.verifier_passed,
            "verifier_feedback": trace.verifier_feedback,
            "observable_events": tuple(dict(item) for item in trace.observable_events),
            "final_output": dict(trace.final_output),
            "cost": _normalized_cost(trace),
        }
        _validate_safe(payload)
        return cls(**payload, trace_hash=canonical_sha256(payload))


class ModelEvidenceExample(BaseModel):
    model_config = ConfigDict(frozen=True)

    example_id: str
    problem_cluster: str
    task: Task
    base_model_id: str
    reference_model_id: str
    failed: ObservableTrajectoryRecord
    reference: ObservableTrajectoryRecord
    ruled_out_layers: tuple[FailureLayer, ...]
    attribution_hash: str = Field(pattern=_SHA256_PATTERN)
    record_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_example(self):
        if self.failed.verifier_passed:
            raise ValueError("Model evidence baseline must fail verification.")
        if not self.reference.verifier_passed:
            raise ValueError("Model evidence reference trajectory must pass verification.")
        if self.failed.model_id != self.base_model_id:
            raise ValueError("Failed trajectory does not use the declared base model.")
        if self.reference.model_id != self.reference_model_id:
            raise ValueError("Reference trajectory does not use the declared reference model.")
        if self.base_model_id == self.reference_model_id:
            raise ValueError("Reference model must differ from the incapable base model.")
        if self.ruled_out_layers != _EXTERNAL_LAYERS:
            raise ValueError("Model evidence must rule out every external failure layer.")
        payload = self.model_dump(mode="json", exclude={"record_hash"})
        _validate_safe(payload)
        if self.record_hash != canonical_sha256(payload):
            raise ValueError("Model evidence record hash mismatch.")
        return self

    @classmethod
    def build(
        cls,
        *,
        report: AttributionReport,
        failed_trace: ExecutionTrace,
        reference_trace: ExecutionTrace,
        problem_cluster: str,
    ) -> "ModelEvidenceExample":
        if (
            report.root_cause_layer != FailureLayer.MODEL
            or report.recommended_action != EvolutionAction.TRAIN_MODEL
            or not report.actionable
        ):
            raise ModelEvidenceDatasetError(
                "Only actionable executable Model attribution may enter the dataset."
            )
        if failed_trace.task != reference_trace.task:
            raise ModelEvidenceDatasetError(
                "Failed and reference trajectories must use the exact same frozen Task."
            )
        if failed_trace.verifier_passed or not reference_trace.verifier_passed:
            raise ModelEvidenceDatasetError(
                "Model evidence requires a failed baseline and successful reference replay."
            )

        supported = [item.experiment_type for item in report.experiments if item.supports_hypothesis]
        if supported != [ExperimentType.REFERENCE_MODEL]:
            raise ModelEvidenceDatasetError(
                "Model evidence requires reference_model as the only successful intervention."
            )
        ruled_out: list[FailureLayer] = []
        for experiment_type, layer in _EXTERNAL_EXPERIMENTS.items():
            matches = [
                item for item in report.experiments if item.experiment_type == experiment_type
            ]
            if len(matches) != 1 or matches[0].supports_hypothesis:
                raise ModelEvidenceDatasetError(
                    f"External layer was not ruled out by executable replay: {layer.value}"
                )
            ruled_out.append(layer)

        failed = ObservableTrajectoryRecord.from_trace(failed_trace)
        reference = ObservableTrajectoryRecord.from_trace(reference_trace)
        attribution_payload = report.model_dump(mode="json")
        _validate_safe(attribution_payload)
        attribution_hash = canonical_sha256(attribution_payload)
        payload = {
            "example_id": f"model-evidence:{failed_trace.task.task_id}",
            "problem_cluster": problem_cluster,
            "task": failed_trace.task,
            "base_model_id": failed_trace.model_id,
            "reference_model_id": reference_trace.model_id,
            "failed": failed,
            "reference": reference,
            "ruled_out_layers": tuple(ruled_out),
            "attribution_hash": attribution_hash,
        }
        return cls(**payload, record_hash=canonical_sha256(payload))


class SupervisedTrajectoryExample(BaseModel):
    model_config = ConfigDict(frozen=True)

    example_id: str
    task: Task
    reference_actions: tuple[dict[str, Any], ...]
    reference_tool_results: tuple[dict[str, Any], ...]
    reference_final_output: dict[str, Any]
    record_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_record(self):
        if not self.reference_actions:
            raise ValueError("SFT evidence requires at least one reference Agent action.")
        payload = self.model_dump(mode="json", exclude={"record_hash"})
        _validate_safe(payload)
        if self.record_hash != canonical_sha256(payload):
            raise ValueError("SFT record hash mismatch.")
        return self


class PreferenceTrajectoryPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    example_id: str
    task: Task
    chosen_actions: tuple[dict[str, Any], ...]
    rejected_actions: tuple[dict[str, Any], ...]
    chosen_final_output: dict[str, Any]
    rejected_final_output: dict[str, Any]
    record_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_record(self):
        if not self.chosen_actions or not self.rejected_actions:
            raise ValueError("Preference evidence requires chosen and rejected actions.")
        payload = self.model_dump(mode="json", exclude={"record_hash"})
        _validate_safe(payload)
        if self.record_hash != canonical_sha256(payload):
            raise ValueError("Preference record hash mismatch.")
        return self


class ReplaySeedRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    example_id: str
    task: Task
    environment_id: str
    seed: int
    record_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_record(self):
        payload = self.model_dump(mode="json", exclude={"record_hash"})
        _validate_safe(payload)
        if self.record_hash != canonical_sha256(payload):
            raise ValueError("Replay seed hash mismatch.")
        return self


class ModelEvidenceDatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-model-evidence-v1"] = "evoagent-model-evidence-v1"
    created_at: datetime
    base_model_id: str
    problem_cluster: str
    environment_id: str
    verifier_id: str
    evidence_task_ids: tuple[str, ...]
    held_out_task_ids: tuple[str, ...]
    examples: tuple[ModelEvidenceExample, ...]
    supervised_examples: tuple[SupervisedTrajectoryExample, ...]
    preference_pairs: tuple[PreferenceTrajectoryPair, ...]
    replay_seeds: tuple[ReplaySeedRecord, ...]
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Dataset creation time must include a timezone.")
        return value


class ModelEvidenceDatasetManager:
    def build(
        self,
        *,
        examples: tuple[ModelEvidenceExample, ...],
        held_out_task_ids: tuple[str, ...],
        environment_id: str,
        verifier_id: str,
        created_at: datetime,
        replay_seed: int,
    ) -> ModelEvidenceDatasetManifest:
        if not examples:
            raise ModelEvidenceDatasetError("Model evidence dataset cannot be empty.")
        task_ids = tuple(item.task.task_id for item in examples)
        if len(set(task_ids)) != len(task_ids):
            raise ModelEvidenceDatasetError("Model evidence Task IDs must be distinct.")
        if len(set(held_out_task_ids)) != len(held_out_task_ids):
            raise ModelEvidenceDatasetError("Held-out Task IDs must be distinct.")
        if set(task_ids) & set(held_out_task_ids):
            raise ModelEvidenceDatasetError(
                "Model evidence and held-out evaluation Tasks must be disjoint."
            )
        base_models = {item.base_model_id for item in examples}
        clusters = {item.problem_cluster for item in examples}
        if len(base_models) != 1 or len(clusters) != 1:
            raise ModelEvidenceDatasetError(
                "All model evidence examples must share one base model and problem cluster."
            )

        supervised: list[SupervisedTrajectoryExample] = []
        preferences: list[PreferenceTrajectoryPair] = []
        seeds: list[ReplaySeedRecord] = []
        for item in examples:
            reference_actions = tuple(
                dict(event["action"])
                for event in item.reference.observable_events
                if event.get("event") == "agent_action"
                and isinstance(event.get("action"), dict)
            )
            failed_actions = tuple(
                dict(event["action"])
                for event in item.failed.observable_events
                if event.get("event") == "agent_action"
                and isinstance(event.get("action"), dict)
            )
            tool_results = tuple(
                dict(event["result"])
                for event in item.reference.observable_events
                if event.get("event") == "tool_result"
                and isinstance(event.get("result"), dict)
            )

            sft_payload = {
                "example_id": item.example_id,
                "task": item.task,
                "reference_actions": reference_actions,
                "reference_tool_results": tool_results,
                "reference_final_output": item.reference.final_output,
            }
            supervised.append(
                SupervisedTrajectoryExample(
                    **sft_payload,
                    record_hash=canonical_sha256(sft_payload),
                )
            )
            preference_payload = {
                "example_id": item.example_id,
                "task": item.task,
                "chosen_actions": reference_actions,
                "rejected_actions": failed_actions,
                "chosen_final_output": item.reference.final_output,
                "rejected_final_output": item.failed.final_output,
            }
            preferences.append(
                PreferenceTrajectoryPair(
                    **preference_payload,
                    record_hash=canonical_sha256(preference_payload),
                )
            )
            seed_payload = {
                "example_id": item.example_id,
                "task": item.task,
                "environment_id": environment_id,
                "seed": replay_seed,
            }
            seeds.append(
                ReplaySeedRecord(
                    **seed_payload,
                    record_hash=canonical_sha256(seed_payload),
                )
            )

        provisional = ModelEvidenceDatasetManifest(
            created_at=created_at,
            base_model_id=next(iter(base_models)),
            problem_cluster=next(iter(clusters)),
            environment_id=environment_id,
            verifier_id=verifier_id,
            evidence_task_ids=task_ids,
            held_out_task_ids=held_out_task_ids,
            examples=examples,
            supervised_examples=tuple(supervised),
            preference_pairs=tuple(preferences),
            replay_seeds=tuple(seeds),
            manifest_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json", exclude={"manifest_hash"})
        _validate_safe(payload)
        manifest = provisional.model_copy(
            update={"manifest_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: ModelEvidenceDatasetManifest) -> bool:
        payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
        _validate_safe(payload)
        if manifest.manifest_hash != canonical_sha256(payload):
            raise ModelEvidenceDatasetError("Model evidence manifest hash mismatch.")
        if not manifest.examples:
            raise ModelEvidenceDatasetError("Model evidence manifest is empty.")
        if not (
            len(manifest.examples)
            == len(manifest.supervised_examples)
            == len(manifest.preference_pairs)
            == len(manifest.replay_seeds)
        ):
            raise ModelEvidenceDatasetError("Model evidence derived record counts differ.")
        task_ids = tuple(item.task.task_id for item in manifest.examples)
        if task_ids != manifest.evidence_task_ids or len(set(task_ids)) != len(task_ids):
            raise ModelEvidenceDatasetError("Model evidence Task manifest is inconsistent.")
        if set(task_ids) & set(manifest.held_out_task_ids):
            raise ModelEvidenceDatasetError("Held-out Tasks leaked into model evidence.")
        if any(item.base_model_id != manifest.base_model_id for item in manifest.examples):
            raise ModelEvidenceDatasetError("Model evidence base model is inconsistent.")
        if any(item.problem_cluster != manifest.problem_cluster for item in manifest.examples):
            raise ModelEvidenceDatasetError("Model evidence problem cluster is inconsistent.")
        expected_ids = [item.example_id for item in manifest.examples]
        for records in (
            manifest.supervised_examples,
            manifest.preference_pairs,
            manifest.replay_seeds,
        ):
            if [item.example_id for item in records] != expected_ids:
                raise ModelEvidenceDatasetError("Derived model evidence ordering is inconsistent.")
        return True

    def signals(self, manifest: ModelEvidenceDatasetManifest) -> DatasetSignals:
        self.verify(manifest)
        return DatasetSignals(
            gold_trajectories=len(manifest.supervised_examples),
            preference_pairs=len(manifest.preference_pairs),
            replayable_environment=True,
            resettable_environment=True,
            machine_verifier=True,
        )

    def export_file(
        self,
        manifest: ModelEvidenceDatasetManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ModelEvidenceDatasetError("Dataset output path must not be a symlink.")
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

    def load_file(self, path: str | Path) -> ModelEvidenceDatasetManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ModelEvidenceDatasetError(
                "Model evidence dataset must be a regular non-symlink file."
            )
        try:
            manifest = ModelEvidenceDatasetManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ModelEvidenceDatasetError("Model evidence dataset is invalid.") from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "ModelEvidenceDatasetError",
    "ModelEvidenceDatasetManager",
    "ModelEvidenceDatasetManifest",
    "ModelEvidenceExample",
    "ObservableTrajectoryRecord",
    "PreferenceTrajectoryPair",
    "ReplaySeedRecord",
    "SupervisedTrajectoryExample",
    "canonical_sha256",
]
