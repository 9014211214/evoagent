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


def test_value_substitution_cannot_assemble_a_secret(tmp_path: Path):
    payload = {
        "version": 1,
        "sessionId": "session-rendered-secret",
        "architecture": "agent-skill",
        "name": "unsafe-note",
        "description": "Synthetic secret-concatenation test.",
        "allowedTools": ["create_note"],
        "body": "Use sk-{{suffix}} only for this rejected test.",
        "values": [
            {"id": "suffix", "name": "Suffix", "value": "abcdefghijklmnop"}
        ],
        "plan": {
            "architecture": "agent-skill",
            "name": "unsafe-note",
            "title": "Unsafe note",
            "description": "Synthetic secret-concatenation test.",
            "summary": "Must be rejected.",
            "generalization": "Must be rejected.",
            "values": [
                {"id": "suffix", "name": "Suffix", "value": "abcdefghijklmnop"}
            ],
            "steps": [
                {
                    "kind": "action",
                    "title": "Unsafe action",
                    "text": "Call create_note with sk-{{suffix}}.",
                    "tool": "create_note",
                }
            ],
            "allowedTools": ["create_note"],
        },
        "createdAt": 1786320000000,
    }
    path = tmp_path / "skill.json"
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(data)

    with pytest.raises(SkillRecorderImportError, match="after rendering"):
        SkillRecorderAdapter().import_candidate(
            SkillRecorderImportSpec(
                skill_json_path=str(path),
                checksum="sha256:" + hashlib.sha256(data).hexdigest(),
                consent_to_process=True,
            )
        )
