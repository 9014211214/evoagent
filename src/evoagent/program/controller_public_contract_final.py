from __future__ import annotations

from evoagent.campaigns import ApprovalDecision, CampaignState
from evoagent.program.controller_public_contract import (
    RetryHardenedEvolutionProgramController as _PublicContractController,
)


class RetryHardenedEvolutionProgramController(_PublicContractController):
    """Final public Controller, including partial and terminal approval retries."""

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
        if not existing:
            return super().approve_generation(
                campaign_id,
                actor_id=actor_id,
                reason=reason,
                expected_revision=expected_revision,
            )
        if (
            len(existing) != 1
            or existing[0].decision != ApprovalDecision.APPROVE
            or existing[0].reason != reason
        ):
            raise ValueError(
                "Campaign approval retry conflicts with the immutable decision."
            )
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        decision = self._continue_decision(plan)
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
            *self._generation_evaluation_actors(campaign_id),
        }
        if actor_id in forbidden:
            raise ValueError(
                "Campaign approval retry violates independent role separation."
            )
        if campaign.state == CampaignState.APPROVAL_PENDING:
            self._validate_campaign(
                campaign,
                policy=policy,
                signal=signal,
                attribution=attribution,
                plan=plan,
                require_authorized=False,
            )
            approvals = tuple(
                self.campaign_governance.repository.approvals(campaign_id)
            )
            if len(approvals) != 1 or approvals[0] != existing[0]:
                raise ValueError(
                    "Partially approved Campaign has inconsistent approval cardinality."
                )
        elif campaign.state in {
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            self._validate_existing_campaign(campaign)
        else:
            raise ValueError(
                "Exact approval retry is invalid for the current Campaign state."
            )
        return campaign


__all__ = ["RetryHardenedEvolutionProgramController"]
