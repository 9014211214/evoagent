from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.skills import (
    SQLiteSkillRegistry,
    SkillEvaluationDecision,
    SkillSpec,
    SkillStateBundleManager,
)

with TemporaryDirectory() as directory:
    root = Path(directory)
    registry = SQLiteSkillRegistry(root / "skills.db")
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
            reason="held-out evaluation passed",
        ),
        expected_active_revision=0,
    )

    restarted = SQLiteSkillRegistry(root / "skills.db")
    bundle_path = root / "skills-state.json"
    bundle = SkillStateBundleManager().export_file(restarted, bundle_path)
    restored = SQLiteSkillRegistry(root / "restored.db")
    SkillStateBundleManager().import_into(restored, bundle)

    print("active after restart:", restarted.active(base.skill_id).spec.version)
    print("active after restore:", restored.active(base.skill_id).spec.version)
    print("versions:", len(restored.list_versions(base.skill_id)))
    print("audit verified:", restored.verify_audit())
