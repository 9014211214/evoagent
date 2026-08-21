from __future__ import annotations

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignRecord,
    CampaignState,
)
from evoagent.program.controller_public_hardened import (
    RetryHardenedEvolutionProgramController as _RetryRevalidatingController,
)
from evoagent.program.models import GenerationStatus, ProgramEventType


_EVALUATION_RESULT_REASONS = {
    "GenerationPlan matches verified feedback, attribution and Program budget.",
    "Recovered exact GenerationPlan evaluation after partial submission.",
}
_NORMAL_COMPLETION_REASON = (
    "Exact authorized generation completed with verified child evidence."
)
_RECOVERY_COMPLETION_REASON = (
    "Recovered exact completed generation after partial cross-registry commit."
)
_PROGRAM_AUTHORIZATION_REASON = (
    "Exact Generation Campaign authorization synchronized locally."
)
_PROGRAM_START_REASON = (
    "Explicit local start of the exact authorized generation."
)
_PROGRAM_COMPLETION_REASON = (
    "Authorized child release evidence completed the Program generation."
)
_TERMINAL_GENERATION_STATUSES = {
    GenerationStatus.COMPLETED,
    GenerationStatus.ROLLED_BACK,
    GenerationStatus.HELD,
}


class RetryHardenedEvolutionProgramController(_RetryRevalidatingController):
    """Final public Controller with phase-correct, audit-bound retries."""

    def approve_generation(
        self,
        campaign_id: str,
        *,
        actor_id: str,
        reason: str,
        expected_revision: int,
    ):
        campaign = self.campaign_governance.repository.get(campaign_id)
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        decision = self._continue_decision(plan)
        forbidden = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
        }
        forbidden.update(self._generation_evaluation_actors(campaign_id))
        if actor_id in forbidden:
            raise ValueError(
                "Evidence producer, attributor, evaluator or decision/planning "
                "actor cannot approve."
            )
        approvals = tuple(
            self.campaign_governance.repository.approvals(campaign_id)
        )
        existing = tuple(
            item for item in approvals if item.actor_id == actor_id
        )
        if not existing:
            if (
                campaign.state != CampaignState.APPROVAL_PENDING
                or len(approvals) not in {0, 1}
            ):
                raise ValueError(
                    "New Generation approval is invalid in its persisted lifecycle phase."
                )
            self._validate_campaign_lifecycle_audit(campaign, approvals)
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
        self._validate_campaign(
            campaign,
            policy=policy,
            signal=signal,
            attribution=attribution,
            plan=plan,
            require_authorized=False,
        )
        approval_actors = tuple(item.actor_id for item in approvals)
        if (
            campaign.required_approvals != 2
            or len(approvals) not in {1, 2}
            or len(set(approval_actors)) != len(approval_actors)
            or any(item.decision != ApprovalDecision.APPROVE for item in approvals)
            or set(approval_actors) & forbidden
        ):
            raise ValueError(
                "Generation approval retry differs from governed approval evidence."
            )
        if campaign.state == CampaignState.APPROVAL_PENDING:
            if len(approvals) != 1:
                raise ValueError(
                    "Approval-pending Campaign must contain exactly one approval."
                )
            self._validate_campaign_lifecycle_audit(campaign, approvals)
        elif campaign.state in {
            CampaignState.AUTHORIZED,
            CampaignState.COMPLETED,
        }:
            if len(approvals) != 2:
                raise ValueError(
                    "Authorized or completed Campaign requires two approvals."
                )
            self._validate_existing_campaign(campaign)
        else:
            raise ValueError(
                "Campaign approval retry is invalid in its lifecycle state."
            )
        return campaign

    def _validate_existing_campaign(self, campaign: CampaignRecord) -> None:
        super()._validate_existing_campaign(campaign)
        approvals = tuple(
            self.campaign_governance.repository.approvals(
                campaign.campaign_id
            )
        )
        self._validate_campaign_lifecycle_audit(campaign, approvals)

    def _validate_campaign_lifecycle_audit(self, campaign, approvals) -> None:
        self.campaign_governance.repository.verify_audit()
        self.repository.verify_audit()
        policy, signal, attribution, plan = self._campaign_evidence(campaign)
        self.repository.verify_state(plan.program_id)

        events = tuple(
            event
            for event in self.campaign_governance.repository.audit_events()
            if event.campaign_id == campaign.campaign_id
        )
        if campaign.state == CampaignState.APPROVAL_PENDING:
            if len(approvals) not in {0, 1}:
                raise ValueError(
                    "Approval-pending Campaign has an invalid approval count."
                )
            expected_types = (
                "campaign_created",
                "candidate_attached",
                "campaign_transitioned",
                "campaign_transitioned",
                *("approval_recorded",) * len(approvals),
            )
        else:
            expected_types = {
                CampaignState.AUTHORIZED: (
                    "campaign_created",
                    "candidate_attached",
                    "campaign_transitioned",
                    "campaign_transitioned",
                    "approval_recorded",
                    "approval_recorded",
                ),
                CampaignState.COMPLETED: (
                    "campaign_created",
                    "candidate_attached",
                    "campaign_transitioned",
                    "campaign_transitioned",
                    "approval_recorded",
                    "approval_recorded",
                    "campaign_transitioned",
                ),
            }.get(campaign.state)
        if expected_types is None or tuple(
            item.event_type for item in events
        ) != expected_types:
            raise ValueError(
                "Generation Campaign audit lifecycle is missing, duplicated or reordered."
            )
        if campaign.revision != len(events) - 1:
            raise ValueError(
                "Generation Campaign revision differs from its audit lifecycle."
            )
        if tuple(item.created_at for item in events) != tuple(
            sorted(item.created_at for item in events)
        ):
            raise ValueError(
                "Generation Campaign audit timestamps are not monotonic."
            )
        if campaign.created_at != events[0].created_at:
            raise ValueError(
                "Generation Campaign creation time differs from its audit event."
            )
        if campaign.updated_at != events[-1].created_at:
            raise ValueError(
                "Generation Campaign update time differs from its audit tail."
            )

        created, candidate, evaluation_started, evaluation_completed = events[:4]
        if (
            created.actor_id != "evoagent-system"
            or created.payload
            != {
                "campaign_type": campaign.campaign_type.value,
                "target_key": campaign.target_key,
                "fingerprint": campaign.fingerprint,
                "state": CampaignState.OPEN.value,
            }
        ):
            raise ValueError(
                "Generation Campaign creation audit differs from its record."
            )
        if candidate.payload != {"candidate_ref": campaign.candidate_ref}:
            raise ValueError(
                "Generation Campaign candidate audit differs from its artifact."
            )
        if evaluation_started.payload != {
            "from_state": CampaignState.CANDIDATE_READY.value,
            "to_state": CampaignState.EVALUATION_PENDING.value,
            "reason": "Independent evaluation started.",
            "cooldown_until": None,
        }:
            raise ValueError(
                "Generation Campaign evaluation-start audit was substituted."
            )
        if (
            evaluation_completed.payload.get("from_state")
            != CampaignState.EVALUATION_PENDING.value
            or evaluation_completed.payload.get("to_state")
            != CampaignState.APPROVAL_PENDING.value
            or evaluation_completed.payload.get("reason")
            not in _EVALUATION_RESULT_REASONS
            or evaluation_completed.payload.get("cooldown_until") is not None
            or set(evaluation_completed.payload)
            != {"from_state", "to_state", "reason", "cooldown_until"}
        ):
            raise ValueError(
                "Generation Campaign evaluation-result audit was substituted."
            )
        evaluator_actors = {
            candidate.actor_id,
            evaluation_started.actor_id,
            evaluation_completed.actor_id,
        }
        if len(evaluator_actors) != 1:
            raise ValueError(
                "Generation Campaign audit does not identify one exact evaluator."
            )
        evaluator = next(iter(evaluator_actors))
        decision = self._continue_decision(plan)
        privileged = {
            signal.evidence_producer_id,
            attribution.attributor_id,
            plan.created_by,
            decision.decided_by,
        }
        if evaluator in privileged:
            raise ValueError(
                "Generation Campaign evaluator overlaps privileged Program authority."
            )

        program_events = tuple(self.repository.events())
        bound_events = tuple(
            event
            for event in program_events
            if event.event_type
            == ProgramEventType.GENERATION_CAMPAIGN_BOUND
            and event.generation_id == plan.generation_id
            and event.payload.get("campaign_id") == campaign.campaign_id
        )
        if len(bound_events) != 1 or bound_events[0].actor_id != evaluator:
            raise ValueError(
                "Program Campaign-binding audit differs from its evaluator."
            )

        approval_events = tuple(
            event for event in events if event.event_type == "approval_recorded"
        )
        if len(approval_events) != len(approvals):
            raise ValueError(
                "Generation approval rows differ from Campaign audit evidence."
            )
        expected_states = (
            CampaignState.APPROVAL_PENDING.value,
            CampaignState.AUTHORIZED.value,
        )[: len(approvals)]
        for event, approval, resulting_state in zip(
            approval_events,
            approvals,
            expected_states,
            strict=True,
        ):
            if (
                event.actor_id != approval.actor_id
                or event.created_at != approval.created_at
                or event.payload
                != {
                    "decision": approval.decision.value,
                    "reason": approval.reason,
                    "resulting_state": resulting_state,
                }
            ):
                raise ValueError(
                    "Generation approval audit identity, reason, time or state differs."
                )
        approval_actors = {item.actor_id for item in approvals}
        if (
            len(approval_actors) != len(approvals)
            or approval_actors & privileged
            or evaluator in approval_actors
        ):
            raise ValueError(
                "Generation approval audit violates role separation."
            )

        generation = self.repository.get_generation(
            plan.program_id,
            plan.generation_id,
        )
        authorization_events = tuple(
            event
            for event in program_events
            if event.event_type == ProgramEventType.GENERATION_AUTHORIZED
            and event.generation_id == plan.generation_id
        )
        start_events = tuple(
            event
            for event in program_events
            if event.event_type == ProgramEventType.GENERATION_STARTED
            and event.generation_id == plan.generation_id
        )
        completion_events = tuple(
            event
            for event in program_events
            if event.event_type == ProgramEventType.GENERATION_COMPLETED
            and event.generation_id == plan.generation_id
        )
        expected_program_counts = {
            GenerationStatus.PLANNED: (0, 0, 0),
            GenerationStatus.AUTHORIZED: (1, 0, 0),
            GenerationStatus.RUNNING: (1, 1, 0),
            GenerationStatus.COMPLETED: (1, 1, 1),
            GenerationStatus.ROLLED_BACK: (1, 1, 1),
            GenerationStatus.HELD: (1, 1, 1),
        }.get(generation.status)
        actual_program_counts = (
            len(authorization_events),
            len(start_events),
            len(completion_events),
        )
        if expected_program_counts is None or actual_program_counts != expected_program_counts:
            raise ValueError(
                "Program generation status differs from authorization, start or completion audit."
            )
        if campaign.state == CampaignState.APPROVAL_PENDING:
            if (
                generation.status != GenerationStatus.PLANNED
                or len(approvals) not in {0, 1}
            ):
                raise ValueError(
                    "Approval-pending Campaign differs from its Program lifecycle."
                )
        elif campaign.state == CampaignState.COMPLETED:
            if generation.status not in _TERMINAL_GENERATION_STATUSES:
                raise ValueError(
                    "Completed Campaign is bound to a non-terminal Program generation."
                )
        elif campaign.state != CampaignState.AUTHORIZED:
            raise ValueError(
                "Campaign state is not valid for Program lifecycle verification."
            )

        execution_forbidden = privileged | approval_actors | {evaluator}
        if authorization_events:
            authorization = authorization_events[0]
            if (
                authorization.reason != _PROGRAM_AUTHORIZATION_REASON
                or authorization.payload != {"campaign_id": campaign.campaign_id}
                or authorization.actor_id in execution_forbidden
                or not approvals
                or authorization.created_at < approvals[-1].created_at
            ):
                raise ValueError(
                    "Program generation authorization audit differs from governed evidence."
                )
        if start_events:
            start = start_events[0]
            if (
                start.reason != _PROGRAM_START_REASON
                or start.payload != {"plan_hash": plan.plan_hash}
                or start.actor_id in execution_forbidden
                or not authorization_events
                or start.created_at < authorization_events[0].created_at
            ):
                raise ValueError(
                    "Program generation start audit differs from governed evidence."
                )
        if completion_events:
            completion_event = completion_events[0]
            outcome = generation.outcome
            if outcome is None:
                raise ValueError(
                    "Program completion audit exists without immutable Generation outcome."
                )
            expected_payload = {
                "outcome_hash": outcome.outcome_hash,
                "release_action": outcome.release_action.value,
                "release_package_hash": outcome.release_package_hash,
            }
            if (
                completion_event.reason != _PROGRAM_COMPLETION_REASON
                or completion_event.payload != expected_payload
                or completion_event.actor_id in execution_forbidden
                or not start_events
                or completion_event.created_at != outcome.completed_at
                or completion_event.created_at < start_events[0].created_at
                or generation.updated_at != outcome.completed_at
            ):
                raise ValueError(
                    "Program generation completion audit differs from immutable outcome."
                )
        elif generation.outcome is not None:
            raise ValueError(
                "Program Generation outcome exists without its completion audit event."
            )

        if campaign.state != CampaignState.COMPLETED:
            return
        if len(completion_events) != 1:
            raise ValueError(
                "Completed Campaign lacks one exact Program completion event."
            )
        campaign_completion = events[-1]
        if (
            campaign_completion.payload.get("from_state")
            != CampaignState.AUTHORIZED.value
            or campaign_completion.payload.get("to_state")
            != CampaignState.COMPLETED.value
            or campaign_completion.payload.get("cooldown_until") is not None
            or set(campaign_completion.payload)
            != {"from_state", "to_state", "reason", "cooldown_until"}
        ):
            raise ValueError(
                "Generation Campaign completion audit was substituted."
            )
        program_completion = completion_events[0]
        if campaign_completion.actor_id in execution_forbidden:
            raise ValueError(
                "Generation completion audit violates role separation."
            )
        completion_reason = campaign_completion.payload.get("reason")
        if completion_reason == _NORMAL_COMPLETION_REASON:
            if (
                campaign_completion.actor_id != program_completion.actor_id
                or campaign_completion.created_at != program_completion.created_at
            ):
                raise ValueError(
                    "Normal Campaign completion differs from Program completion."
                )
        elif completion_reason == _RECOVERY_COMPLETION_REASON:
            if campaign_completion.created_at < program_completion.created_at:
                raise ValueError(
                    "Recovered Campaign completion predates Program completion."
                )
        else:
            raise ValueError(
                "Generation Campaign completion reason is not governed."
            )


__all__ = ["RetryHardenedEvolutionProgramController"]
