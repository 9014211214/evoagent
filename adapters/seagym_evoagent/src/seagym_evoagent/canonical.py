"""Canonical serialization, hashing, and contained atomic persistence."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


MAX_JSON_BYTES = 256 * 1024


def canonical_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path, *, max_bytes: int = 32 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            total += len(block)
            if total > max_bytes:
                raise ValueError("file exceeds the permitted hashing size")
            digest.update(block)
    return digest.hexdigest()


def strict_json_loads(raw: str | bytes, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > max_bytes:
        raise ValueError("JSON document exceeds the permitted size")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except UnicodeDecodeError as exc:
        raise ValueError("JSON document is not valid UTF-8") from exc
    _validate_json_value(value)
    return value


def read_json(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    return strict_json_loads(data, max_bytes=max_bytes)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def contained_path(root: Path, candidate: Path, *, must_exist: bool = False) -> Path:
    if _is_linklike(root):
        raise ValueError("controlled root cannot be a symlink or junction")
    lexical = candidate.absolute()
    for part in (lexical, *lexical.parents):
        if part.exists() and _is_linklike(part):
            raise ValueError("symlinked or junction paths are not accepted")
        if part == root.absolute():
            break
    root_resolved = root.resolve(strict=True)
    resolved = candidate.resolve(strict=must_exist)
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("path escapes the controlled directory")
    return resolved


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _validate_json_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > 500_000 or depth > 24:
        raise ValueError("JSON document exceeds structural limits")
    if value is None or isinstance(value, bool | str | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are forbidden")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")
