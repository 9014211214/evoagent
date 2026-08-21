from copy import deepcopy
from evoagent.domain.models import AgentSnapshot, CandidateArtifact, Skill

class SnapshotRegistry:
    def __init__(self):
        self._snapshots = {}

    def add(self, snapshot: AgentSnapshot):
        self._snapshots[snapshot.snapshot_id] = deepcopy(snapshot)

    def get(self, snapshot_id: str) -> AgentSnapshot:
        return deepcopy(self._snapshots[snapshot_id])

    def promote_skill_candidate(self, base: AgentSnapshot, candidate: CandidateArtifact, new_snapshot_id: str) -> AgentSnapshot:
        if candidate.artifact_type != "skill":
            raise ValueError("Expected skill candidate.")
        skill = Skill.model_validate(candidate.payload)
        skill.status = "stable"
        nxt = deepcopy(base)
        nxt.snapshot_id = new_snapshot_id
        nxt.parent_snapshot_id = base.snapshot_id
        nxt.round_index = base.round_index + 1
        nxt.skills[skill.skill_id] = skill
        self.add(nxt)
        return nxt
