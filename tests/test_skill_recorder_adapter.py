from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from evoagent.acquisition import InitialSkillAcquisitionGate, SyntheticAcquisitionSandbox
from evoagent.integrations import (
    SKILL_RECORDER_COMMIT,
    SKILL_RECORDER_RELEASE,
    SkillRecorderAdapter,
    SkillRecorderImportError,
    SkillRecorderImportSpec,
)
from evoagent.skills import SkillRegistry


def built_skill(**overrides):
    plan = {
        "architecture": "agent-skill",
        "name": "create-note",
        "title": "Create a verified note",
        "description": "Create a note using an approved semantic plan.",
        "summary": "Normalize the title and create the note.",
        "generalization": "Use stable tools and verify the created status.",
        "values": [
            {"id": "title", "name": "Title", "value": "Public Note"}
        ],
        "steps": [
            {
                "kind": "calculation",
                "title": "Normalize title",
                "text": "Normalize {{title}} before the tool call.",
                "tool": "",
            },
            {
                "kind": "action",
                "title": "Create note",
                "text": "Call create_note for {{title}} and observe status created.",
                "tool": "create_note",
            },
        ],
        "allowedTools": ["create_note"],
    }
    payload = {
        "version": 1,
        "sessionId": "session-001",
        "architecture": "agent-skill",
        "name": "create-note",
        "description": "Create a note using an approved semantic plan.",
        "allowedTools": ["create_note"],
        "body": "# Create note\nUse {{title}} and verify the created status.",
        "values": [
            {"id": "title", "name": "Title", "value": "Public Note"}
        ],
        "plan": plan,
        "createdAt": 1786320000000,
    }
    payload.update(overrides)
    return payload


def write_skill(tmp_path: Path, payload=None) -> tuple[Path, str]:
    path = tmp_path / "skill.json"
    data = json.dumps(payload or built_skill(), sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    return path, "sha256:" + hashlib.sha256(data).hexdigest()


def import_spec(path: Path, checksum: str) -> SkillRecorderImportSpec:
    return SkillRecorderImportSpec(
        skill_json_path=str(path),
        checksum=checksum,
        consent_to_process=True,
        source_uri="file:///synthetic/skill-recorder/session-001/skill.json",
    )


def test_valid_skill_recorder_output_becomes_candidate_then_uses_existing_gate(tmp_path):
    path, checksum = write_skill(tmp_path)
    candidate = SkillRecorderAdapter().import_candidate(import_spec(path, checksum))

    assert candidate.status == "candidate"
    assert candidate.skill.provenance == "microsoft-skill-recorder"
    assert candidate.skill.allowed_tools == ("create_note",)
    assert candidate.skill.procedure[0].startswith("1. [calculation]")
    assert candidate.skill.procedure[1].startswith("2. [action]")
    assert "Public Note" in candidate.skill.procedure[0]
    assert "{{title}}" not in " ".join(candidate.skill.procedure)
    assert SKILL_RECORDER_RELEASE in candidate.skill.generated_by
    assert SKILL_RECORDER_COMMIT in candidate.skill.source_refs[0]

    registry = SkillRegistry()
    assert registry.list_versions(candidate.skill.skill_id) == []
    result = InitialSkillAcquisitionGate().evaluate_and_register(
        candidate,
        sandbox=SyntheticAcquisitionSandbox(),
        registry=registry,
    )
    assert result.registered is True
    assert registry.active(candidate.skill.skill_id).spec == candidate.skill


def test_checksum_consent_and_modified_bytes_are_rejected(tmp_path):
    path, checksum = write_skill(tmp_path)
    with pytest.raises(SkillRecorderImportError, match="Consent"):
        SkillRecorderAdapter().import_candidate(
            import_spec(path, checksum).model_copy(update={"consent_to_process": False})
        )

    with pytest.raises(SkillRecorderImportError, match="checksum"):
        SkillRecorderAdapter().import_candidate(
            import_spec(path, "sha256:" + "0" * 64)
        )

    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SkillRecorderImportError, match="checksum"):
        SkillRecorderAdapter().import_candidate(import_spec(path, checksum))


def test_unresolved_tokens_secrets_and_unsupported_architecture_are_rejected(tmp_path):
    unresolved = built_skill(body="Use {{missing_value}} and verify the result.")
    path, checksum = write_skill(tmp_path, unresolved)
    with pytest.raises(SkillRecorderImportError, match="Unresolved"):
        SkillRecorderAdapter().import_candidate(import_spec(path, checksum))

    secret = built_skill()
    secret["values"][0]["value"] = "sk-abcdefghijklmnop"
    secret["plan"]["values"][0]["value"] = "sk-abcdefghijklmnop"
    path, checksum = write_skill(tmp_path, secret)
    with pytest.raises(SkillRecorderImportError, match="secret"):
        SkillRecorderAdapter().import_candidate(import_spec(path, checksum))

    unsupported = built_skill(architecture="copilot-studio")
    unsupported["plan"]["architecture"] = "copilot-studio"
    path, checksum = write_skill(tmp_path, unsupported)
    with pytest.raises(SkillRecorderImportError, match="invalid"):
        SkillRecorderAdapter().import_candidate(import_spec(path, checksum))


def test_symlink_wrong_filename_and_inconsistent_plan_are_rejected(tmp_path):
    real, checksum = write_skill(tmp_path)
    link = tmp_path / "linked-skill.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("Symlinks are unavailable in this environment.")
    with pytest.raises(SkillRecorderImportError, match="non-symlink"):
        SkillRecorderAdapter().import_candidate(import_spec(link, checksum))

    renamed = tmp_path / "not-skill.json"
    renamed.write_bytes(real.read_bytes())
    with pytest.raises(SkillRecorderImportError, match="named skill.json"):
        SkillRecorderAdapter().import_candidate(import_spec(renamed, checksum))

    inconsistent = built_skill()
    inconsistent["plan"]["description"] = "Different description"
    path, checksum = write_skill(tmp_path, inconsistent)
    with pytest.raises(SkillRecorderImportError, match="invalid"):
        SkillRecorderAdapter().import_candidate(import_spec(path, checksum))
