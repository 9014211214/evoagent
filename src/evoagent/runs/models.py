from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.benchmarks.models import BenchmarkManifest, ResourceBudget


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GIT_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_SAFE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class RunArtifactKind(str, Enum):
    CONFIG = "config"
    SNAPSHOT = "snapshot"
    RESULTS = "results"
    METRICS = "metrics"
    LOG = "log"
    AUDIT_CHECKPOINT = "audit_checkpoint"
    THIRD_PARTY_LOCK = "third_party_lock"
    OTHER = "other"


class RunStatus(str, Enum):
    PLANNED = "planned"
    DRY_RUN = "dry_run"
    EXECUTED_UNVERIFIED = "executed_unverified"
    EXTERNALLY_VALIDATED = "externally_validated"


class RunArtifactSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    logical_name: str = Field(pattern=_SAFE_NAME_PATTERN)
    kind: RunArtifactKind
    source_path: str
    media_type: str = "application/octet-stream"
    required: bool = True


class RunArtifactRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    logical_name: str = Field(pattern=_SAFE_NAME_PATTERN)
    kind: RunArtifactKind
    relative_path: str
    media_type: str
    required: bool = True
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if (
            normalized.startswith("/")
            or not normalized.startswith("artifacts/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("Run artifact path must be a safe path below artifacts/.")
        return normalized


class RunEnvironmentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    python_version: str
    platform: str
    implementation: str = "CPython"
    packages: dict[str, str] = Field(default_factory=dict)
    tools: dict[str, str] = Field(default_factory=dict)
    container_images: dict[str, str] = Field(default_factory=dict)
    network_access: bool = False


class ReproducibleRunSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_NAME_PATTERN)
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_GIT_COMMIT_PATTERN)
    dirty_worktree: bool = False
    system_name: str
    initial_model_id: str
    snapshot_ids: tuple[str, ...]
    benchmark: BenchmarkManifest
    evolution_budget: ResourceBudget
    evaluation_budget: ResourceBudget
    command: tuple[str, ...]
    environment: RunEnvironmentSpec
    random_seeds: dict[str, int] = Field(default_factory=dict)
    provenance: tuple[str, ...] = ()
    status: RunStatus = RunStatus.PLANNED
    external_validation_reference: str | None = None
    external_signature_required: bool = False

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Run creation time must include a timezone.")
        return value

    @field_validator("snapshot_ids")
    @classmethod
    def validate_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("Run snapshot IDs must be non-empty.")
        if len(value) != len(set(value)):
            raise ValueError("Run snapshot IDs must be unique.")
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("Run command arguments must be non-empty.")
        return value

    @field_validator("random_seeds")
    @classmethod
    def validate_seeds(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() or seed < 0 for key, seed in value.items()):
            raise ValueError("Random seed names must be non-empty and values non-negative.")
        return value

    @field_validator("source_repository")
    @classmethod
    def validate_source_repository(cls, value: str) -> str:
        if not value.startswith("https://github.com/"):
            raise ValueError("Source repository must be an HTTPS GitHub URL.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == RunStatus.EXTERNALLY_VALIDATED:
            if not self.external_validation_reference:
                raise ValueError("Externally validated runs require a validation reference.")
            if not self.external_signature_required:
                raise ValueError("Externally validated runs must require an external signature.")
            if self.dirty_worktree:
                raise ValueError("Externally validated runs cannot declare a dirty worktree.")
        return self


class ReproducibleRunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-run-bundle-v1"] = "evoagent-run-bundle-v1"
    spec: ReproducibleRunSpec
    artifacts: tuple[RunArtifactRecord, ...]
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("artifacts")
    @classmethod
    def validate_artifacts(
        cls, value: tuple[RunArtifactRecord, ...]
    ) -> tuple[RunArtifactRecord, ...]:
        if not value:
            raise ValueError("A reproducible run bundle requires at least one artifact.")
        names = [item.logical_name for item in value]
        paths = [item.relative_path for item in value]
        if len(set(names)) != len(names) or len(set(paths)) != len(paths):
            raise ValueError("Run artifact names and paths must be unique.")
        return value

    @model_validator(mode="after")
    def validate_claim_evidence(self):
        if self.spec.status == RunStatus.EXTERNALLY_VALIDATED:
            kinds = {item.kind for item in self.artifacts}
            required = {RunArtifactKind.RESULTS, RunArtifactKind.THIRD_PARTY_LOCK}
            if not required.issubset(kinds):
                raise ValueError(
                    "Externally validated runs require result and third-party-lock artifacts."
                )
        return self


class RunManifestCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-run-checkpoint-v1"] = "evoagent-run-checkpoint-v1"
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)


class ExternalSignatureReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-external-signature-ref-v1"] = (
        "evoagent-external-signature-ref-v1"
    )
    signed_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    algorithm: str
    signer_identity: str
    signature_uri: str
    verification_instructions: str | None = None

    @field_validator("algorithm", "signer_identity", "signature_uri")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("External signature metadata must not be empty.")
        return normalized


class RunBundleVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    verified: Literal[True] = True
    bundle_path: str
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    external_checkpoint_matched: bool
    artifacts_verified: int = Field(ge=0)
    external_signature_reference_present: bool
    external_signature_cryptographically_verified: bool = False
