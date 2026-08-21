from __future__ import annotations

from evoagent.campaigns.models import CampaignRisk, CampaignType


class CampaignApprovalPolicy:
    """Return the minimum distinct non-generator approvals for a campaign."""

    def required_approvals(self, campaign_type: CampaignType, risk: CampaignRisk) -> int:
        if campaign_type == CampaignType.MODEL and risk == CampaignRisk.HIGH:
            return 2
        if risk == CampaignRisk.HIGH:
            return 2
        return 1
