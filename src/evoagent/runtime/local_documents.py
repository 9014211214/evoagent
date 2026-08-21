from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from evoagent.domain.models import Task
from evoagent.runtime.interfaces import ResettableToolEnvironment
from evoagent.runtime.models import (
    EnvironmentObservation,
    EnvironmentState,
    ToolCall,
    ToolResult,
)


_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_DOCUMENT_BYTES = 1024 * 1024


class LocalDocumentEnvironmentError(RuntimeError):
    pass


class LocalDocumentEnvironment(ResettableToolEnvironment):
    """Filesystem-backed tools whose effects remain inside one resettable episode root."""

    TOOL_NAMES = ("read_document", "write_document", "list_documents")

    def __init__(self, root: str | Path):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise LocalDocumentEnvironmentError("Environment root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._episode_root: Path | None = None
        self._episode_id: str | None = None
        self._protected: set[str] = set()
        self._attempted_writes: list[str] = []
        self._step_index = 0

    @property
    def episode_root(self) -> Path:
        if self._episode_root is None:
            raise LocalDocumentEnvironmentError("Environment has not been reset.")
        return self._episode_root

    def reset(self, task: Task, *, seed: int) -> EnvironmentObservation:
        episode_id = hashlib.sha256(f"{task.task_id}:{seed}".encode("utf-8")).hexdigest()[:24]
        episode_root = self.root / episode_id
        if episode_root.exists():
            if episode_root.is_symlink():
                raise LocalDocumentEnvironmentError("Episode root must not be a symlink.")
            shutil.rmtree(episode_root)
        episode_root.mkdir(parents=True)
        self._episode_root = episode_root
        self._episode_id = episode_id
        self._protected = set()
        self._attempted_writes = []
        self._step_index = 0

        initial = task.input.get("initial_documents", {})
        if not isinstance(initial, dict):
            raise ValueError("initial_documents must be an object.")
        for raw_path, specification in initial.items():
            if isinstance(specification, str):
                content = specification
                protected = False
            elif isinstance(specification, dict):
                content = specification.get("content", "")
                protected = bool(specification.get("protected", False))
            else:
                raise ValueError("Initial document specifications must be strings or objects.")
            if not isinstance(content, str):
                raise ValueError("Initial document content must be text.")
            encoded = content.encode("utf-8")
            if len(encoded) > _MAX_DOCUMENT_BYTES:
                raise ValueError("Initial document exceeds the one MiB limit.")
            normalized, target = self._target(raw_path, create_parents=True)
            self._atomic_write(target, encoded)
            if protected:
                self._protected.add(normalized)

        return self._observation(last_result=None)

    def execute(self, call: ToolCall) -> EnvironmentObservation:
        self.episode_root
        self._step_index += 1
        try:
            if call.tool_name == "read_document":
                result = self._read(call)
            elif call.tool_name == "write_document":
                result = self._write(call)
            elif call.tool_name == "list_documents":
                result = self._list(call)
            else:
                result = self._failure(call, "unknown_tool", "Tool is not available.")
        except (TypeError, ValueError) as exc:
            result = self._failure(call, "invalid_arguments", str(exc))
        except LocalDocumentEnvironmentError as exc:
            result = self._failure(call, "unsafe_path", str(exc))
        return self._observation(last_result=result)

    def inspect_state(self) -> EnvironmentState:
        documents: dict[str, dict[str, Any]] = {}
        for path in sorted(self.episode_root.rglob("*")):
            if path.is_symlink():
                relative = path.relative_to(self.episode_root).as_posix()
                documents[relative] = {"symlink": True, "protected": relative in self._protected}
            elif path.is_file():
                relative = path.relative_to(self.episode_root).as_posix()
                data = path.read_bytes()
                try:
                    content = data.decode("utf-8")
                except UnicodeDecodeError:
                    content = "<non-utf8>"
                documents[relative] = {
                    "content": content,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "protected": relative in self._protected,
                }
        public_state = {
            "episode_id": self._episode_id,
            "documents": documents,
            "attempted_writes": tuple(self._attempted_writes),
        }
        return EnvironmentState(
            state_fingerprint=self._fingerprint(public_state),
            public_state=public_state,
        )

    def _read(self, call: ToolCall) -> ToolResult:
        self._require_keys(call.arguments, required={"path"})
        normalized, target = self._target(call.arguments["path"])
        if target.is_symlink():
            raise LocalDocumentEnvironmentError("Document path resolves to a symlink.")
        if not target.exists():
            return self._success(
                call,
                output={"path": normalized, "exists": False, "protected": False},
            )
        if not target.is_file():
            return self._failure(call, "not_document", "Path is not a regular document.")
        data = target.read_bytes()
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            return self._failure(call, "non_utf8_document", "Document is not UTF-8 text.")
        return self._success(
            call,
            output={
                "path": normalized,
                "exists": True,
                "content": content,
                "protected": normalized in self._protected,
            },
        )

    def _write(self, call: ToolCall) -> ToolResult:
        self._require_keys(call.arguments, required={"path", "content"})
        content = call.arguments["content"]
        if not isinstance(content, str):
            raise TypeError("write_document content must be text.")
        data = content.encode("utf-8")
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise ValueError("Document exceeds the one MiB limit.")
        normalized, target = self._target(call.arguments["path"], create_parents=True)
        self._attempted_writes.append(normalized)
        if normalized in self._protected:
            return self._failure(
                call,
                "protected_document",
                "Protected documents cannot be overwritten.",
                output={"path": normalized},
            )
        if target.is_symlink():
            raise LocalDocumentEnvironmentError("Document path resolves to a symlink.")
        if target.exists() and not target.is_file():
            return self._failure(call, "not_document", "Path is not a regular document.")
        previous = target.read_bytes() if target.exists() else None
        self._atomic_write(target, data)
        return self._success(
            call,
            output={"path": normalized, "written": True, "bytes": len(data)},
            state_changed=previous != data,
        )

    def _list(self, call: ToolCall) -> ToolResult:
        self._require_keys(call.arguments, required=set())
        paths = []
        for path in sorted(self.episode_root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                relative = path.relative_to(self.episode_root).as_posix()
                paths.append(
                    {"path": relative, "protected": relative in self._protected}
                )
        return self._success(call, output={"documents": paths})

    def _target(self, raw_path: Any, *, create_parents: bool = False) -> tuple[str, Path]:
        if not isinstance(raw_path, str) or "\\" in raw_path or "\x00" in raw_path:
            raise LocalDocumentEnvironmentError("Document path must be a safe POSIX relative path.")
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise LocalDocumentEnvironmentError("Absolute paths and traversal are forbidden.")
        if any(not _SAFE_PART.fullmatch(part) for part in pure.parts):
            raise LocalDocumentEnvironmentError("Document path contains an unsafe segment.")
        normalized = pure.as_posix()
        target = self.episode_root.joinpath(*pure.parts)
        root_resolved = self.episode_root.resolve()
        for parent in (self.episode_root, *target.parents):
            if parent == self.episode_root.parent:
                break
            if parent.exists() and parent.is_symlink():
                raise LocalDocumentEnvironmentError("Symlink traversal is forbidden.")
        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.resolve(strict=False).relative_to(root_resolved)
        except ValueError as exc:
            raise LocalDocumentEnvironmentError("Document path escapes the episode root.") from exc
        return normalized, target

    def _observation(self, *, last_result: ToolResult | None) -> EnvironmentObservation:
        state = self.inspect_state()
        return EnvironmentObservation(
            episode_id=self._episode_id or "uninitialized",
            step_index=self._step_index,
            state_fingerprint=state.state_fingerprint,
            available_tools=self.TOOL_NAMES,
            last_tool_result=last_result,
        )

    def _success(
        self,
        call: ToolCall,
        *,
        output: dict[str, Any],
        state_changed: bool = False,
    ) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            ok=True,
            output=output,
            state_changed=state_changed,
            state_fingerprint=self.inspect_state().state_fingerprint,
        )

    def _failure(
        self,
        call: ToolCall,
        error_code: str,
        message: str,
        *,
        output: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            ok=False,
            output=output or {},
            error_code=error_code,
            error_message=message,
            state_changed=False,
            state_fingerprint=self.inspect_state().state_fingerprint,
        )

    @staticmethod
    def _require_keys(arguments: dict[str, Any], *, required: set[str]) -> None:
        if set(arguments) != required:
            raise ValueError(
                f"Expected arguments {sorted(required)}, received {sorted(arguments)}."
            )

    @staticmethod
    def _fingerprint(public_state: dict[str, Any]) -> str:
        canonical = json.dumps(
            public_state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


__all__ = ["LocalDocumentEnvironment", "LocalDocumentEnvironmentError"]
