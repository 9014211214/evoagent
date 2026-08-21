import hashlib
import json
import sqlite3

import pytest

from evoagent.cli import main
from evoagent.skills import (
    SQLiteSkillRegistry,
    SkillRegistryBundle,
    SkillSpec,
    SkillStateBundleError,
    SkillStateBundleManager,
)


def _registry_with_candidate(path):
    registry = SQLiteSkillRegistry(path)
    base = SkillSpec(
        skill_id="decision_skill",
        name="Decision Skill",
        version="1.0.0",
        description="Handle safe cases.",
        rules=("accept_safe",),
    )
    candidate = base.model_copy(
        update={
            "version": "1.1.0",
            "description": "Handle safe and unsafe cases.",
            "rules": ("accept_safe", "reject_unsafe"),
        }
    )
    registry.register_initial(base)
    registry.add_candidate(candidate, parent_version=base.version, reason="verified failure")
    return registry


def _rehashed_bundle(payload):
    material = dict(payload)
    material.pop("manifest_hash", None)
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["manifest_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return SkillRegistryBundle.model_validate(payload)


def test_semantic_revision_tamper_is_rejected_even_after_manifest_rehash(tmp_path):
    registry = SQLiteSkillRegistry(tmp_path / "skills.db")
    registry.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
        )
    )
    manager = SkillStateBundleManager()
    payload = manager.build(registry).model_dump(mode="json")
    payload["active_revisions"]["decision_skill"] = 1

    with pytest.raises(SkillStateBundleError, match="revisions"):
        manager.verify(_rehashed_bundle(payload))


def test_parent_graph_tamper_is_rejected_even_after_manifest_rehash(tmp_path):
    manager = SkillStateBundleManager()
    payload = manager.build(_registry_with_candidate(tmp_path / "skills.db")).model_dump(
        mode="json"
    )
    candidate = next(
        record for record in payload["records"] if record["spec"]["version"] == "1.1.0"
    )
    candidate["parent_version"] = "9.9.9"

    with pytest.raises(SkillStateBundleError, match="Missing parent"):
        manager.verify(_rehashed_bundle(payload))


def test_database_content_tamper_blocks_bundle_export(tmp_path):
    path = tmp_path / "skills.db"
    registry = SQLiteSkillRegistry(path)
    registry.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
        )
    )
    with sqlite3.connect(path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT spec_json FROM skill_versions WHERE skill_id = ? AND version = ?",
                ("decision_skill", "1.0.0"),
            ).fetchone()[0]
        )
        payload["description"] = "tampered capability"
        connection.execute(
            "UPDATE skill_versions SET spec_json = ? WHERE skill_id = ? AND version = ?",
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "decision_skill",
                "1.0.0",
            ),
        )
        connection.commit()

    with pytest.raises(SkillStateBundleError, match="content hash"):
        SkillStateBundleManager().build(registry)


def test_read_only_cli_does_not_create_a_missing_database(tmp_path, capsys):
    missing = tmp_path / "missing.db"
    assert main(["skill", "list", "--db", str(missing)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "FileNotFoundError"
    assert not missing.exists()
