from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.benchmarks.full_agent import (
    BenchmarkAgentScope,
    FullAgentBenchmarkAdapter,
    FullAgentBenchmarkBatch,
    FullAgentBenchmarkManifest,
    FullAgentBenchmarkTaskResult,
)
from evoagent.benchmarks.models import ResourceBudget, ResourceUsage
from evoagent.continual.models import UnifiedAgentSnapshot
from evoagent.model_registry.models import canonical_sha256


_HASH = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SECRET_JSON_ASSIGNMENT = re.compile(
    r'(?i)"(?:password|passwd|api[_-]?key|access[_-]?token|auth[_-]?token|secret|private[_-]?key)"\s*:\s*"(?!\*{3}"|\[?redacted\]?"|<redacted>")[^"\r\n]{4,}"'
)


class FullAgentExternalEvidenceError(ValueError):
    pass


class FullAgentExternalRunPlan(BaseModel):
    """Credential-free, non-executing package for an external Full-Agent runner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: Literal["evoagent-full-agent-run-plan-v1"] = (
        "evoagent-full-agent-run-plan-v1"
    )
    plan_id: str = Field(pattern=_SAFE_ID)
    snapshot: UnifiedAgentSnapshot
    manifest: FullAgentBenchmarkManifest
    budget: ResourceBudget
    execution_enabled: Literal[False] = False
    network_access_authorized: Literal[False] = False
    paid_execution_authorized: Literal[False] = False
    upload_authorized: Literal[False] = False
    public_submission_authorized: Literal[False] = False
    plan_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_plan(self):
        if self.snapshot.model_id != self.manifest.model_id:
            raise ValueError("Full-Agent run plan changed the frozen model.")
        for key in ("runtime_hash", "tool_contract_hash", "verifier_hash"):
            if getattr(self.snapshot, key) != getattr(self.manifest, key):
                raise ValueError("Full-Agent run plan changed a frozen runtime contract.")
        expected_trials = len(self.manifest.task_roles) * self.manifest.trials_per_task
        if self.budget.max_task_trials != expected_trials:
            raise ValueError("Full-Agent run plan must bind the exact Task-trial count.")
        payload = self.model_dump(mode="json", exclude={"plan_hash"})
        if self.plan_hash != canonical_sha256(payload):
            raise ValueError("Full-Agent external run-plan hash mismatch.")
        return self


class FullAgentExternalResultFile(BaseModel):
    """Strict safe subset emitted by an independently operated runner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: Literal["evoagent-full-agent-result-v1"] = (
        "evoagent-full-agent-result-v1"
    )
    execution_id: str = Field(pattern=_SAFE_ID)
    adapter_id: str = Field(pattern=_SAFE_ID)
    manifest_hash: str = Field(pattern=_HASH)
    snapshot_hash: str = Field(pattern=_HASH)
    task_results: tuple[FullAgentBenchmarkTaskResult, ...]
    usage: ResourceUsage
    completed: Literal[True] = True
    external_execution_performed: Literal[True] = True
    synthetic_fixture: Literal[False] = False
    official_submission_performed: Literal[False] = False
    official_leaderboard_claimed: Literal[False] = False
    result_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_result(self):
        task_ids = [item.task_id for item in self.task_results]
        if not task_ids or len(task_ids) != len(set(task_ids)):
            raise ValueError("External Full-Agent result Tasks must be non-empty and unique.")
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("External Full-Agent result hash mismatch.")
        return self


def build_full_agent_external_run_plan(**values) -> FullAgentExternalRunPlan:
    payload = dict(values)
    payload.setdefault("format_version", "evoagent-full-agent-run-plan-v1")
    payload.setdefault("execution_enabled", False)
    payload.setdefault("network_access_authorized", False)
    payload.setdefault("paid_execution_authorized", False)
    payload.setdefault("upload_authorized", False)
    payload.setdefault("public_submission_authorized", False)
    return FullAgentExternalRunPlan(**payload, plan_hash=canonical_sha256(payload))


class FullAgentExternalEvidenceAdapter(FullAgentBenchmarkAdapter):
    """Import one caller-hashed result without importing benchmark code.

    The adapter deliberately accepts only a small observable schema. Prompts,
    trajectories, logs, exception messages, Environment payloads and arbitrary
    provider metadata have no field in the contract and are rejected as extras.
    """

    scope = BenchmarkAgentScope.FULL_AGENT

    def __init__(
        self,
        root: str | Path,
        *,
        relative_path: str | Path,
        expected_sha256: str,
        max_bytes: int = 4 * 1024 * 1024,
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise FullAgentExternalEvidenceError("Full-Agent import root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.relative_path = Path(relative_path)
        if re.fullmatch(_HASH, expected_sha256) is None:
            raise ValueError("Expected Full-Agent result SHA-256 is invalid.")
        if max_bytes <= 0:
            raise ValueError("Full-Agent result size limit must be positive.")
        self.expected_sha256 = expected_sha256
        self.max_bytes = max_bytes

    def evaluate(
        self,
        snapshot: UnifiedAgentSnapshot,
        manifest: FullAgentBenchmarkManifest,
        budget: ResourceBudget,
    ) -> FullAgentBenchmarkBatch:
        record, source_sha256 = self._load()
        payload = {
            "adapter_id": record.adapter_id,
            "adapter_scope": BenchmarkAgentScope.FULL_AGENT,
            "manifest_hash": record.manifest_hash,
            "snapshot_hash": record.snapshot_hash,
            "source_result_sha256": source_sha256,
            "task_results": record.task_results,
            "usage": record.usage,
            "external_execution_performed": True,
            "synthetic_fixture": False,
            "official_submission_performed": False,
            "official_leaderboard_claimed": False,
        }
        return FullAgentBenchmarkBatch(**payload, batch_hash=canonical_sha256(payload))

    def _load(self) -> tuple[FullAgentExternalResultFile, str]:
        path = self._resolve_file()
        size = path.stat().st_size
        if size <= 0 or size > self.max_bytes:
            raise FullAgentExternalEvidenceError(
                "Full-Agent result is empty or exceeds the configured size limit."
            )
        raw = path.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != self.expected_sha256:
            raise FullAgentExternalEvidenceError("Full-Agent result SHA-256 mismatch.")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FullAgentExternalEvidenceError(
                "Full-Agent result must be UTF-8 JSON."
            ) from exc
        if _SECRET_JSON_ASSIGNMENT.search(text) or any(
            pattern.search(text) for pattern in _SECRET_PATTERNS
        ):
            raise FullAgentExternalEvidenceError(
                "Full-Agent result contains a potential credential or private key."
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FullAgentExternalEvidenceError(
                "Full-Agent result is not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise FullAgentExternalEvidenceError(
                "Full-Agent result root must be a JSON object."
            )
        try:
            return FullAgentExternalResultFile.model_validate(payload), actual_sha256
        except ValueError as exc:
            raise FullAgentExternalEvidenceError(
                "Full-Agent result does not satisfy the strict observable schema."
            ) from exc

    def _resolve_file(self) -> Path:
        candidate = self.relative_path
        if candidate.is_absolute() or candidate.name != "full-agent-result.json":
            raise FullAgentExternalEvidenceError(
                "Full-Agent adapter accepts only a relative full-agent-result.json path."
            )
        current = self.root
        for part in candidate.parts:
            if part in {"", ".", ".."} or "\x00" in part:
                raise FullAgentExternalEvidenceError(
                    "Full-Agent result path contains an unsafe segment."
                )
            current = current / part
            if current.exists() and current.is_symlink():
                raise FullAgentExternalEvidenceError(
                    "Full-Agent result path must not contain symlinks."
                )
        resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise FullAgentExternalEvidenceError(
                "Full-Agent result path escapes its controlled root."
            ) from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise FullAgentExternalEvidenceError(
                "Full-Agent result must be a regular non-symlink file."
            )
        return resolved


__all__ = [
    "FullAgentExternalEvidenceAdapter",
    "FullAgentExternalEvidenceError",
    "FullAgentExternalResultFile",
    "FullAgentExternalRunPlan",
    "build_full_agent_external_run_plan",
]
