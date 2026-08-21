from __future__ import annotations

import pytest

from evoagent.campaigns import CampaignGovernanceService
from evoagent.lab import ReferenceEvolutionLab, ReferenceLabError
from evoagent.skills import SQLiteSkillRegistry, SkillVersionStatus


def test_reference_lab_cannot_promote_before_campaign_authorization(tmp_path, monkeypatch):
    original = CampaignGovernanceService.approve

    def approval_that_does_not_authorize(self, campaign_id, **kwargs):
        return self.repository.get(campaign_id)

    monkeypatch.setattr(CampaignGovernanceService, "approve", approval_that_does_not_authorize)
    lab = ReferenceEvolutionLab(tmp_path, source_commit="2" * 40)

    with pytest.raises(ReferenceLabError, match="not authorized"):
        lab.run()

    registry = SQLiteSkillRegistry(lab.skill_database)
    assert registry.active(lab.SKILL_ID).spec.version == lab.BASE_VERSION
    records = registry.list_versions(lab.SKILL_ID)
    assert len(records) == 2
    assert any(item.status == SkillVersionStatus.CANDIDATE for item in records)

    monkeypatch.setattr(CampaignGovernanceService, "approve", original)
