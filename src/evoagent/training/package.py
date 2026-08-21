from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.campaigns.models import (
    CampaignCheckpoint,
    CampaignRecord,
    CampaignState,
    CampaignType,
)
from evoagent.domain.models import Task
from evoagent.traces.models import TraceCheckpoint
from evoagent.training.evidence import (
    ModelEvidenceDatasetManifest,
    ModelEvidenceDatasetManager,
    canonical_sha256,
)
from evoagent.training.models import (
    AgenticRLTaskSpec,
    ModelCandidate,
    ModelImprovementTicket,
    TrainingMethod,
)


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
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


class ModelEvolutionPackageError(ValueError):
    pass


def _validate_safe(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise ModelEvolutionPackageError(
                    f"Forbidden hidden-reasoning field in model package: {path}.{key}"
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
            raise ModelEvolutionPackageError(
                f"Potential secret in model package at {path}."
            )


class ModelEvolutionPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-model-evolution-package-v1"] = (
        "evoagent-model-evolution-package-v1"
    )
    run_id: str
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    campaign: CampaignRecord
    dataset: ModelEvidenceDatasetManifest
    held_out_tasks: tuple[Task, ...]
    ticket: ModelImprovementTicket
    candidate: ModelCandidate
    campaign_checkpoint: CampaignCheckpoint
    trace_checkpoint: TraceCheckpoint
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    training_executed: Literal[False] = False
    external_execution_performed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Model package creation time must include a timezone.")
        return value


class ModelEvolutionPackageManager:
    def build(
        self,
        *,
        run_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        campaign: CampaignRecord,
        dataset: ModelEvidenceDatasetManifest,
        held_out_tasks: tuple[Task, ...],
        ticket: ModelImprovementTicket,
        candidate: ModelCandidate,
        campaign_checkpoint: CampaignCheckpoint,
        trace_checkpoint: TraceCheckpoint,
    ) -> ModelEvolutionPackageManifest:
        provisional = ModelEvolutionPackageManifest(
            run_id=run_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            campaign=campaign,
            dataset=dataset,
            held_out_tasks=held_out_tasks,
            ticket=ticket,
            candidate=candidate,
            campaign_checkpoint=campaign_checkpoint,
            trace_checkpoint=trace_checkpoint,
            package_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        _validate_safe(payload)
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: ModelEvolutionPackageManifest) -> bool:
        payload = manifest.model_dump(mode="json", exclude={"package_hash"})
        _validate_safe(payload)
        if manifest.package_hash != canonical_sha256(payload):
            raise ModelEvolutionPackageError("Model evolution package hash mismatch.")
        ModelEvidenceDatasetManager().verify(manifest.dataset)

        campaign = manifest.campaign
        ticket = manifest.ticket
        candidate = manifest.candidate
        dataset = manifest.dataset
        held_out_ids = tuple(task.task_id for task in manifest.held_out_tasks)

        if not manifest.held_out_tasks or len(set(held_out_ids)) != len(held_out_ids):
            raise ModelEvolutionPackageError(
                "Model package requires unique frozen held-out Tasks."
            )
        if held_out_ids != dataset.held_out_task_ids:
            raise ModelEvolutionPackageError(
                "Packaged held-out Tasks do not match the dataset manifest."
            )
        if set(held_out_ids) & set(dataset.evidence_task_ids):
            raise ModelEvolutionPackageError(
                "Held-out Tasks overlap model-evolution evidence."
            )
        if campaign.campaign_type != CampaignType.MODEL:
            raise ModelEvolutionPackageError("Model package Campaign has the wrong type.")
        if campaign.state != CampaignState.CANDIDATE_READY:
            raise ModelEvolutionPackageError(
                "Model package requires an unevaluated CANDIDATE_READY Campaign."
            )
        if campaign.artifact_payload is None or campaign.artifact_payload.get("kind") != (
            "model_candidate"
        ):
            raise ModelEvolutionPackageError(
                "Model Campaign does not contain a dry-run model candidate."
            )
        if ticket.base_model_id != dataset.base_model_id:
            raise ModelEvolutionPackageError("Ticket base model does not match the dataset.")
        if ticket.problem_cluster != dataset.problem_cluster:
            raise ModelEvolutionPackageError(
                "Ticket problem cluster does not match the dataset."
            )
        evidence_trace_ids = tuple(item.failed.trace_id for item in dataset.examples)
        if ticket.evidence_trace_ids != evidence_trace_ids:
            raise ModelEvolutionPackageError(
                "Ticket evidence Traces do not match the verified dataset."
            )
        if ticket.evidence_manifest_hash != dataset.manifest_hash:
            raise ModelEvolutionPackageError(
                "Ticket evidence manifest does not match the dataset."
            )
        if ticket.held_out_task_ids != held_out_ids:
            raise ModelEvolutionPackageError(
                "Ticket held-out manifest does not match the packaged Tasks."
            )
        if candidate.base_model_id != ticket.base_model_id:
            raise ModelEvolutionPackageError("Candidate base model does not match its Ticket.")
        if candidate.method != TrainingMethod.AGENTIC_RL:
            raise ModelEvolutionPackageError(
                "The governed v1 package requires an Agentic RL dry-run candidate."
            )
        if candidate.evidence_manifest_hash != dataset.manifest_hash:
            raise ModelEvolutionPackageError(
                "Candidate evidence manifest does not match the dataset."
            )
        if candidate.held_out_task_ids != held_out_ids:
            raise ModelEvolutionPackageError(
                "Candidate held-out manifest does not match the packaged Tasks."
            )
        if candidate.training_executed:
            raise ModelEvolutionPackageError("Dry-run Candidate cannot claim training execution.")
        if not isinstance(candidate.task_spec, AgenticRLTaskSpec):
            raise ModelEvolutionPackageError(
                "Agentic RL Candidate requires an AgenticRLTaskSpec."
            )
        if candidate.task_spec.execution_enabled:
            raise ModelEvolutionPackageError(
                "Governed model package must keep Agentic RL execution disabled."
            )
        if candidate.task_spec.evidence_manifest_hash != dataset.manifest_hash:
            raise ModelEvolutionPackageError(
                "Agentic RL task evidence does not match the dataset."
            )
        if candidate.task_spec.held_out_task_ids != held_out_ids:
            raise ModelEvolutionPackageError(
                "Agentic RL task held-out manifest does not match the packaged Tasks."
            )

        stored_ticket = ModelImprovementTicket.model_validate(
            campaign.artifact_payload.get("ticket")
        )
        stored_candidate = ModelCandidate.model_validate(
            campaign.artifact_payload.get("candidate")
        )
        if stored_ticket != ticket or stored_candidate != candidate:
            raise ModelEvolutionPackageError(
                "Campaign artifact does not match the packaged Ticket and Candidate."
            )
        return True

    def export_file(
        self,
        manifest: ModelEvolutionPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ModelEvolutionPackageError("Model package output must not be a symlink.")
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

    def load_file(self, path: str | Path) -> ModelEvolutionPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ModelEvolutionPackageError(
                "Model evolution package must be a regular non-symlink file."
            )
        try:
            manifest = ModelEvolutionPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ModelEvolutionPackageError("Model evolution package is invalid.") from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "ModelEvolutionPackageError",
    "ModelEvolutionPackageManager",
    "ModelEvolutionPackageManifest",
]
