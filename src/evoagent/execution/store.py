from __future__ import annotations

import sqlite3
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from evoagent.execution.authorization import (
    ExecutionAuthorizationError,
    ExecutionAuthorizationManager,
    command_hash,
    preflight_hash,
)
from evoagent.execution.models import (
    ExecutionAuthorization,
    ExecutionPreflightResult,
    ExecutionUseReceipt,
    ExecutionUseStatus,
)


class ExecutionUseError(PermissionError):
    pass


class SQLiteExecutionUseStore:
    """Single-node one-use ledger for externally authorized commands."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_uses (
                    authorization_hash TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    command_hash TEXT NOT NULL,
                    preflight_hash TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    return_code INTEGER
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(execution_uses)")
            }
            if "preflight_hash" not in columns:
                connection.execute(
                    "ALTER TABLE execution_uses ADD COLUMN preflight_hash TEXT"
                )

    def claim(
        self,
        authorization: ExecutionAuthorization,
        preflight: ExecutionPreflightResult,
        *,
        now: datetime | None = None,
    ) -> ExecutionUseReceipt:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ExecutionUseError("Execution claim time must include a timezone.")
        try:
            ExecutionAuthorizationManager().verify_authorization(authorization)
        except ExecutionAuthorizationError as exc:
            raise ExecutionUseError(
                "Execution authorization is invalid at claim time."
            ) from exc
        request = authorization.request
        if now < request.issued_at:
            raise ExecutionUseError("Execution authorization is not active yet.")
        if now >= request.expires_at:
            raise ExecutionUseError("Execution authorization has expired.")
        if preflight.authorization_hash != authorization.authorization_hash:
            raise ExecutionUseError("Preflight belongs to another authorization.")
        if preflight.request_hash != request.request_hash:
            raise ExecutionUseError("Preflight belongs to another execution request.")
        if preflight.preflight_hash != preflight_hash(preflight):
            raise ExecutionUseError("Execution preflight hash mismatch.")
        if not (
            request.issued_at
            <= preflight.checked_at
            < preflight.fresh_until
            <= request.expires_at
        ):
            raise ExecutionUseError(
                "Execution preflight time chain is outside the authorization window."
            )
        if now < preflight.checked_at:
            raise ExecutionUseError("Execution preflight is not active yet.")
        if now >= preflight.fresh_until:
            raise ExecutionUseError("Execution preflight is stale.")
        invocation = request.invocation
        if preflight.command_hash != command_hash(invocation.command):
            raise ExecutionUseError("Execution preflight command hash mismatch.")
        executable_path = Path(preflight.executable_path)
        resolved_executable = shutil.which(invocation.command[0])
        if (
            not executable_path.is_absolute()
            or executable_path.is_symlink()
            or not executable_path.is_file()
            or resolved_executable is None
            or executable_path.resolve() != Path(resolved_executable).resolve()
        ):
            raise ExecutionUseError(
                "Execution preflight executable no longer matches the approved command."
            )
        if (
            preflight.adapter != invocation.adapter
            or preflight.workspace != str(Path(invocation.workspace).resolve())
            or preflight.required_approvals
            != ExecutionAuthorizationManager.required_approvals(invocation)
            or preflight.approver_ids
            != tuple(item.approver_id for item in authorization.approvals)
            or set(preflight.environment_presence)
            != set(invocation.required_environment_variables)
            or not all(preflight.environment_presence.values())
            or preflight.network_access != invocation.network_access
            or preflight.upload != invocation.upload
            or preflight.public != invocation.public
            or preflight.training != invocation.training
            or preflight.budget != invocation.budget
        ):
            raise ExecutionUseError(
                "Execution preflight differs from the approved invocation."
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT status FROM execution_uses WHERE authorization_hash = ?",
                    (authorization.authorization_hash,),
                ).fetchone()
                if existing is not None:
                    raise ExecutionUseError(
                        f"Execution authorization was already used with status {existing['status']}."
                    )
                connection.execute(
                    "INSERT INTO execution_uses (authorization_hash, request_id, command_hash, "
                    "preflight_hash, status, started_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        authorization.authorization_hash,
                        authorization.request.request_id,
                        preflight.command_hash,
                        preflight.preflight_hash,
                        ExecutionUseStatus.CLAIMED.value,
                        now.isoformat(),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(authorization.authorization_hash)

    def complete(
        self,
        authorization_hash: str,
        *,
        return_code: int,
        now: datetime | None = None,
    ) -> ExecutionUseReceipt:
        now = now or datetime.now(timezone.utc)
        status = (
            ExecutionUseStatus.COMPLETED
            if return_code == 0
            else ExecutionUseStatus.FAILED
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM execution_uses WHERE authorization_hash = ?",
                    (authorization_hash,),
                ).fetchone()
                if row is None:
                    raise KeyError("Unknown execution authorization use.")
                if row["status"] != ExecutionUseStatus.CLAIMED.value:
                    raise ExecutionUseError(
                        "Execution authorization use is already finalized."
                    )
                connection.execute(
                    "UPDATE execution_uses SET status = ?, completed_at = ?, return_code = ? "
                    "WHERE authorization_hash = ?",
                    (status.value, now.isoformat(), return_code, authorization_hash),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(authorization_hash)

    def get(self, authorization_hash: str) -> ExecutionUseReceipt:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM execution_uses WHERE authorization_hash = ?",
                (authorization_hash,),
            ).fetchone()
        if row is None:
            raise KeyError("Unknown execution authorization use.")
        return ExecutionUseReceipt(
            authorization_hash=row["authorization_hash"],
            request_id=row["request_id"],
            command_hash=row["command_hash"],
            preflight_hash=row["preflight_hash"],
            status=ExecutionUseStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            return_code=row["return_code"],
        )


__all__ = ["ExecutionUseError", "SQLiteExecutionUseStore"]
