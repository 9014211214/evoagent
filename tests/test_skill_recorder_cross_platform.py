from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evoagent.integrations import (
    SkillRecorderAdapter,
    SkillRecorderImportError,
    SkillRecorderImportSpec,
)


def payload():
    return {
        "version": 1,
        "sessionId": "session-cross-platform",
        "architecture": "agent-skill",
        "name": "create-note",
        "description": "Create a note from an approved plan.",
        "allowedTools": ["create_note"],
        "body": "Use {{title}} and verify status created.",
        "values": [{"id": "title", "name": "Title", "value": "Public Note"}],
        "plan": {
            "architecture": "agent-skill",
            "name": "create-note",
            "title": "Create a verified note",
            "description": "Create a note from an approved plan.",
            "summary": "Create and verify.",
            "generalization": "Use a stable tool.",
            "values": [{"id": "title", "name": "Title", "value": "Public Note"}],
            "steps": [
                {
                    "kind": "action",
                    "title": "Create note",
                    "text": "Call create_note for {{title}}.",
                    "tool": "create_note",
                }
            ],
            "allowedTools": ["create_note"],
        },
        "createdAt": 1786320000000,
        "exportedPath": "C:\\Users\\example\\Documents\\create-note",
        "exportedAt": 1786320001000,
    }


def write(tmp_path: Path, value) -> tuple[Path, str]:
    path = tmp_path / "skill.json"
    data = json.dumps(value, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    return path, "sha256:" + hashlib.sha256(data).hexdigest()


def spec(path: Path, checksum: str):
    return SkillRecorderImportSpec(
        skill_json_path=str(path),
        checksum=checksum,
        consent_to_process=True,
    )


def test_windows_exported_path_is_valid_when_imported_on_any_platform(tmp_path):
    path, checksum = write(tmp_path, payload())
    candidate = SkillRecorderAdapter().import_candidate(spec(path, checksum))
    assert candidate.skill.procedure_kinds == ("action",)


def test_conflicting_top_level_and_plan_values_are_rejected(tmp_path):
    value = payload()
    value["values"][0]["value"] = "Top Level"
    value["plan"]["values"][0]["value"] = "Plan Level"
    path, checksum = write(tmp_path, value)
    with pytest.raises(SkillRecorderImportError, match="invalid"):
        SkillRecorderAdapter().import_candidate(spec(path, checksum))
