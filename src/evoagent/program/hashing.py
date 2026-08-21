from __future__ import annotations

from typing import Any

from pydantic_core import to_jsonable_python

from evoagent.model_registry.models import canonical_sha256


def normalize_program_payload(value: Any) -> Any:
    """Normalize enums, tuples, datetimes and nested models exactly as Pydantic JSON."""

    return to_jsonable_python(value)


def program_payload_hash(value: Any) -> str:
    """Hash the representation later used by Pydantic model validation and export."""

    return canonical_sha256(normalize_program_payload(value))


__all__ = ["normalize_program_payload", "program_payload_hash"]
