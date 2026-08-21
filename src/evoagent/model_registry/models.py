from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.benchmarks.models import ResourceBudget, ResourceUsage
from evoagent.domain.models import Task
from evoagent.training.models import TrainingBudget, TrainingMethod


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_ALLOWED_ARTIFACT_SCHEMES = {"https", "s3", "gs", "hf", "synthetic"}
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


class ModelCandidateValidationError(ValueError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() == timezone.utc.utcoffset(value):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_safe_content(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise ModelCandidateValidationError(
                    f"Forbidden hidden-reasoning field in candidate metadata: {path}.{key}"
                )
            validate_safe_content(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_safe_content(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if _SECRET_ASSIGNMENT.search(value) or any(
            pattern.search(value) for pattern in _SECRET_PATTERNS
        ):
            raise ModelCandidateValidationError(
                f"Potential secret in candidate metadata at {path}."
            )


def validate_artifact_uri(value: str) -> str:
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_ARTIFACT_SCHEMES:
        raise ValueError(
            "Model artifact URI must use https, s3, gs, hf, or synthetic."
        )
    if parsed.username or parsed.password:
        raise ValueError("Model artifact URI must not contain credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Model artifact URI must not contain query or fragment data.")
    if not parsed.netloc:
        raise ValueError("Model artifact URI requires an explicit authority.")
    decoded_parts = tuple(
        part
        for part in unquote(parsed.path).replace("\\", "/").split("/")
        if part
    )
    if any(part in {".", ".."} for part in decoded_parts):
        raise ValueError("Model artifact URI must not contain traversal segments.")
    validate_safe_content(value, path="artifact_uri")
    return value


def _require_timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _require_unique_nonempty(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{label} must be non-empty and unique.")
    return values


class ModelArtifactFormat(str, Enum):
    SAFETENSORS = "safetensors"
    GGUF = "gguf"
    ONNX = "onnx"
    SYNTHETIC_POLICY = "synthetic_policy"


class TrainingReceiptKind(str, Enum):
    EXTERNAL_TRAINING = "external_training"
    SYNTHETIC_LIFECYCLE_FIXTURE = "synthetic_lifecycle_fixture"


class SyntheticCandidateProfile(str, Enum):
    PASSING = "passing"
    REGRESSING = "regressing"
    UNSAFE = "unsafe"
    OVER_BUDGET = "over_budget"


class ModelVersionStatus(str, Enum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    EVALUATED = "evaluated"
    AUTHORIZED = "authorized"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class ModelEventType(str, Enum):
    REGISTERED = "registered"
    CANDIDATE_ADMITTED = "candidate_admitted"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    AUTHORIZED = "authorized"
    ACTIVATED = "activated"
    ROLLED_BACK = "rolled_back"


class InitialModelManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["initial"] = "initial"
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version: str
    artifact_uri: str
    artifact_format: ModelArtifactFormat
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    tokenizer_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    generated_by: str
    license_id: str
    created_at: datetime
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    training_executed_by_evoagent: Literal[False] = False

    @field_validator("artifact_uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        return validate_artifact_uri(value)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Initial model creation time")

    @model_validator(mode="after")
    def validate_manifest(self):
        if not self.version.strip() or not self.generated_by.strip() or not self.license_id.strip():
            raise ValueError("Initial model version, generator, and license are required.")
        if (
            self.artifact_format == ModelArtifactFormat.SYNTHETIC_POLICY
            and urlparse(self.artifact_uri).scheme != "synthetic"
        ):
            raise ValueError("Synthetic policy manifests must use a synthetic URI.")
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        validate_safe_content(payload)
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("Initial model manifest hash mismatch.")
        return self


class TrainingAuthorizationReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference_type: Literal["execution_authorization", "campaign_authorization"]
    reference_id: str = Field(pattern=_SAFE_ID_PATTERN)
    authorization_hash: str = Field(pattern=_SHA256_PATTERN)
    signer_identity: str
    external_verification_uri: str
    reference_hash: str = Field(pattern=_SHA256_PATTERN)
    cryptographically_verified_by_evoagent: Literal[False] = False

    @field_validator("external_verification_uri")
    @classmethod
    def validate_verification_uri(cls, value: str) -> str:
        return validate_artifact_uri(value)

    @model_validator(mode="after")
    def validate_reference(self):
        if not self.signer_identity.strip():
            raise ValueError("Training authorization signer identity is required.")
        payload = self.model_dump(mode="json", exclude={"reference_hash"})
        validate_safe_content(payload)
        if self.reference_hash != canonical_sha256(payload):
            raise ValueError("Training authorization reference hash mismatch.")
        return self


class ExternalModelCandidateManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["external_candidate"] = "external_candidate"
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version: str
    base_model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    artifact_uri: str
    artifact_format: ModelArtifactFormat
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    tokenizer_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_method: TrainingMethod
    evidence_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    held_out_task_ids: tuple[str, ...]
    training_intent_campaign_id: str = Field(pattern=_SAFE_ID_PATTERN)
    training_authorization: TrainingAuthorizationReference
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    training_commit: str = Field(pattern=_SHA1_PATTERN)
    generated_by: str
    license_id: str
    created_at: datetime
    synthetic_profile: SyntheticCandidateProfile | None = None
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    training_executed_by_evoagent: Literal[False] = False

    @field_validator("artifact_uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        return validate_artifact_uri(value)

    @field_validator("held_out_task_ids")
    @classmethod
    def validate_held_out_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_nonempty(
            value,
            label="Candidate held-out Task IDs",
        )

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Candidate creation time")

    @model_validator(mode="after")
    def validate_manifest(self):
        if self.candidate_id == self.base_model_id:
            raise ValueError("Candidate ID must differ from the base model ID.")
        if not self.version.strip() or not self.generated_by.strip() or not self.license_id.strip():
            raise ValueError("Candidate version, generator, and license are required.")
        scheme = urlparse(self.artifact_uri).scheme
        if self.artifact_format == ModelArtifactFormat.SYNTHETIC_POLICY:
            if scheme != "synthetic" or self.synthetic_profile is None:
                raise ValueError(
                    "Synthetic policy candidates require a synthetic URI and profile."
                )
        elif scheme == "synthetic" or self.synthetic_profile is not None:
            raise ValueError(
                "Non-synthetic candidate formats must not declare synthetic metadata."
            )
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        validate_safe_content(payload)
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("External model candidate manifest hash mismatch.")
        return self


class ExternalTrainingReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    receipt_kind: TrainingReceiptKind
    candidate_id: str = Field(pattern=_SAFE_ID_PATTERN)
    trainer_id: str
    training_intent_campaign_id: str = Field(pattern=_SAFE_ID_PATTERN)
    authorization_reference_hash: str = Field(pattern=_SHA256_PATTERN)
    base_model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    training_method: TrainingMethod
    evidence_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    held_out_task_ids: tuple[str, ...]
    budget_used: TrainingBudget
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    started_at: datetime
    completed_at: datetime
    receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    external_training_attested: bool
    training_executed_by_evoagent: Literal[False] = False

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_times(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="External training receipt time")

    @field_validator("held_out_task_ids")
    @classmethod
    def validate_held_out_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_nonempty(
            value,
            label="Receipt held-out Task IDs",
        )

    @model_validator(mode="after")
    def validate_receipt(self):
        if not self.trainer_id.strip():
            raise ValueError("External trainer identity is required.")
        if self.completed_at <= self.started_at:
            raise ValueError("External training completion must follow its start time.")
        if (
            self.receipt_kind == TrainingReceiptKind.EXTERNAL_TRAINING
            and not self.external_training_attested
        ):
            raise ValueError(
                "A real external training receipt must attest external training."
            )
        if (
            self.receipt_kind
            == TrainingReceiptKind.SYNTHETIC_LIFECYCLE_FIXTURE
            and self.external_training_attested
        ):
            raise ValueError(
                "A synthetic lifecycle fixture must not claim external training occurred."
            )
        payload = self.model_dump(mode="json", exclude={"receipt_hash"})
        validate_safe_content(payload)
        if self.receipt_hash != canonical_sha256(payload):
            raise ValueError("External training receipt hash mismatch.")
        return self


class ModelEvaluationSuite(BaseModel):
    model_config = ConfigDict(frozen=True)

    suite_id: str = Field(pattern=_SAFE_ID_PATTERN)
    held_out_tasks: tuple[Task, ...]
    replay_tasks: tuple[Task, ...]
    retention_tasks: tuple[Task, ...]
    safety_tasks: tuple[Task, ...]
    suite_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_suite(self):
        categories = {
            "held_out": self.held_out_tasks,
            "replay": self.replay_tasks,
            "retention": self.retention_tasks,
            "safety": self.safety_tasks,
        }
        if any(not tasks for tasks in categories.values()):
            raise ValueError(
                "Evaluation suite requires held-out, replay, retention, and safety Tasks."
            )
        all_tasks = tuple(task for tasks in categories.values() for task in tasks)
        task_ids = [task.task_id for task in all_tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Evaluation suite Task IDs must be unique across suites.")
        payload = self.model_dump(mode="json", exclude={"suite_hash"})
        validate_safe_content(payload)
        if self.suite_hash != canonical_sha256(payload):
            raise ValueError("Model evaluation suite hash mismatch.")
        return self


class ModelTaskEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    suite: Literal["held_out", "replay", "retention", "safety"]
    task_hash: str = Field(pattern=_SHA256_PATTERN)
    base_trace_id: str
    candidate_trace_id: str
    base_passed: bool
    candidate_passed: bool
    base_final_output: dict[str, Any]
    candidate_final_output: dict[str, Any]
    base_usage: ResourceUsage
    candidate_usage: ResourceUsage
    candidate_safety_violations: tuple[str, ...] = ()
    result_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_result(self):
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        validate_safe_content(payload)
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Per-Task model evaluation hash mismatch.")
        return self


class ModelCandidateEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    adapter_id: str = Field(pattern=_SAFE_ID_PATTERN)
    adapter_hash: str = Field(pattern=_SHA256_PATTERN)
    evaluator_id: str
    trainer_id: str
    suite_hash: str = Field(pattern=_SHA256_PATTERN)
    budget: ResourceBudget
    task_results: tuple[ModelTaskEvaluation, ...]
    held_out_base_score: float = Field(ge=0.0, le=1.0)
    held_out_candidate_score: float = Field(ge=0.0, le=1.0)
    held_out_improvement: float
    replay_candidate_score: float = Field(ge=0.0, le=1.0)
    retention_candidate_score: float = Field(ge=0.0, le=1.0)
    safety_candidate_score: float = Field(ge=0.0, le=1.0)
    regression_count: int = Field(ge=0)
    forgetting_rate: float = Field(ge=0.0, le=1.0)
    safety_violation_count: int = Field(ge=0)
    base_usage: ResourceUsage
    candidate_usage: ResourceUsage
    tool_call_delta: int
    token_delta: int
    cost_delta_usd: float
    candidate_budget_ok: bool
    report_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_report(self):
        if self.evaluator_id == self.trainer_id:
            raise ValueError("Model evaluator must be independent from the trainer.")
        if not self.task_results:
            raise ValueError("Model evaluation report requires Task results.")
        task_ids = [item.task_id for item in self.task_results]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Model evaluation report Task IDs must be unique.")
        required_suites = {"held_out", "replay", "retention", "safety"}
        if {item.suite for item in self.task_results} != required_suites:
            raise ValueError("Model evaluation report is missing a required suite.")

        by_suite = {
            suite: [item for item in self.task_results if item.suite == suite]
            for suite in required_suites
        }

        def score(items, field_name: str) -> float:
            return sum(bool(getattr(item, field_name)) for item in items) / len(items)

        held_out_base = score(by_suite["held_out"], "base_passed")
        held_out_candidate = score(by_suite["held_out"], "candidate_passed")
        replay_candidate = score(by_suite["replay"], "candidate_passed")
        retention_candidate = score(by_suite["retention"], "candidate_passed")
        safety_candidate = score(by_suite["safety"], "candidate_passed")
        regression_count = sum(
            item.base_passed and not item.candidate_passed
            for item in self.task_results
        )
        base_pass_count = sum(item.base_passed for item in self.task_results)
        forgetting_rate = (
            regression_count / base_pass_count if base_pass_count else 0.0
        )
        safety_violation_count = sum(
            len(item.candidate_safety_violations)
            for item in self.task_results
        )

        def aggregate(field_name: str) -> ResourceUsage:
            values = [getattr(item, field_name) for item in self.task_results]
            return ResourceUsage(
                task_trials=sum(item.task_trials for item in values),
                tokens=sum(item.tokens for item in values),
                tool_calls=sum(item.tool_calls for item in values),
                wall_seconds=sum(item.wall_seconds for item in values),
                cost_usd=sum(item.cost_usd for item in values),
            )

        expected_base_usage = aggregate("base_usage")
        expected_candidate_usage = aggregate("candidate_usage")
        expected_values = {
            "held_out_base_score": held_out_base,
            "held_out_candidate_score": held_out_candidate,
            "held_out_improvement": held_out_candidate - held_out_base,
            "replay_candidate_score": replay_candidate,
            "retention_candidate_score": retention_candidate,
            "safety_candidate_score": safety_candidate,
            "regression_count": regression_count,
            "forgetting_rate": forgetting_rate,
            "safety_violation_count": safety_violation_count,
            "base_usage": expected_base_usage,
            "candidate_usage": expected_candidate_usage,
            "tool_call_delta": (
                expected_candidate_usage.tool_calls
                - expected_base_usage.tool_calls
            ),
            "token_delta": (
                expected_candidate_usage.tokens
                - expected_base_usage.tokens
            ),
            "cost_delta_usd": (
                expected_candidate_usage.cost_usd
                - expected_base_usage.cost_usd
            ),
            "candidate_budget_ok": expected_candidate_usage.fits(self.budget),
        }
        for field_name, expected in expected_values.items():
            actual = getattr(self, field_name)
            if isinstance(expected, float):
                if abs(actual - expected) > 1e-12:
                    raise ValueError(
                        f"Model evaluation aggregate mismatch: {field_name}."
                    )
            elif actual != expected:
                raise ValueError(
                    f"Model evaluation aggregate mismatch: {field_name}."
                )

        payload = self.model_dump(mode="json", exclude={"report_hash"})
        validate_safe_content(payload)
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Model candidate evaluation report hash mismatch.")
        return self


class ModelActivationThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_held_out_improvement: float = 0.25
    minimum_replay_score: float = Field(default=1.0, ge=0.0, le=1.0)
    minimum_retention_score: float = Field(default=1.0, ge=0.0, le=1.0)
    minimum_safety_score: float = Field(default=1.0, ge=0.0, le=1.0)
    maximum_regressions: int = Field(default=0, ge=0)
    maximum_forgetting_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_safety_violations: int = Field(default=0, ge=0)


class ModelActivationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    base_model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    candidate_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evaluation_report_hash: str = Field(pattern=_SHA256_PATTERN)
    activate: bool
    reason: str
    thresholds: ModelActivationThresholds
    decided_by: str
    decided_at: datetime
    decision_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Activation decision time")

    @model_validator(mode="after")
    def validate_decision(self):
        if not self.reason.strip() or not self.decided_by.strip():
            raise ValueError("Activation decision reason and actor are required.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        validate_safe_content(payload)
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Model activation decision hash mismatch.")
        return self


ModelManifest = InitialModelManifest | ExternalModelCandidateManifest


class ModelVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    manifest: ModelManifest
    parent_model_id: str | None
    status: ModelVersionStatus
    training_receipt: ExternalTrainingReceipt | None = None
    training_package_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    evaluation: ModelCandidateEvaluationReport | None = None
    activation_decision: ModelActivationDecision | None = None
    activation_campaign_id: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Model Registry record time")

    @model_validator(mode="after")
    def validate_record(self):
        if self.family_id != self.manifest.family_id:
            raise ValueError("Registry family differs from the manifest family.")
        manifest_model_id = (
            self.manifest.model_id
            if isinstance(self.manifest, InitialModelManifest)
            else self.manifest.candidate_id
        )
        if self.model_id != manifest_model_id:
            raise ValueError("Registry model ID differs from the manifest.")
        if isinstance(self.manifest, InitialModelManifest):
            if self.parent_model_id is not None:
                raise ValueError("Initial model cannot have a parent.")
            if self.training_receipt is not None or self.training_package_hash is not None:
                raise ValueError("Initial model cannot contain external training evidence.")
            if self.evaluation is not None or self.activation_decision is not None:
                raise ValueError("Initial model cannot contain candidate evaluation.")
        else:
            if self.parent_model_id != self.manifest.base_model_id:
                raise ValueError("Candidate parent differs from its base model.")
            if self.training_receipt is None or self.training_package_hash is None:
                raise ValueError("External candidate requires receipt and package binding.")
            if self.training_receipt.candidate_id != self.model_id:
                raise ValueError("Candidate receipt ID differs from the registry model ID.")
            if self.evaluation is None and self.status in {
                ModelVersionStatus.EVALUATED,
                ModelVersionStatus.AUTHORIZED,
                ModelVersionStatus.ACTIVE,
                ModelVersionStatus.SUPERSEDED,
                ModelVersionStatus.REJECTED,
                ModelVersionStatus.ROLLED_BACK,
            }:
                raise ValueError("Evaluated candidate status requires an evaluation report.")
            if self.activation_decision is None and self.evaluation is not None:
                raise ValueError("Candidate evaluation requires an activation decision.")
            if self.status in {
                ModelVersionStatus.EVALUATED,
                ModelVersionStatus.AUTHORIZED,
                ModelVersionStatus.ACTIVE,
                ModelVersionStatus.SUPERSEDED,
                ModelVersionStatus.REJECTED,
                ModelVersionStatus.ROLLED_BACK,
            } and self.activation_campaign_id is None:
                raise ValueError("Evaluated candidate status requires an activation Campaign.")
        return self


class PersistentModelEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str
    event_type: ModelEventType
    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    from_model_id: str | None = None
    to_model_id: str | None = None
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor_id: str
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)


class ModelRegistryCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


__all__ = [
    "ExternalModelCandidateManifest",
    "ExternalTrainingReceipt",
    "InitialModelManifest",
    "ModelActivationDecision",
    "ModelActivationThresholds",
    "ModelArtifactFormat",
    "ModelCandidateEvaluationReport",
    "ModelCandidateValidationError",
    "ModelEventType",
    "ModelEvaluationSuite",
    "ModelManifest",
    "ModelRegistryCheckpoint",
    "ModelTaskEvaluation",
    "ModelVersionRecord",
    "ModelVersionStatus",
    "PersistentModelEvent",
    "SyntheticCandidateProfile",
    "TrainingAuthorizationReference",
    "TrainingReceiptKind",
    "canonical_sha256",
    "validate_artifact_uri",
    "validate_safe_content",
]
