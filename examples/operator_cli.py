from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.cli import main
from evoagent.skills import SQLiteSkillRegistry, SkillSpec

with TemporaryDirectory() as directory:
    root = Path(directory)
    database = root / "skills.db"
    bundle = root / "skills-state.json"
    registry = SQLiteSkillRegistry(database)
    registry.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
            rules=("accept_safe",),
        )
    )

    main(["skill", "list", "--db", str(database)])
    main(
        [
            "skill",
            "show",
            "--db",
            str(database),
            "--skill-id",
            "decision_skill",
        ]
    )
    main(["skill", "export", "--db", str(database), "--out", str(bundle)])
    main(["skill", "audit-verify", "--db", str(database)])
