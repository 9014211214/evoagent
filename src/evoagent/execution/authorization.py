from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from evoagent._io import atomic_temporary_path
from evoagent.execution.environment import build_authorized_environment
from evoagent.execution.models import (
    ExecutionApproval,
    ExecutionAuthorization,
    ExecutionInvocation,
    ExecutionPreflightResult,
    ExecutionRequest,
)
from evoagent.execution.process import platform_executable_argv


_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|api[_-]?key|access[_-]?token|auth[_-]?token|secret|private[_-]?key)="
)


class ExecutionAuthorizationError(PermissionError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
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


def command_hash(command: tuple[str, ...]) -> str:
    return hashlib.sha256(_canonical_json(list(command))).hexdigest()


def request_hash(request: ExecutionRequest | dict[str, Any]) -> str:
    payload = (
        request.model_dump(mode="json")
        if isinstance(request, ExecutionRequest)
        else _jsonable(request)
    )
    payload.pop("request_hash", None)
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def authorization_hash(authorization: ExecutionAuthorization | dict[str, Any]) -> str:
    payload = (
        authorization.model_dump(mode="json")
        if isinstance(authorization, ExecutionAuthorization)
        else _jsonable(authorization)
    )
    payload.pop("authorization_hash", None)
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def preflight_hash(preflight: ExecutionPreflightResult | dict[str, Any]) -> str:
    payload = (
        preflight.model_dump(mode="json")
        if isinstance(preflight, ExecutionPreflightResult)
        else _jsonable(preflight)
    )
    payload.pop("preflight_hash", None)
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


class ExecutionAuthorizationManager:
    def prepare_request(
        self,
        *,
        request_id: str,
        requester_id: str,
        purpose: str,
        issued_at: datetime,
        expires_at: datetime,
        invocation: ExecutionInvocation,
    ) -> ExecutionRequest:
        self._reject_secrets(invocation.command)
        self._reject_secret_text(purpose, label="Execution request purpose")
        provisional = ExecutionRequest(
            request_id=request_id,
            requester_id=requester_id,
            purpose=purpose,
            issued_at=issued_at,
            expires_at=expires_at,
            invocation=invocation,
            request_hash="0" * 64,
        )
        return provisional.model_copy(
            update={"request_hash": request_hash(provisional)}
        )

    @staticmethod
    def approve(
        request: ExecutionRequest,
        *,
        approver_id: str,
        approved_at: datetime,
        reason: str,
    ) -> ExecutionApproval:
        return ExecutionApproval(
            approver_id=approver_id,
            approved_at=approved_at,
            approved_request_hash=request.request_hash,
            reason=reason,
        )

    def authorize(
        self,
        request: ExecutionRequest,
        *,
        approvals: tuple[ExecutionApproval, ...],
    ) -> ExecutionAuthorization:
        self.verify_request(request)
        self._validate_approvals(request, approvals)
        provisional = ExecutionAuthorization(
            request=request,
            approvals=approvals,
            max_uses=1,
            authorization_hash="0" * 64,
        )
        return provisional.model_copy(
            update={"authorization_hash": authorization_hash(provisional)}
        )

    def verify_request(self, request: ExecutionRequest) -> bool:
        self._reject_secrets(request.invocation.command)
        self._reject_secret_text(request.purpose, label="Execution request purpose")
        if request.request_hash != request_hash(request):
            raise ExecutionAuthorizationError("Execution request hash mismatch.")
        return True

    def verify_authorization(self, authorization: ExecutionAuthorization) -> bool:
        self.verify_request(authorization.request)
        if authorization.authorization_hash != authorization_hash(authorization):
            raise ExecutionAuthorizationError("Execution authorization hash mismatch.")
        self._validate_approvals(authorization.request, authorization.approvals)
        return True

    def write_request(self, request: ExecutionRequest, path: str | Path) -> Path:
        self.verify_request(request)
        return self._atomic_write(path, request.model_dump_json(indent=2) + "\n")

    def write_authorization(
        self, authorization: ExecutionAuthorization, path: str | Path
    ) -> Path:
        self.verify_authorization(authorization)
        return self._atomic_write(path, authorization.model_dump_json(indent=2) + "\n")

    def load_request(self, path: str | Path) -> ExecutionRequest:
        target = self._regular_file(path, label="Execution request")
        try:
            request = ExecutionRequest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise ExecutionAuthorizationError("Execution request is invalid.") from exc
        self.verify_request(request)
        return request

    def load_authorization(self, path: str | Path) -> ExecutionAuthorization:
        target = self._regular_file(path, label="Execution authorization")
        try:
            authorization = ExecutionAuthorization.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise ExecutionAuthorizationError(
                "Execution authorization is invalid."
            ) from exc
        self.verify_authorization(authorization)
        return authorization

    def preflight(
        self,
        authorization: ExecutionAuthorization,
        invocation: ExecutionInvocation,
        *,
        environment: dict[str, str] | None = None,
        now: datetime | None = None,
        version_timeout_seconds: int = 10,
        freshness_seconds: int = 300,
    ) -> ExecutionPreflightResult:
        self.verify_authorization(authorization)
        request = authorization.request
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ExecutionAuthorizationError("Preflight time must include a timezone.")
        if not 1 <= freshness_seconds <= 900:
            raise ExecutionAuthorizationError(
                "Preflight freshness must be between 1 and 900 seconds."
            )
        if now < request.issued_at:
            raise ExecutionAuthorizationError(
                "Execution authorization is not active yet."
            )
        if now >= request.expires_at:
            raise ExecutionAuthorizationError("Execution authorization has expired.")
        if invocation != request.invocation:
            raise ExecutionAuthorizationError(
                "Execution invocation differs from the approved request."
            )
        self._reject_secrets(invocation.command)

        workspace = Path(invocation.workspace)
        if workspace.is_symlink() or not workspace.is_dir():
            raise ExecutionAuthorizationError(
                "Authorized execution workspace must already exist as a non-symlink directory."
            )
        if invocation.workspace_must_be_empty and any(workspace.iterdir()):
            raise ExecutionAuthorizationError(
                "Authorized execution workspace must be empty."
            )

        executable = shutil.which(invocation.command[0])
        if not executable:
            raise ExecutionAuthorizationError(
                f"Authorized executable is not available: {invocation.command[0]}"
            )
        version_environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "HOME"}
        }
        try:
            completed = subprocess.run(
                platform_executable_argv(executable, invocation.version_arguments),
                cwd=workspace,
                env=version_environment,
                text=True,
                capture_output=True,
                timeout=version_timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExecutionAuthorizationError(
                "Unable to inspect the authorized executable version."
            ) from exc
        output = (completed.stdout + "\n" + completed.stderr).strip()[:500]
        if completed.returncode != 0:
            raise ExecutionAuthorizationError(
                f"Executable version check failed with exit code {completed.returncode}."
            )
        if not re.search(invocation.expected_version_pattern, output):
            raise ExecutionAuthorizationError(
                "Executable version output does not match the approved pattern."
            )

        combined_environment = build_authorized_environment(invocation, environment)
        presence = {
            name: bool(combined_environment.get(name))
            for name in invocation.required_environment_variables
        }
        missing = [name for name, present in presence.items() if not present]
        if missing:
            raise ExecutionAuthorizationError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        provisional = ExecutionPreflightResult(
            authorization_hash=authorization.authorization_hash,
            request_hash=request.request_hash,
            command_hash=command_hash(invocation.command),
            checked_at=now,
            fresh_until=min(
                now + timedelta(seconds=freshness_seconds),
                request.expires_at,
            ),
            adapter=invocation.adapter,
            executable_path=str(Path(executable).resolve()),
            executable_version_output=output,
            workspace=str(workspace.resolve()),
            environment_presence=presence,
            required_approvals=self.required_approvals(invocation),
            approver_ids=tuple(item.approver_id for item in authorization.approvals),
            network_access=invocation.network_access,
            upload=invocation.upload,
            public=invocation.public,
            training=invocation.training,
            budget=invocation.budget,
            preflight_hash="0" * 64,
        )
        return provisional.model_copy(
            update={"preflight_hash": preflight_hash(provisional)}
        )

    @staticmethod
    def required_approvals(invocation: ExecutionInvocation) -> int:
        high_risk = (
            invocation.network_access
            or invocation.upload
            or invocation.public
            or invocation.training
            or invocation.budget.max_cost_usd > 0
            or invocation.budget.max_gpu_hours > 0
        )
        return 2 if high_risk else 1

    def _validate_approvals(
        self,
        request: ExecutionRequest,
        approvals: tuple[ExecutionApproval, ...],
    ) -> None:
        required = self.required_approvals(request.invocation)
        if len(approvals) < required:
            raise ExecutionAuthorizationError(
                f"Execution requires at least {required} distinct approvals."
            )
        approver_ids = [item.approver_id for item in approvals]
        if len(set(approver_ids)) != len(approver_ids):
            raise ExecutionAuthorizationError("Execution approvers must be distinct.")
        if request.requester_id in approver_ids:
            raise ExecutionAuthorizationError(
                "Execution requester cannot self-approve."
            )
        for approval in approvals:
            self._reject_secret_text(
                approval.reason, label=f"Approval reason for {approval.approver_id}"
            )
            if approval.approved_request_hash != request.request_hash:
                raise ExecutionAuthorizationError(
                    "Execution approval is bound to another request hash."
                )
            if not (request.issued_at <= approval.approved_at < request.expires_at):
                raise ExecutionAuthorizationError(
                    "Execution approval time is outside the request validity window."
                )

    @classmethod
    def _reject_secret_text(cls, value: str, *, label: str) -> None:
        if _SECRET_ASSIGNMENT.search(value) or any(
            pattern.search(value) for pattern in _SECRET_PATTERNS
        ):
            raise ExecutionAuthorizationError(f"{label} contains a potential secret.")

    @staticmethod
    def _reject_secrets(command: tuple[str, ...]) -> None:
        for argument in command:
            if _SECRET_ASSIGNMENT.search(argument) or any(
                pattern.search(argument) for pattern in _SECRET_PATTERNS
            ):
                raise ExecutionAuthorizationError(
                    "Execution command contains a potential secret; use environment variables."
                )

    @staticmethod
    def _regular_file(path: str | Path, *, label: str) -> Path:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ExecutionAuthorizationError(
                f"{label} must be an existing regular non-symlink file."
            )
        return target

    @staticmethod
    def _atomic_write(path: str | Path, content: str) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = atomic_temporary_path(destination)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination


__all__ = [
    "ExecutionAuthorizationError",
    "ExecutionAuthorizationManager",
    "authorization_hash",
    "command_hash",
    "preflight_hash",
    "request_hash",
]
