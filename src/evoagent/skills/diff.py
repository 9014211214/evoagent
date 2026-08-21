from evoagent.skills.models import SkillDiff, SkillSpec


def diff_skills(base: SkillSpec, candidate: SkillSpec) -> SkillDiff:
    if base.skill_id != candidate.skill_id:
        raise ValueError("Cannot diff different skill IDs.")

    base_rules = set(base.rules)
    candidate_rules = set(candidate.rules)
    sections = (
        "preconditions",
        "allowed_tools",
        "procedure",
        "procedure_kinds",
        "success_criteria",
        "failure_handling",
    )
    changed_sections = tuple(
        section for section in sections if getattr(base, section) != getattr(candidate, section)
    )
    return SkillDiff(
        base_version=base.version,
        candidate_version=candidate.version,
        added_rules=tuple(rule for rule in candidate.rules if rule not in base_rules),
        removed_rules=tuple(rule for rule in base.rules if rule not in candidate_rules),
        description_changed=base.description != candidate.description,
        changed_sections=changed_sections,
    )
