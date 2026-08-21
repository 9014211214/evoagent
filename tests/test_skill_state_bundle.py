import json

import pytest

from evoagent.skills import (
    SQLiteSkillRegistry,
    SkillEvaluationDecision,
    SkillSpec,
    SkillStateBundleError,
    SkillStateBundleManager,
)


def populated_registry(path):
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
    registry.add_candidate(candidate, parent_version="1.0.0", reason="verified failure")
    registry.promote(
        base.skill_id,
        candidate.version,
        SkillEvaluationDecision(
            skill_id=base.skill_id,
            base_version=base.version,
            candidate_version=candidate.version,
            promote=True,
            base_score=0.5,
            candidate_score=1.0,
            regression_count=0,
            reason="evaluation passed",
        ),
    )
    return registry


def keyed_records(registry):
    return {
        (record.spec.skill_id, record.spec.version): record
        for skill_id in registry.list_skill_ids()
        for record in registry.list_versions(skill_id)
    }


def test_bundle_round_trip_into_empty_registry(tmp_path):
    source = populated_registry(tmp_path / "source.db")
    manager = SkillStateBundleManager()
    path = tmp_path / "state.json"
    bundle = manager.export_file(source, path)

    target = SQLiteSkillRegistry(tmp_path / "target.db")
    manager.import_into(target, manager.load_file(path))

    assert keyed_records(target) == keyed_records(source)
    assert target.active_versions() == source.active_versions()
    assert target.active_revisions() == source.active_revisions()
    assert target.events() == source.events()
    assert target.verify_audit() is True
    assert bundle.manifest_hash


def test_modified_bundle_and_nonempty_import_are_rejected(tmp_path):
    source = populated_registry(tmp_path / "source.db")
    manager = SkillStateBundleManager()
    path = tmp_path / "state.json"
    manager.export_file(source, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["spec"]["description"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SkillStateBundleError):
        manager.load_file(path)

    clean = manager.build(source)
    with pytest.raises(SkillStateBundleError):
        manager.import_into(source, clean)


def test_secret_bearing_skill_state_cannot_be_exported(tmp_path):
    registry = SQLiteSkillRegistry(tmp_path / "skills.db")
    registry.register_initial(
        SkillSpec(
            skill_id="unsafe_skill",
            name="Unsafe Skill",
            version="1.0.0",
            description="uses sk-abcdefghijklmnop secret",
        )
    )
    with pytest.raises(SkillStateBundleError):
        SkillStateBundleManager().build(registry)
