from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.acquisition import InitialSkillAcquisitionGate, SyntheticAcquisitionSandbox
from evoagent.integrations import SkillRecorderAdapter, SkillRecorderImportSpec
from evoagent.skills import SkillRegistry


with TemporaryDirectory() as directory:
    root = Path(directory)
    skill_json = root / "skill.json"
    payload = {
        "version": 1,
        "sessionId": "synthetic-session-001",
        "architecture": "agent-skill",
        "name": "create-note",
        "description": "Create a note from a reviewed semantic demonstration.",
        "allowedTools": ["create_note"],
        "body": "Use {{title}} and verify status created.",
        "values": [{"id": "title", "name": "Title", "value": "Public Note"}],
        "plan": {
            "architecture": "agent-skill",
            "name": "create-note",
            "title": "Create a verified note",
            "description": "Create a note from a reviewed semantic demonstration.",
            "summary": "Normalize the title and create the note.",
            "generalization": "Use stable tools and verify the observable status.",
            "values": [{"id": "title", "name": "Title", "value": "Public Note"}],
            "steps": [
                {
                    "kind": "calculation",
                    "title": "Normalize title",
                    "text": "Normalize {{title}}.",
                    "tool": "",
                },
                {
                    "kind": "action",
                    "title": "Create note",
                    "text": "Call create_note for {{title}} and verify status created.",
                    "tool": "create_note",
                },
            ],
            "allowedTools": ["create_note"],
        },
        "createdAt": 1786320000000,
    }
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    skill_json.write_bytes(data)
    checksum = "sha256:" + hashlib.sha256(data).hexdigest()

    candidate = SkillRecorderAdapter().import_candidate(
        SkillRecorderImportSpec(
            skill_json_path=str(skill_json),
            checksum=checksum,
            consent_to_process=True,
            source_uri="file:///synthetic/skill-recorder/skill.json",
        )
    )
    registry = SkillRegistry()
    result = InitialSkillAcquisitionGate().evaluate_and_register(
        candidate,
        sandbox=SyntheticAcquisitionSandbox(),
        registry=registry,
    )

    print("candidate:", candidate.candidate_id)
    print("procedure kinds:", list(candidate.skill.procedure_kinds))
    print("registered:", result.registered)
    print("active version:", registry.active(candidate.skill.skill_id).spec.version)
    print("real screen recording used:", False)
    print("GitHub Copilot called:", False)
