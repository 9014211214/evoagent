from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

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
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    return_code INTEGER
                )
                """
            )

    def claim(
        self,
        authorization: ExecutionAuthorization,
        preflight: ExecutionPreflightResult,
        *,
        now: datetime | None = None,
    ) -> ExecutionUseReceipt:
        now = now or datetime.now(timezone.utc)
        if preflight.authorization_hash != authorization.authorization_hash:
            raise ExecutionUseError("Preflight belongs to another authorization.")
        if preflight.request_hash != authorization.request.request_hash:
            raise ExecutionUseError("Preflight belongs to another execution request.")
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
                    "status, started_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        authorization.authorization_hash,
                        authorization.request.request_id,
                        preflight.command_hash,
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
                    raise ExecutionUseError("Execution authorization use is already finalized.")
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
