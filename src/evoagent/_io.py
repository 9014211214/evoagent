from __future__ import annotations

import hashlib
import uuid
from pathlib import Path


def atomic_temporary_path(destination: Path) -> Path:
    """Return a short same-directory path suitable for atomic replacement."""

    name_hash = hashlib.sha256(destination.name.encode("utf-8")).hexdigest()[:8]
    nonce = uuid.uuid4().hex[:8]
    return destination.with_name(f".evo-{name_hash}-{nonce}.tmp")


__all__ = ["atomic_temporary_path"]
