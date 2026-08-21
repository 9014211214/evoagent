from __future__ import annotations

from evoagent.domain.models import AgentSnapshot, Skill
from evoagent.skills.models import SkillSpec


def snapshot_from_skill_spec(
    spec: SkillSpec,
    *,
    snapshot_id: str,
    round_index: int,
    model_id: str,
    parent_snapshot_id: str | None = None,
    harness_version: str = "1.2.0",
) -> AgentSnapshot:
    """Bridge the persistent semantic Skill model into the runtime snapshot model."""

    runtime_skill = Skill(
        skill_id=spec.skill_id,
        name=spec.name,
        version=spec.version,
        description=spec.description,
        rules=list(spec.rules),
        provenance=spec.provenance,
        status="stable",
    )
    return AgentSnapshot(
        snapshot_id=snapshot_id,
        round_index=round_index,
        model_id=model_id,
        skills={spec.skill_id: runtime_skill},
        harness_version=harness_version,
        parent_snapshot_id=parent_snapshot_id,
        metadata={
            "active_skill_id": spec.skill_id,
            "skill_content_hash_source": "persistent-skill-spec",
            "skill_generated_by": spec.generated_by,
        },
    )


__all__ = ["snapshot_from_skill_spec"]
