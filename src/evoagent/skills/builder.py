from __future__ import annotations

from packaging.version import Version

from evoagent.skills.models import SkillPatch, SkillSpec


class SkillCandidateBuilder:
    """Build a new immutable SkillSpec without mutating the base version."""

    def propose(self, base: SkillSpec, patch: SkillPatch, *, new_version: str) -> SkillSpec:
        if Version(new_version) <= Version(base.version):
            raise ValueError("Candidate version must be newer than the base version.")

        current_rules = list(base.rules)
        for rule in patch.remove_rules:
            if rule not in current_rules:
                raise ValueError(f"Cannot remove missing rule: {rule}")
            current_rules.remove(rule)
        for rule in patch.add_rules:
            if rule not in current_rules:
                current_rules.append(rule)

        procedure = patch.procedure if patch.procedure is not None else base.procedure
        if patch.procedure_kinds is not None:
            procedure_kinds = patch.procedure_kinds
        elif patch.procedure is None:
            procedure_kinds = base.procedure_kinds
        else:
            # A replacement authored by an older evolver has no reliable typed metadata.
            procedure_kinds = ()

        return SkillSpec(
            skill_id=base.skill_id,
            name=base.name,
            version=new_version,
            description=patch.description if patch.description is not None else base.description,
            rules=tuple(current_rules),
            preconditions=(
                patch.preconditions if patch.preconditions is not None else base.preconditions
            ),
            allowed_tools=(
                patch.allowed_tools if patch.allowed_tools is not None else base.allowed_tools
            ),
            procedure=procedure,
            procedure_kinds=procedure_kinds,
            success_criteria=(
                patch.success_criteria
                if patch.success_criteria is not None
                else base.success_criteria
            ),
            failure_handling=(
                patch.failure_handling
                if patch.failure_handling is not None
                else base.failure_handling
            ),
            provenance=base.provenance,
            source_refs=tuple(dict.fromkeys((*base.source_refs, *patch.evidence_trace_ids))),
            generated_by=patch.generated_by,
        )
