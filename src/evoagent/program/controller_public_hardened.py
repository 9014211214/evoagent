from __future__ import annotations

from evoagent.campaigns import ApprovalDecision
from evoagent.program.controller_evidence_hardened_final import (
    RetryHardenedEvolutionProgramController as _UniqueEvidenceController,
)


class RetryHardenedEvolutionProgramController(_UniqueEvidenceController):
    """Final public Controller with complete read-only retry revalidation."""

    def approve_generation(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ):
        campaign = self.campaign_governance.repository.get(campaign_id)
        existing = tuple(
            item
            for item in self.campaign_governance.repository.approvals(campaign_id)
            if item.actor_id == actor_id
        )
        if existing:
            if (
                len(existing) != 1
                or existing[0].decision != ApprovalDecision.APPROVE
                or existing[0].reason != reason
            ):
                raise ValueError(
                    "Campaign approval retry conflicts with the immutable decision."
                )
            self._validate_existing_campaign(campaign)
            return campaign
        return super().approve_generation(
            campaign_id,
            actor_id=actor_id,
            reason=reason,
            expected_revision=expected_revision,
        )


__all__ = ["RetryHardenedEvolutionProgramController"]
