from copy import deepcopy
from packaging.version import Version
from evoagent.domain.models import CandidateArtifact, FailureReport, Skill

def _bump_minor(version: str) -> str:
    v = Version(version)
    return f"{v.major}.{v.minor + 1}.0"

class SkillEvolver:
    def propose(self, skill: Skill, report: FailureReport) -> CandidateArtifact:
        updated = deepcopy(skill)
        feedback = " ".join(report.evidence).lower()
        if "missing_skill_rule: reject_unsafe" in feedback:
            if "reject_unsafe" not in updated.rules:
                updated.rules.append("reject_unsafe")
        else:
            raise ValueError("No supported verified skill patch can be derived from evidence.")
        candidate_version = _bump_minor(skill.version)
        updated.version = candidate_version
        updated.status = "candidate"
        return CandidateArtifact(
            artifact_id=f"{skill.skill_id}:{candidate_version}",
            artifact_type="skill",
            base_version=skill.version,
            candidate_version=candidate_version,
            payload=updated.model_dump(),
            generated_by="native_skill_evolver_v0.1",
        )
