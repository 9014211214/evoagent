from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evoagent.skills import SkillSpec
from evoagent.skills.sqlite_registry import skill_content_hash


ROOT = Path(__file__).resolve().parents[1]


def test_empty_procedure_kinds_preserve_pre_rc2_skill_hash_format():
    legacy_payload = {
        "skill_id": "legacy_skill",
        "name": "Legacy Skill",
        "version": "1.0.0",
        "description": "Created before typed procedure semantics.",
        "rules": [],
        "preconditions": [],
        "allowed_tools": [],
        "procedure": [],
        "success_criteria": [],
        "failure_handling": [],
        "provenance": "independent",
        "source_refs": [],
        "generated_by": "human",
    }
    canonical = json.dumps(
        legacy_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    spec = SkillSpec.model_validate(legacy_payload)
    assert spec.procedure_kinds == ()
    assert skill_content_hash(spec) == expected


def test_no_write_enabled_one_time_workflow_is_mergeable():
    workflow_directory = ROOT / ".github" / "workflows"
    offenders = []
    for path in workflow_directory.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "contents: write" in text or "hardening-sync" in path.name or "rc2-sync" in path.name:
            offenders.append(path.name)
    assert offenders == []
